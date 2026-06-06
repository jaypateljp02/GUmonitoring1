import React, { useState, useEffect, useRef } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView, Animated } from 'react-native';
import { api, API_URL } from '../services/api';
import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';

export default function DashboardScreen({ navigation }) {
  const [telemetry, setTelemetry] = useState(null);
  const [loading, setLoading] = useState(true);
  const SENSOR_ID = 'a4b002884e';

  // Animation value for flashing warning
  const flashAnim = useRef(new Animated.Value(0)).current;

  const fetchTelemetry = async () => {
    try {
      const response = await api.get(`/sensors/device/${SENSOR_ID}/telemetry?days=1`);
      if (response.data && response.data.length > 0) {
        setTelemetry(response.data[0]); // Most recent
      }
    } catch (err) {
      console.log('Error fetching telemetry', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTelemetry();
    const interval = setInterval(fetchTelemetry, 5000); // Poll every 5 seconds
    return () => clearInterval(interval);
  }, []);

  // Flashing effect if temp > 4.0
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
      const response = await api.get(`/sensors/device/${SENSOR_ID}/export`, { responseType: 'blob' });
      const reader = new FileReader();
      reader.onload = async () => {
        const base64data = reader.result.split(',')[1];
        const fileUri = `${FileSystem.documentDirectory}telemetry_${SENSOR_ID}.csv`;
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
      await api.post(`/sensors/device/${SENSOR_ID}/mock`, { mode });
    } catch (e) {
      console.log('Failed to set mock mode', e);
    }
  };

  const warningBackgroundColor = flashAnim.interpolate({
    inputRange: [0, 1],
    outputRange: ['#1F2937', '#7F1D1D'] // Dark gray to deep red
  });

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20 }}>
      <Text style={styles.header}>Fridge 1 Monitor</Text>
      
      <Animated.View style={[styles.card, { backgroundColor: (telemetry && telemetry.temperature > 4.0) ? warningBackgroundColor : '#1F2937' }]}>
        <Text style={styles.cardTitle}>Live Metrics (Sensor: {SENSOR_ID})</Text>
        
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

      <TouchableOpacity style={styles.actionButton} onPress={() => navigation.navigate('Analytics')}>
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
  container: { flex: 1, backgroundColor: '#111827' },
  header: { fontSize: 28, fontWeight: 'bold', color: '#fff', marginBottom: 20 },
  card: { borderRadius: 16, padding: 20, marginBottom: 20, borderWidth: 1, borderColor: '#374151' },
  cardTitle: { color: '#9CA3AF', fontSize: 14, marginBottom: 16, fontWeight: 'bold' },
  row: { flexDirection: 'row', justifyContent: 'space-between' },
  metricBox: { flex: 1 },
  metricLabel: { color: '#9CA3AF', fontSize: 12, marginBottom: 4 },
  metricValue: { color: '#fff', fontSize: 24, fontWeight: 'bold' },
  warningText: { color: '#FECACA', fontWeight: 'bold', marginTop: 16, textAlign: 'center', backgroundColor: '#991B1B', padding: 8, borderRadius: 8 },
  actionButton: { backgroundColor: '#3B82F6', borderRadius: 12, padding: 16, alignItems: 'center', marginBottom: 12 },
  exportButton: { backgroundColor: '#10B981' },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: 'bold' },
  devPanel: { marginTop: 30, padding: 16, backgroundColor: '#1F2937', borderRadius: 16, borderWidth: 1, borderColor: '#374151', borderStyle: 'dashed' },
  devPanelTitle: { color: '#9CA3AF', fontSize: 12, fontWeight: 'bold', marginBottom: 12 },
  devRow: { flexDirection: 'row', justifyContent: 'space-between', gap: 10 },
  devButton: { flex: 1, backgroundColor: '#4B5563', padding: 12, borderRadius: 8, alignItems: 'center' },
  devButtonText: { color: '#fff', fontWeight: 'bold' }
});
