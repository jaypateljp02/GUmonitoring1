import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, ScrollView, Dimensions, ActivityIndicator } from 'react-native';
import { api } from '../services/api';
import { LineChart } from 'react-native-chart-kit';

export default function AnalyticsScreen({ route }) {
  const device = route?.params?.device || { id: 'a4b002884e', name: 'Device 1', icon: '❄️' };
  const SENSOR_ID = device.id;
  const [telemetryLogs, setTelemetryLogs] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await api.get(`/sensors/device/${SENSOR_ID}/telemetry?days=7`);
        // Reverse because backend sorts by descending
        setTelemetryLogs(response.data.reverse());
      } catch (err) {
        console.log('Error fetching analytics', err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [SENSOR_ID]);

  if (loading) {
    return (
      <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
        <ActivityIndicator size="large" color="#3B82F6" />
      </View>
    );
  }

  if (telemetryLogs.length === 0) {
    return (
      <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
        <Text style={styles.errorText}>No data available for the past 7 days.</Text>
      </View>
    );
  }

  // To prevent crashing with too many points, sample maximum 20 points
  const step = Math.max(1, Math.floor(telemetryLogs.length / 20));
  const sampledLogs = telemetryLogs.filter((_, index) => index % step === 0);

  const data = {
    labels: sampledLogs.map(log => {
      const d = new Date(log.timestamp.endsWith('Z') ? log.timestamp : log.timestamp + 'Z');
      return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
    }),
    datasets: [
      {
        data: sampledLogs.map(log => parseFloat(log.temperature)),
        color: (opacity = 1) => `rgba(59, 130, 246, ${opacity})`, // Blue
        strokeWidth: 2
      },
      {
        // 4.0C Max Limit line
        data: sampledLogs.map(() => 4.0),
        color: (opacity = 1) => `rgba(239, 68, 68, ${opacity})`, // Red
        strokeWidth: 1,
        withDots: false,
      }
    ],
    legend: ["Temperature (°C)", "Max Threshold (4.0°C)"]
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20 }}>
      <Text style={styles.header}>{device.icon} {device.name} Analytics</Text>
      
      <View style={styles.chartContainer}>
        <Text style={styles.chartTitle}>7-Day Temperature Trend</Text>
        <LineChart
          data={data}
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
              r: "4",
              strokeWidth: "2",
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

      <View style={styles.infoBox}>
        <Text style={styles.infoText}>
          The red line represents the absolute maximum acceptable temperature (4.0°C) for the cold storage. 
          Temperatures consistently crossing this line will trigger system alarms.
        </Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F3F4F6' },
  header: { fontSize: 24, fontWeight: 'bold', color: '#111827', marginBottom: 20 },
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
  errorText: { color: '#6B7280', fontSize: 16 },
  infoBox: {
    marginTop: 20,
    backgroundColor: '#EFF6FF',
    padding: 16,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#BFDBFE',
  },
  infoText: {
    color: '#1E40AF',
    fontSize: 14,
    lineHeight: 20,
  }
});
