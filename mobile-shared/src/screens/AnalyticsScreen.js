import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, ScrollView, Dimensions, ActivityIndicator, TouchableOpacity, Alert } from 'react-native';
import { api } from '../services/api';
import { LineChart } from 'react-native-chart-kit';
import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';

export default function AnalyticsScreen({ route }) {
  const device = route?.params?.device || { id: 'a4b002884e', name: 'Device 1', icon: '❄️' };
  const SENSOR_ID = device.id;
  const [telemetryLogs, setTelemetryLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  
  // Custom selection states
  const [timeFrame, setTimeFrame] = useState('7D');
  const [intervalMinutes, setIntervalMinutes] = useState(30);
  const [monthlyData, setMonthlyData] = useState([]);
  
  // Dynamic threshold states
  const [tempMin, setTempMin] = useState(null);
  const [tempMax, setTempMax] = useState(null);

  // Fetch thresholds once
  useEffect(() => {
    const fetchThresholds = async () => {
      try {
        const res = await api.get(`/sensors/device/${SENSOR_ID}/sensors`);
        const tempSensor = res.data.find(s => s.type === 'temperature');
        if (tempSensor) {
          setTempMin(tempSensor.min_threshold);
          setTempMax(tempSensor.max_threshold);
        }
      } catch (err) {
        console.log('Error fetching device thresholds', err);
      }
    };
    fetchThresholds();
  }, [SENSOR_ID]);

  // Fetch telemetry logs whenever parameters change
  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        if (timeFrame === 'Monthly') {
          const response = await api.get(`/sensors/device/${SENSOR_ID}/metrics/monthly`);
          setMonthlyData(response.data.daily_metrics || []);
        } else {
          const numDays = parseInt(timeFrame.replace('D', ''));
          const response = await api.get(`/sensors/device/${SENSOR_ID}/telemetry`, {
            params: { days: numDays, interval_minutes: intervalMinutes }
          });
          // Reverse because backend sorts by descending (newest first)
          setTelemetryLogs(response.data.reverse());
        }
      } catch (err) {
        console.log('Error fetching analytics', err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [SENSOR_ID, timeFrame, intervalMinutes]);

  const handleExportCSV = async () => {
    try {
      const exportDays = timeFrame === 'Monthly' ? 30 : parseInt(timeFrame.replace('D', ''));
      const response = await api.get(`/sensors/device/${SENSOR_ID}/export`, {
        params: { days: exportDays, interval_minutes: intervalMinutes }
      });
      const fileUri = `${FileSystem.documentDirectory}telemetry_${SENSOR_ID}_${timeFrame}_${intervalMinutes}m.csv`;
      await FileSystem.writeAsStringAsync(fileUri, response.data, { encoding: FileSystem.EncodingType.UTF8 });
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(fileUri);
      }
    } catch (e) {
      console.log('Export failed', e);
      Alert.alert('Error', 'Failed to export telemetry data');
    }
  };

  // Safe date parsing across iOS, Android, and Web platforms
  const parseDate = (timestampStr) => {
    if (!timestampStr) return new Date();
    const normalized = timestampStr.replace(' ', 'T');
    const parts = normalized.split('T');
    if (parts.length === 2 && !parts[1].includes('Z') && !parts[1].match(/[+-]\d{2}:?\d{2}$/)) {
      return new Date(normalized + 'Z');
    }
    return new Date(normalized);
  };

  // Clean data logs and filter out invalid readings to prevent crashes
  const cleanedLogs = telemetryLogs
    .map(log => {
      const temp = parseFloat(log.temperature);
      return {
        ...log,
        temperature: isNaN(temp) ? 0.0 : temp
      };
    });

  // Sample data points to fit the screen
  const step = Math.max(1, Math.floor(cleanedLogs.length / 20));
  const sampledLogs = cleanedLogs.filter((_, index) => index % step === 0);

  // Spaced-out X-axis time labels to prevent overlap
  let lastLabelTime = null;
  const labels = sampledLogs.map((log, index) => {
    const d = parseDate(log.timestamp);
    if (isNaN(d.getTime())) return "";
    
    // Group dynamically depending on interval: e.g. every hour or every day
    let labelGroup;
    if (timeFrame === '7D' || timeFrame === '30D') {
      labelGroup = d.getDate(); // group by day
    } else {
      labelGroup = d.getHours() * 2 + (d.getMinutes() >= 30 ? 1 : 0); // group by 30 mins
    }
    
    // Label first point, last point, and whenever group changes
    if (index === 0 || index === sampledLogs.length - 1 || lastLabelTime !== labelGroup) {
      lastLabelTime = labelGroup;
      if (timeFrame === '7D' || timeFrame === '30D') {
        return `${d.getMonth() + 1}/${d.getDate()}`;
      }
      return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
    }
    return ""; // Hide label to avoid overlap
  });

  const effectiveMaxThreshold = tempMax !== null ? tempMax : 4.0;

  const chartData = {
    labels: labels.length > 0 ? labels : ["No Data"],
    datasets: [
      {
        data: sampledLogs.length > 0 ? sampledLogs.map(log => log.temperature) : [0],
        color: (opacity = 1) => `rgba(59, 130, 246, ${opacity})`, // Blue
        strokeWidth: 2
      },
      {
        // Max Limit line
        data: sampledLogs.length > 0 ? sampledLogs.map(() => effectiveMaxThreshold) : [effectiveMaxThreshold],
        color: (opacity = 1) => `rgba(239, 68, 68, ${opacity})`, // Red
        strokeWidth: 1.5,
        withDots: false,
      }
    ],
    legend: ["Temp (°C)", `Max Limit (${effectiveMaxThreshold}°C)`]
  };

  // If min threshold is set, add to datasets
  if (tempMin !== null) {
    chartData.datasets.push({
      data: sampledLogs.length > 0 ? sampledLogs.map(() => tempMin) : [tempMin],
      color: (opacity = 1) => `rgba(16, 185, 129, ${opacity})`, // Green
      strokeWidth: 1.5,
      withDots: false,
    });
    chartData.legend.push(`Min Limit (${tempMin}°C)`);
  }

  const timeFrameOptions = [
    { label: '1D', value: '1D' },
    { label: '3D', value: '3D' },
    { label: '7D', value: '7D' },
    { label: 'Monthly', value: 'Monthly' }
  ];

  // Prepare Monthly Data for Chart
  const monthlyLabels = monthlyData.map(d => {
    const date = new Date(d.date);
    return `${date.getMonth()+1}/${date.getDate()}`;
  });
  
  const monthlyChartData = {
    labels: monthlyLabels.length > 0 ? monthlyLabels : ["No Data"],
    datasets: [
      {
        data: monthlyData.length > 0 ? monthlyData.map(d => d.temp_max !== null ? parseFloat(d.temp_max) : 0) : [0],
        color: (opacity = 1) => `rgba(239, 68, 68, ${opacity})`, // Red (Max)
        strokeWidth: 2
      },
      {
        data: monthlyData.length > 0 ? monthlyData.map(d => d.temp_min !== null ? parseFloat(d.temp_min) : 0) : [0],
        color: (opacity = 1) => `rgba(59, 130, 246, ${opacity})`, // Blue (Min)
        strokeWidth: 2
      }
    ],
    legend: ["Max Temp (°C)", "Min Temp (°C)"]
  };

  const intervalOptions = [
    { label: 'Raw', value: 1 },
    { label: '15m', value: 15 },
    { label: '30m', value: 30 },
    { label: '1h', value: 60 },
    { label: '2h', value: 120 }
  ];

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20 }}>
      <Text style={styles.header}>{device.icon} {device.name} Analytics</Text>
      
      {/* Time Frame selector */}
      <Text style={styles.selectorTitle}>Time Frame</Text>
      <View style={styles.selectorRow}>
        {timeFrameOptions.map(option => (
          <TouchableOpacity
            key={option.value}
            style={[styles.selectorButton, timeFrame === option.value && styles.selectorButtonActive]}
            onPress={() => setTimeFrame(option.value)}
          >
            <Text style={[styles.selectorButtonText, timeFrame === option.value && styles.selectorButtonTextActive]}>
              {option.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* Interval selector */}
      <Text style={styles.selectorTitle}>Data Interval</Text>
      <View style={styles.selectorRow}>
        {intervalOptions.map(option => (
          <TouchableOpacity
            key={option.value}
            style={[styles.selectorButton, intervalMinutes === option.value && styles.selectorButtonActive]}
            onPress={() => setIntervalMinutes(option.value)}
          >
            <Text style={[styles.selectorButtonText, intervalMinutes === option.value && styles.selectorButtonTextActive]}>
              {option.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {loading ? (
        <View style={styles.chartLoadingContainer}>
          <ActivityIndicator size="large" color="#3B82F6" />
        </View>
      ) : timeFrame === 'Monthly' ? (
        <View style={styles.chartContainer}>
          <Text style={styles.chartTitle}>Monthly Temperature Extremes</Text>
          {monthlyData.length > 0 ? (
            <LineChart
              data={monthlyChartData}
              width={Dimensions.get('window').width - 40}
              height={220}
              yAxisSuffix="°C"
              yAxisInterval={1}
              chartConfig={{
                backgroundColor: '#FFFFFF',
                backgroundGradientFrom: '#FFFFFF',
                backgroundGradientTo: '#F9FAFB',
                decimalPlaces: 1,
                color: (opacity = 1) => `rgba(17, 24, 39, ${opacity})`,
                labelColor: (opacity = 1) => `rgba(107, 114, 128, ${opacity})`,
                style: { borderRadius: 16 },
                propsForDots: { r: "3", strokeWidth: "1", stroke: "#3B82F6" }
              }}
              bezier
              style={{ marginVertical: 8, borderRadius: 16 }}
            />
          ) : (
            <Text style={styles.errorText}>No data for this month.</Text>
          )}
        </View>
      ) : cleanedLogs.length < 2 ? (
        <View style={styles.errorContainer}>
          <Text style={styles.errorText}>No analytics data available yet.</Text>
          <Text style={[styles.errorSubText, { marginTop: 8 }]}>Need at least 2 temperature readings to render the trend chart.</Text>
        </View>
      ) : (
        <View style={styles.chartContainer}>
          <Text style={styles.chartTitle}>{timeFrame} Temperature Trend</Text>
          <LineChart
            data={chartData}
            width={Dimensions.get('window').width - 40}
            height={220}
            yAxisSuffix="°C"
            yAxisInterval={1}
            chartConfig={{
              backgroundColor: '#FFFFFF',
              backgroundGradientFrom: '#FFFFFF',
              backgroundGradientTo: '#F9FAFB',
              decimalPlaces: 1,
              color: (opacity = 1) => `rgba(17, 24, 39, ${opacity})`,
              labelColor: (opacity = 1) => `rgba(107, 114, 128, ${opacity})`,
              style: {
                borderRadius: 16
              },
              propsForDots: {
                r: "3",
                strokeWidth: "1",
                stroke: "#3B82F6"
              }
            }}
            bezier
            style={{
              marginVertical: 8,
              borderRadius: 16
            }}
          />
        </View>
      )}

      <TouchableOpacity style={styles.exportButton} onPress={handleExportCSV}>
        <Text style={styles.exportButtonText}>Export CSV Audit Log</Text>
      </TouchableOpacity>

      <View style={styles.infoBox}>
        <Text style={styles.infoText}>
          The red line represents the maximum acceptable temperature ({effectiveMaxThreshold}°C) for the cold storage.
          {tempMin !== null && ` The green line represents the minimum acceptable temperature (${tempMin}°C).`}
          Temperatures crossing these bounds will trigger system alarms.
        </Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F3F4F6' },
  header: { fontSize: 24, fontWeight: 'bold', color: '#111827', marginBottom: 16 },
  selectorTitle: { fontSize: 13, fontWeight: 'bold', color: '#6B7280', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 },
  selectorRow: { flexDirection: 'row', gap: 8, marginBottom: 16 },
  selectorButton: { flex: 1, backgroundColor: '#FFFFFF', borderWidth: 1, borderColor: '#E5E7EB', borderRadius: 8, paddingVertical: 8, alignItems: 'center' },
  selectorButtonActive: { backgroundColor: '#3B82F6', borderColor: '#3B82F6' },
  selectorButtonText: { fontSize: 13, fontWeight: '600', color: '#4B5563' },
  selectorButtonTextActive: { color: '#FFFFFF' },
  chartLoadingContainer: { height: 240, justifyContent: 'center', alignItems: 'center', backgroundColor: '#FFFFFF', borderRadius: 16, borderWidth: 1, borderColor: '#E5E7EB' },
  chartContainer: { 
    backgroundColor: '#FFFFFF', 
    borderRadius: 16, 
    padding: 10, 
    borderWidth: 1, 
    borderColor: '#E5E7EB',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.05,
    shadowRadius: 8,
    elevation: 2,
  },
  chartTitle: { color: '#6B7280', fontSize: 14, fontWeight: 'bold', marginBottom: 10, alignSelf: 'flex-start' },
  errorContainer: { height: 240, justifyContent: 'center', alignItems: 'center', backgroundColor: '#FFFFFF', borderRadius: 16, borderWidth: 1, borderColor: '#E5E7EB', padding: 20 },
  errorText: { color: '#6B7280', fontSize: 16, fontWeight: 'bold', textAlign: 'center' },
  errorSubText: { color: '#9CA3AF', fontSize: 13, textAlign: 'center' },
  exportButton: { backgroundColor: '#10B981', borderRadius: 12, padding: 16, alignItems: 'center', marginTop: 20 },
  exportButtonText: { color: '#fff', fontSize: 16, fontWeight: 'bold' },
  infoBox: {
    marginTop: 20,
    backgroundColor: '#EFF6FF',
    padding: 16,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#BFDBFE',
    marginBottom: 30,
  },
  infoText: {
    color: '#1E40AF',
    fontSize: 14,
    lineHeight: 20,
  }
});
