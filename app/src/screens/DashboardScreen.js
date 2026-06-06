import React, { useState, useEffect, useRef } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView, Animated } from 'react-native';
import { api } from '../services/api';
import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';

export default function DashboardScreen({ route, navigation }) {
  const device = route.params.device;
  const [telemetry, setTelemetry] = useState(null);
  const [loading, setLoading] = useState(true);
  const flashAnim = useRef(new Animated.Value(0)).current;

  const fetchTelemetry = async () => {
    try {
      const response = await api.get(`/sensors/device/${device.id}/telemetry?days=1`);
      if (response.data && response.data.length > 0) {
        setTelemetry(response.data[0]);
      }
    } catch (err) {
      console.log('Error fetching telemetry', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTelemetry();
    const interval = setInterval(fetchTelemetry, 5000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (telemetry && telemetry.temperature > 4.0) {
      Animated.loop(
        Animated.sequence([
          Animated.timing(flashAnim, { toValue: 1, duration: 500, useNativeDriver: false }),
          Animated.timing(flashAnim, { toValue: 0, duration: 500, useNativeDriver: false })
        ])
      ).start();
    } else {
      flashAnim.stopAnimation();
      flashAnim.setValue(0);
    }
  }, [telemetry]);

  const handleExportCSV = async () => {
    try {
      const response = await api.get(`/sensors/device/${device.id}/export`, { responseType: 'blob' });
      const reader = new FileReader();
      reader.onload = async () => {
        const base64data = reader.result.split(',')[1];
        const fileUri = `${FileSystem.documentDirectory}telemetry_${device.id}.csv`;
        await FileSystem.writeAsStringAsync(fileUri, base64data, { encoding: FileSystem.EncodingType.Base64 });
        if (await Sharing.isAvailableAsync()) {
          await Sharing.shareAsync(fileUri);
        }
      };
      reader.readAsDataURL(response.data);
    } catch (e) {
      console.log('Export failed', e);
    }
  };

  const setMockMode = async (mode) => {
    try {
      await api.post(`/sensors/device/${device.id}/mock`, { mode });
      fetchTelemetry();
    } catch (e) {
      console.log('Failed to set mock mode', e);
    }
  };

  const warningBackgroundColor = flashAnim.interpolate({
    inputRange: [0, 1],
    outputRange: ['#FFFFFF', '#FEE2E2']
  });

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20 }}>
      <Text style={styles.header}>{device.icon} {device.name} Monitor</Text>
      
      <Animated.View style={[styles.card, { backgroundColor: (telemetry && telemetry.temperature > 4.0) ? warningBackgroundColor : '#FFFFFF' }]}>
        <Text style={styles.cardTitle}>Live Metrics (Sensor: {device.id})</Text>
        
        <View style={styles.row}>
          <View style={styles.metricBox}>
            <Text style={styles.metricLabel}>Temperature</Text>
            <Text style={styles.metricValue}>
              {telemetry ? `${telemetry.temperature}°C` : '--'}
            </Text>
          </View>
          <View style={styles.metricBox}>
            <Text style={styles.metricLabel}>Humidity</Text>
            <Text style={styles.metricValue}>
              {telemetry ? `${telemetry.humidity}%` : '--'}
            </Text>
          </View>
        </View>

        <View style={[styles.row, { marginTop: 16 }]}>
          <View style={styles.metricBox}>
            <Text style={styles.metricLabel}>Battery</Text>
            <Text style={styles.metricValue}>
              {telemetry ? `${telemetry.battery_level}%` : '--'}
            </Text>
          </View>
          <View style={styles.metricBox}>
            <Text style={styles.metricLabel}>Last Updated</Text>
            <Text style={[styles.metricValue, { fontSize: 14 }]}>
              {telemetry ? new Date(telemetry.timestamp.endsWith('Z') ? telemetry.timestamp : telemetry.timestamp + 'Z').toLocaleTimeString() : '--'}
            </Text>
          </View>
        </View>

        {telemetry && telemetry.temperature > 4.0 && (
          <Text style={styles.warningText}>⚠️ TEMPERATURE ALERT: EXCEEDS 4.0°C</Text>
        )}
      </Animated.View>

      <TouchableOpacity style={styles.actionButton} onPress={() => navigation.navigate('Analytics', { device })}>
        <Text style={styles.buttonText}>View 7-Day Analytics</Text>
      </TouchableOpacity>
      
      <TouchableOpacity style={[styles.actionButton, styles.exportButton]} onPress={handleExportCSV}>
        <Text style={styles.buttonText}>Export CSV Audit Log</Text>
      </TouchableOpacity>

      <View style={styles.devPanel}>
        <Text style={styles.devPanelTitle}>Developer Simulator Panel</Text>
        <View style={styles.devRow}>
          <TouchableOpacity style={styles.devButton} onPress={() => setMockMode('normal')}>
            <Text style={styles.devButtonText}>Normal</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[styles.devButton, {backgroundColor: '#3B82F6'}]} onPress={() => setMockMode('ice')}>
            <Text style={styles.devButtonText}>Ice (-3°C)</Text>
          </TouchableOpacity>
        </View>
        <View style={[styles.devRow, {marginTop: 10}]}>
          <TouchableOpacity style={[styles.devButton, {backgroundColor: '#EF4444'}]} onPress={() => setMockMode('warm')}>
            <Text style={styles.devButtonText}>Warm (8°C)</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[styles.devButton, {backgroundColor: '#F59E0B'}]} onPress={() => setMockMode('failover')}>
            <Text style={styles.devButtonText}>Failover</Text>
          </TouchableOpacity>
        </View>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F3F4F6' },
  header: { fontSize: 28, fontWeight: 'bold', color: '#111827', marginBottom: 20 },
  card: {
    borderRadius: 16, padding: 20, marginBottom: 20,
    borderWidth: 1, borderColor: '#E5E7EB',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.05,
    shadowRadius: 8,
    elevation: 2,
  },
  cardTitle: { color: '#6B7280', fontSize: 14, marginBottom: 16, fontWeight: 'bold' },
  row: { flexDirection: 'row', justifyContent: 'space-between' },
  metricBox: { flex: 1 },
  metricLabel: { color: '#6B7280', fontSize: 12, marginBottom: 4 },
  metricValue: { color: '#111827', fontSize: 24, fontWeight: 'bold' },
  warningText: { color: '#991B1B', fontWeight: 'bold', marginTop: 16, textAlign: 'center', backgroundColor: '#FEE2E2', padding: 8, borderRadius: 8 },
  actionButton: { backgroundColor: '#3B82F6', borderRadius: 12, padding: 16, alignItems: 'center', marginBottom: 12 },
  exportButton: { backgroundColor: '#10B981' },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: 'bold' },
  devPanel: { marginTop: 30, padding: 16, backgroundColor: '#FFFFFF', borderRadius: 16, borderWidth: 1, borderColor: '#D1D5DB', borderStyle: 'dashed' },
  devPanelTitle: { color: '#6B7280', fontSize: 12, fontWeight: 'bold', marginBottom: 12 },
  devRow: { flexDirection: 'row', justifyContent: 'space-between', gap: 10 },
  devButton: { flex: 1, backgroundColor: '#6B7280', padding: 12, borderRadius: 8, alignItems: 'center' },
  devButtonText: { color: '#fff', fontWeight: 'bold' }
});
