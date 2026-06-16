import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, ScrollView, Dimensions, ActivityIndicator, TouchableOpacity, Alert, useWindowDimensions } from 'react-native';
import { api } from '../services/api';
import { LineChart } from 'react-native-chart-kit';
import RNFS from 'react-native-fs';
import Share from 'react-native-share';

export default function AnalyticsScreen({ route }) {
  const { width } = useWindowDimensions();
  const device = route?.params?.device || { id: 'a4b002884e', name: 'Device 1', icon: '❄️' };
  const SENSOR_ID = device.id;
  const [telemetryLogs, setTelemetryLogs] = useState([]);
  const [offlinePeriods, setOfflinePeriods] = useState([]);
  const [loading, setLoading] = useState(true);
  
  // Custom selection states
  const [timeFrame, setTimeFrame] = useState('7D');
  const [intervalMinutes, setIntervalMinutes] = useState(1);
  const [monthlyData, setMonthlyData] = useState([]);
  
  // Dynamic threshold states
  const [tempMin, setTempMin] = useState(null);
  const [tempMax, setTempMax] = useState(null);
  const [humMin, setHumMin] = useState(null);
  const [humMax, setHumMax] = useState(null);
  
  // Historical alerts states
  const [alertLogs, setAlertLogs] = useState([]);
  const [deviceSensors, setDeviceSensors] = useState([]);

  // Collapse states for logs
  const [isOfflineCollapsed, setIsOfflineCollapsed] = useState(true);
  const [isAlertsCollapsed, setIsAlertsCollapsed] = useState(true);

  // Fetch thresholds once
  useEffect(() => {
    const fetchThresholds = async () => {
      try {
        const res = await api.get(`/sensors/device/${SENSOR_ID}/sensors`);
        setDeviceSensors(res.data || []);
        const tempSensor = res.data.find(s => s.type === 'temperature');
        if (tempSensor) {
          setTempMin(tempSensor.min_threshold);
          setTempMax(tempSensor.max_threshold);
        }
        const humSensor = res.data.find(s => s.type === 'humidity');
        if (humSensor) {
          setHumMin(humSensor.min_threshold);
          setHumMax(humSensor.max_threshold);
        }
      } catch (err) {
        console.log('Error fetching device thresholds', err);
      }
    };
    fetchThresholds();
  }, [SENSOR_ID]);

  // Auto-adjust interval when timeframe changes
  useEffect(() => {
    if (timeFrame === '1D') {
      setIntervalMinutes(1);
    } else if (timeFrame === '3D') {
      setIntervalMinutes(30);
    } else if (timeFrame === '7D') {
      setIntervalMinutes(60);
    } else if (timeFrame === '30D') {
      setIntervalMinutes(120);
    }
  }, [timeFrame]);

  // Fetch telemetry logs whenever parameters change
  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        if (timeFrame === 'Monthly' || timeFrame === '30D') {
          const isMonthly = timeFrame === 'Monthly';
          const endpoint = isMonthly ? `/sensors/device/${SENSOR_ID}/metrics/monthly` : `/sensors/device/${SENSOR_ID}/metrics/rolling?days=30`;
          const response = await api.get(endpoint);
          setMonthlyData(response.data.daily_metrics || []);
          setOfflinePeriods([]);
        } else {
          const numDays = parseInt(timeFrame.replace('D', ''));
          const response = await api.get(`/sensors/device/${SENSOR_ID}/telemetry`, {
            params: { days: numDays, interval_minutes: intervalMinutes }
          });
          const telemetryData = response.data.telemetry || [];
          setTelemetryLogs(telemetryData.reverse());
          setOfflinePeriods(response.data.offline_periods || []);
        }
      } catch (err) {
        console.log('Error fetching analytics', err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [SENSOR_ID, timeFrame, intervalMinutes]);

  // Fetch alert logs for all logical sensors under this device
  useEffect(() => {
    const fetchAlertLogs = async () => {
      if (deviceSensors.length === 0) return;
      try {
        let allAlerts = [];
        for (const s of deviceSensors) {
          const res = await api.get(`/alerts?sensor_id=${s.id}`);
          if (res.data) {
            allAlerts.push(...res.data);
          }
        }
        allAlerts.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
        setAlertLogs(allAlerts.slice(0, 10)); // keep last 10 entries
      } catch (err) {
        console.log('Error fetching alerts', err);
      }
    };
    fetchAlertLogs();
  }, [deviceSensors]);

  const handleExportCSV = async () => {
    try {
      const exportDays = timeFrame === 'Monthly' ? 30 : parseInt(timeFrame.replace('D', ''));
      const response = await api.get(`/sensors/device/${SENSOR_ID}/export`, {
        params: { days: exportDays, interval_minutes: intervalMinutes },
        responseType: 'text'
      });
      const fileUri = `${RNFS.DocumentDirectoryPath}/telemetry_${SENSOR_ID}_${timeFrame}_${intervalMinutes}m.csv`;
      await RNFS.writeFile(fileUri, response.data, 'utf8');
      await Share.open({
        url: `file://${fileUri}`,
        type: 'text/csv',
      });
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

  // Format YYYY-MM-DD date into a premium readable date string
  const formatDate = (dateStr) => {
    if (!dateStr) return "";
    const parts = dateStr.split('-');
    if (parts.length === 3) {
      const year = parts[0];
      const monthIndex = parseInt(parts[1], 10) - 1;
      const day = parseInt(parts[2], 10);
      const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
      const monthName = monthNames[monthIndex] || parts[1];
      return `${monthName} ${day}, ${year}`;
    }
    return dateStr;
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

  // Peak-preserving sampling logic to ensure sudden spikes/drops are never lost in aggregation
  let sampledLogs = [];
  if (cleanedLogs.length <= 300) {
    sampledLogs = cleanedLogs;
  } else {
    const step = Math.floor(cleanedLogs.length / 150); // group into 150 buckets
    for (let i = 0; i < cleanedLogs.length; i += step) {
      const chunk = cleanedLogs.slice(i, i + step);
      if (chunk.length === 0) continue;
      
      let minLog = chunk[0];
      let maxLog = chunk[0];
      for (const log of chunk) {
        if (log.temperature < minLog.temperature) minLog = log;
        if (log.temperature > maxLog.temperature) maxLog = log;
      }
      
      if (minLog.timestamp === maxLog.timestamp) {
        sampledLogs.push(minLog);
      } else {
        const sorted = [minLog, maxLog].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
        sampledLogs.push(...sorted);
      }
    }
  }

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
    { label: '30D', value: '30D' },
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

  // --- Humidity Trend Chart Data ---
  const cleanedHumLogs = telemetryLogs
    .map(log => {
      const humVal = parseFloat(log.humidity);
      return {
        ...log,
        humidity: isNaN(humVal) ? 0.0 : humVal
      };
    });

  // Peak-preserving sampling logic for humidity
  let sampledHumLogs = [];
  if (cleanedHumLogs.length <= 300) {
    sampledHumLogs = cleanedHumLogs;
  } else {
    const step = Math.floor(cleanedHumLogs.length / 150);
    for (let i = 0; i < cleanedHumLogs.length; i += step) {
      const chunk = cleanedHumLogs.slice(i, i + step);
      if (chunk.length === 0) continue;
      let minLog = chunk[0];
      let maxLog = chunk[0];
      for (const log of chunk) {
        if (log.humidity < minLog.humidity) minLog = log;
        if (log.humidity > maxLog.humidity) maxLog = log;
      }
      if (minLog.timestamp === maxLog.timestamp) {
        sampledHumLogs.push(minLog);
      } else {
        const sorted = [minLog, maxLog].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
        sampledHumLogs.push(...sorted);
      }
    }
  }

  const humChartData = {
    labels: labels.length > 0 ? labels : ["No Data"],
    datasets: [
      {
        data: sampledHumLogs.length > 0 ? sampledHumLogs.map(log => log.humidity) : [0],
        color: (opacity = 1) => `rgba(139, 92, 246, ${opacity})`, // Purple
        strokeWidth: 2
      }
    ],
    legend: ["Humidity (%)"]
  };

  if (humMax !== null) {
    humChartData.datasets.push({
      data: sampledHumLogs.length > 0 ? sampledHumLogs.map(() => humMax) : [humMax],
      color: (opacity = 1) => `rgba(236, 72, 153, ${opacity})`, // Pink
      strokeWidth: 1.5,
      withDots: false,
    });
    humChartData.legend.push(`Max Limit (${humMax}%)`);
  }

  if (humMin !== null) {
    humChartData.datasets.push({
      data: sampledHumLogs.length > 0 ? sampledHumLogs.map(() => humMin) : [humMin],
      color: (opacity = 1) => `rgba(16, 185, 129, ${opacity})`, // Green
      strokeWidth: 1.5,
      withDots: false,
    });
    humChartData.legend.push(`Min Limit (${humMin}%)`);
  }

  const monthlyHumChartData = {
    labels: monthlyLabels.length > 0 ? monthlyLabels : ["No Data"],
    datasets: [
      {
        data: monthlyData.length > 0 ? monthlyData.map(d => d.hum_max !== null ? parseFloat(d.hum_max) : 0) : [0],
        color: (opacity = 1) => `rgba(236, 72, 153, ${opacity})`, // Pink (Max)
        strokeWidth: 2
      },
      {
        data: monthlyData.length > 0 ? monthlyData.map(d => d.hum_min !== null ? parseFloat(d.hum_min) : 0) : [0],
        color: (opacity = 1) => `rgba(139, 92, 246, ${opacity})`, // Purple (Min)
        strokeWidth: 2
      }
    ],
    legend: ["Max Hum (%)", "Min Hum (%)"]
  };

  const intervalOptions = [
    { label: 'Raw', value: 1 },
    { label: '15m', value: 15 },
    { label: '30m', value: 30 },
    { label: '1h', value: 60 },
    { label: '2h', value: 120 }
  ];

  const chartConfigLight = {
    backgroundColor: '#FFFFFF',
    backgroundGradientFrom: '#FFFFFF',
    backgroundGradientTo: '#F9FAFB',
    decimalPlaces: 1,
    color: (opacity = 1) => `rgba(17, 24, 39, ${opacity})`,
    labelColor: (opacity = 1) => `rgba(107, 114, 128, ${opacity})`,
    style: { borderRadius: 16 },
    propsForDots: { r: "3", strokeWidth: "1", stroke: "#3B82F6" }
  };

  const latestTelemetry = telemetryLogs[telemetryLogs.length - 1] || null;
  const latestTimeStr = latestTelemetry ? latestTelemetry.timestamp : null;
  const latestTime = latestTimeStr ? parseDate(latestTimeStr) : null;
  const isOnline = latestTime ? (new Date() - latestTime) < 2 * 60 * 1000 : false;
  const isOffline = latestTelemetry && !isOnline;

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20 }}>
      {/* Top Header Row with battery & time in the right corner */}
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 10, marginBottom: 20, flexWrap: 'wrap', gap: 10 }}>
        <Text style={[styles.header, { marginTop: 0, marginBottom: 0, flex: 1, minWidth: 150 }]} numberOfLines={1} ellipsizeMode="tail">
          {device.icon} {device.name} Analytics
        </Text>
        <View style={{ flexDirection: 'row', gap: 6, flexShrink: 0 }}>
          <View style={[styles.topBadge, { backgroundColor: isOffline ? '#E5E7EB' : '#E6F4EA', borderColor: isOffline ? '#D1D5DB' : '#C2E7C9' }]}>
            <Text style={{ color: isOffline ? '#5E5E5E' : '#137333', fontSize: 11, fontWeight: 'bold' }}>
              🔋 {latestTelemetry && latestTelemetry.battery_level !== undefined && latestTelemetry.battery_level !== null ? `${parseInt(latestTelemetry.battery_level)}%` : '--'}
            </Text>
          </View>
          <View style={[styles.topBadge, { backgroundColor: isOffline ? '#E5E7EB' : '#E8F0FE', borderColor: isOffline ? '#D1D5DB' : '#D2E3FC' }]}>
            <Text style={{ color: isOffline ? '#5E5E5E' : '#1A73E8', fontSize: 11, fontWeight: 'bold' }}>
              🕒 {latestTelemetry ? new Date(latestTelemetry.timestamp.endsWith('Z') ? latestTelemetry.timestamp : latestTelemetry.timestamp + 'Z').toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '--'}
            </Text>
          </View>
        </View>
      </View>
      
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

      {/* Repositioned Chart Guide Info Box */}
      <View style={[styles.infoBox, { marginBottom: 16, marginTop: 10 }]}>
        <Text style={styles.infoText}>
          📊 Chart Guide:{"\n"}
          🔴 Red line: Max Limit ({effectiveMaxThreshold}°C) (dangerous if crossed).{"\n"}
          {tempMin !== null ? `🟢 Green line: Min Limit (${tempMin}°C) (dangerous if crossed).\n` : ''}
          🔵 Blue line: Actual recorded temperatures.
        </Text>
      </View>

      {loading ? (
        <View style={styles.chartLoadingContainer}>
          <ActivityIndicator size="large" color="#3B82F6" style={{ marginBottom: 12 }} />
          <Text style={{ color: '#4B5563', fontSize: 14, fontWeight: '600', textAlign: 'center' }}>
            ⚡ Fetching historical readings...
          </Text>
        </View>
      ) : (
        <View>
          {(timeFrame === 'Monthly' || timeFrame === '30D') ? (
            <View>
              {/* Temperature Extremes Chart */}
              <View style={styles.chartContainer}>
                <Text style={styles.chartTitle}>{timeFrame === 'Monthly' ? 'Monthly' : '30-Day'} Temperature Extremes</Text>
                {monthlyData.length > 0 ? (
                  <ScrollView horizontal={true} showsHorizontalScrollIndicator={true}>
                    <LineChart
                      data={monthlyChartData}
                      width={Math.max(300, width - 40, monthlyData.length * 45)}
                      height={340}
                      yAxisSuffix="°C"
                      yAxisInterval={1}
                      chartConfig={chartConfigLight}
                      bezier
                      style={{ marginVertical: 8, borderRadius: 16 }}
                    />
                  </ScrollView>
                ) : (
                  <Text style={styles.errorText}>No temperature extremes data available for this range.</Text>
                )}
              </View>

              {/* Humidity Extremes Chart */}
              <View style={[styles.chartContainer, { marginTop: 20 }]}>
                <Text style={styles.chartTitle}>{timeFrame === 'Monthly' ? 'Monthly' : '30-Day'} Humidity Extremes</Text>
                {monthlyData.length > 0 ? (
                  <ScrollView horizontal={true} showsHorizontalScrollIndicator={true}>
                    <LineChart
                      data={monthlyHumChartData}
                      width={Math.max(300, width - 40, monthlyData.length * 45)}
                      height={340}
                      yAxisSuffix="%"
                      yAxisInterval={1}
                      chartConfig={{
                        ...chartConfigLight,
                        propsForDots: { r: "3", strokeWidth: "1", stroke: "#8B5CF6" }
                      }}
                      bezier
                      style={{ marginVertical: 8, borderRadius: 16 }}
                    />
                  </ScrollView>
                ) : (
                  <Text style={styles.errorText}>No humidity extremes data available for this range.</Text>
                )}
              </View>

              {monthlyData.length > 0 && (
                <View style={styles.extremesTableContainer}>
                  <Text style={styles.extremesTableTitle}>Daily Temperature Log</Text>
                  <View style={styles.tableHeader}>
                    <Text style={[styles.tableHeaderCell, { flex: 2 }]}>Date</Text>
                    <Text style={[styles.tableHeaderCell, { flex: 1.2, textAlign: 'right', color: '#3B82F6' }]}>Min Temp</Text>
                    <Text style={[styles.tableHeaderCell, { flex: 1.2, textAlign: 'right', color: '#EF4444' }]}>Max Temp</Text>
                  </View>
                  {monthlyData.slice().reverse().map((item, index) => {
                    const minVal = parseFloat(item.temp_min);
                    const maxVal = parseFloat(item.temp_max);
                    return (
                      <View key={item.date || index} style={styles.tableRow}>
                        <Text style={[styles.tableCell, { flex: 2, fontWeight: '600' }]}>{formatDate(item.date)}</Text>
                        <Text style={[styles.tableCell, { flex: 1.2, textAlign: 'right', color: '#2563EB', fontWeight: 'bold' }]}>
                          {!isNaN(minVal) ? `${minVal.toFixed(1)}°C` : 'N/A'}
                        </Text>
                        <Text style={[styles.tableCell, { flex: 1.2, textAlign: 'right', color: '#DC2626', fontWeight: 'bold' }]}>
                          {!isNaN(maxVal) ? `${maxVal.toFixed(1)}°C` : 'N/A'}
                        </Text>
                      </View>
                    );
                  })}
                </View>
              )}
            </View>
          ) : (
            <View>
              {cleanedLogs.length < 2 ? (
                <View style={styles.errorContainer}>
                  <Text style={styles.errorText}>No temperature trend data available yet.</Text>
                </View>
              ) : (
                <View style={styles.chartContainer}>
                  <Text style={styles.chartTitle}>{timeFrame} Temperature Trend</Text>
                  <ScrollView horizontal={true} showsHorizontalScrollIndicator={true}>
                    <LineChart
                      data={chartData}
                      width={Math.max(300, width - 40, sampledLogs.length * 40)}
                      height={340}
                      yAxisSuffix="°C"
                      yAxisInterval={1}
                      chartConfig={chartConfigLight}
                      bezier
                      style={{
                        marginVertical: 8,
                        borderRadius: 16
                      }}
                    />
                  </ScrollView>
                </View>
              )}

              {cleanedHumLogs.length < 2 ? (
                <View style={[styles.errorContainer, { marginTop: 20 }]}>
                  <Text style={styles.errorText}>No humidity trend data available yet.</Text>
                </View>
              ) : (
                <View style={[styles.chartContainer, { marginTop: 20 }]}>
                  <Text style={styles.chartTitle}>{timeFrame} Humidity Trend</Text>
                  <ScrollView horizontal={true} showsHorizontalScrollIndicator={true}>
                    <LineChart
                      data={humChartData}
                      width={Math.max(300, width - 40, sampledHumLogs.length * 40)}
                      height={340}
                      yAxisSuffix="%"
                      yAxisInterval={1}
                      chartConfig={{
                        ...chartConfigLight,
                        propsForDots: { r: "3", strokeWidth: "1", stroke: "#8B5CF6" }
                      }}
                      bezier
                      style={{
                        marginVertical: 8,
                        borderRadius: 16
                      }}
                    />
                  </ScrollView>
                </View>
              )}
            </View>
          )}

          {/* Collapsible Offline History Log */}
          {offlinePeriods && offlinePeriods.length > 0 && (
            <View style={styles.offlineContainer}>
              <TouchableOpacity 
                style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}
                onPress={() => setIsOfflineCollapsed(!isOfflineCollapsed)}
                activeOpacity={0.7}
              >
                <Text style={[styles.offlineTitle, { marginBottom: 0 }]}>⚠️ Offline History Log</Text>
                <Text style={{ fontSize: 13, fontWeight: '800', color: '#EF4444' }}>
                  {isOfflineCollapsed ? 'Expand ▽' : 'Collapse ▲'}
                </Text>
              </TouchableOpacity>
              <Text style={{ fontSize: 12, color: '#6B7280', marginTop: 4, marginBottom: isOfflineCollapsed ? 0 : 12 }}>
                Periods where the sensor stopped sending telemetry to the gateway (offline for &gt;2 mins).
              </Text>
              {!isOfflineCollapsed && offlinePeriods.map((period, index) => (
                <View key={index} style={styles.offlineRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.offlineMsg}>
                      Offline: {period.start} to {period.end}
                    </Text>
                  </View>
                  <View style={styles.durationBadge}>
                    <Text style={styles.durationText}>{period.duration_minutes} mins</Text>
                  </View>
                </View>
              ))}
            </View>
          )}

          <TouchableOpacity style={styles.exportButton} onPress={handleExportCSV} activeOpacity={0.8}>
            <Text style={styles.exportButtonText}>📥 Export CSV Audit Log</Text>
          </TouchableOpacity>

          {/* Collapsible Outage & Alert History Log */}
          <View style={styles.historyContainer}>
            <TouchableOpacity 
              style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}
              onPress={() => setIsAlertsCollapsed(!isAlertsCollapsed)}
              activeOpacity={0.7}
            >
              <Text style={[styles.historyTitle, { marginBottom: 0 }]}>📜 Outage & Alert Logs</Text>
              <Text style={{ fontSize: 13, fontWeight: '800', color: '#2563EB' }}>
                {isAlertsCollapsed ? 'Expand ▽' : 'Collapse ▲'}
              </Text>
            </TouchableOpacity>
            {!isAlertsCollapsed && (
              <View style={{ marginTop: 16 }}>
                {alertLogs.length > 0 ? (
                  alertLogs.map(alert => (
                    <View key={alert.id} style={[styles.alertRow, alert.resolved ? styles.alertResolved : styles.alertActive]}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.alertMsg}>{alert.message}</Text>
                        <Text style={styles.alertTime}>
                          ⏰ {new Date(alert.created_at.endsWith('Z') ? alert.created_at : alert.created_at + 'Z').toLocaleString()}
                        </Text>
                      </View>
                      <View style={[styles.statusBadge, alert.resolved ? styles.statusBadgeResolved : styles.statusBadgeActive]}>
                        <Text style={styles.statusText}>{alert.resolved ? 'RESOLVED' : 'ACTIVE'}</Text>
                      </View>
                    </View>
                  ))
                ) : (
                  <Text style={styles.noAlertsText}>No historical alerts or offline events found.</Text>
                )}
              </View>
            )}
          </View>
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F3F4F6' },
  topBadge: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 10,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.05)',
  },
  header: { fontSize: 24, fontWeight: 'bold', color: '#111827', marginBottom: 20 },
  selectorTitle: { fontSize: 11, fontWeight: '800', color: '#6B7280', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 },
  selectorRow: { flexDirection: 'row', gap: 8, marginBottom: 20 },
  selectorButton: { flex: 1, backgroundColor: '#FFFFFF', borderWidth: 1, borderColor: '#E5E7EB', borderRadius: 10, paddingVertical: 10, alignItems: 'center' },
  selectorButtonActive: { backgroundColor: '#3B82F6', borderColor: '#3B82F6' },
  selectorButtonText: { fontSize: 13, fontWeight: '700', color: '#4B5563' },
  selectorButtonTextActive: { color: '#FFFFFF' },
  chartLoadingContainer: { height: 240, justifyContent: 'center', alignItems: 'center', backgroundColor: '#FFFFFF', borderRadius: 24, borderWidth: 1, borderColor: '#E5E7EB' },
  chartContainer: { 
    backgroundColor: '#FFFFFF', 
    borderRadius: 24, 
    padding: 12, 
    borderWidth: 1, 
    borderColor: '#E5E7EB',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.05,
    shadowRadius: 10,
    elevation: 2,
  },
  chartTitle: { color: '#6B7280', fontSize: 12, fontWeight: '800', marginBottom: 14, alignSelf: 'flex-start', letterSpacing: 0.5 },
  errorContainer: { height: 240, justifyContent: 'center', alignItems: 'center', backgroundColor: '#FFFFFF', borderRadius: 24, borderWidth: 1, borderColor: '#E5E7EB', padding: 20 },
  errorText: { color: '#6B7280', fontSize: 16, fontWeight: 'bold', textAlign: 'center' },
  errorSubText: { color: '#9CA3AF', fontSize: 13, textAlign: 'center' },
  exportButton: { backgroundColor: '#10B981', borderRadius: 14, padding: 16, alignItems: 'center', marginTop: 24 },
  exportButtonText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  infoBox: {
    marginTop: 24,
    backgroundColor: '#EFF6FF',
    padding: 18,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: '#BFDBFE',
    marginBottom: 40,
  },
  infoText: {
    color: '#1E40AF',
    fontSize: 14,
    lineHeight: 22,
  },
  historyContainer: {
    marginTop: 12,
    backgroundColor: '#FFFFFF',
    borderRadius: 24,
    padding: 20,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    marginBottom: 40,
  },
  historyTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: '#111827',
    marginBottom: 16,
  },
  alertRow: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 14,
    borderRadius: 14,
    marginBottom: 10,
    borderWidth: 1,
  },
  alertActive: {
    backgroundColor: '#FEF2F2',
    borderColor: '#FCA5A5',
  },
  alertResolved: {
    backgroundColor: '#F0FDF4',
    borderColor: '#86EFAC',
  },
  alertMsg: {
    fontSize: 13,
    fontWeight: '600',
    color: '#1F2937',
    lineHeight: 18,
  },
  alertTime: {
    fontSize: 11,
    color: '#6B7280',
    marginTop: 6,
  },
  statusBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
    marginLeft: 10,
  },
  statusBadgeActive: {
    backgroundColor: '#EF4444',
  },
  statusBadgeResolved: {
    backgroundColor: '#10B981',
  },
  statusText: {
    fontSize: 10,
    fontWeight: '800',
    color: '#FFFFFF',
  },
  noAlertsText: {
    color: '#9CA3AF',
    fontSize: 13,
    textAlign: 'center',
    paddingVertical: 12,
  },
  extremesTableContainer: {
    marginTop: 20,
    backgroundColor: '#FFFFFF',
    borderRadius: 20,
    padding: 16,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    width: '100%',
  },
  extremesTableTitle: {
    fontSize: 15,
    fontWeight: '800',
    color: '#111827',
    marginBottom: 12,
  },
  tableHeader: {
    flexDirection: 'row',
    borderBottomWidth: 1,
    borderBottomColor: '#E5E7EB',
    paddingBottom: 8,
    marginBottom: 8,
  },
  tableHeaderCell: {
    fontSize: 12,
    fontWeight: '800',
    color: '#6B7280',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  tableRow: {
    flexDirection: 'row',
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#F3F4F6',
    alignItems: 'center',
  },
  tableCell: {
    fontSize: 14,
    color: '#374151',
  },
  offlineContainer: {
    marginTop: 20,
    backgroundColor: '#FFFFFF',
    borderRadius: 24,
    padding: 20,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.05,
    shadowRadius: 10,
    elevation: 2,
  },
  offlineTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: '#EF4444',
    marginBottom: 16,
  },
  offlineRow: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 14,
    borderRadius: 14,
    marginBottom: 10,
    borderWidth: 1,
    backgroundColor: '#FEF2F2',
    borderColor: '#FCA5A5',
  },
  offlineMsg: {
    fontSize: 13,
    fontWeight: '600',
    color: '#991B1B',
    lineHeight: 18,
  },
  durationBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
    backgroundColor: '#EF4444',
    marginLeft: 10,
  },
  durationText: {
    fontSize: 10,
    fontWeight: '800',
    color: '#FFFFFF',
  },
});
