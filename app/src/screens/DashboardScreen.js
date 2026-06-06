import React, { useState, useEffect, useRef } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView, Animated } from 'react-native';
import { api, API_URL } from '../services/api';
import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';

const DEVICES = [
  { id: 'a4b002884e', name: 'Device 1',      icon: '❄️' },
  { id: 'a4b002898f', name: 'Miso Room',      icon: '🍶' },
  { id: 'a4b0028991', name: 'Vinegar Room',   icon: '🫗' },
];

function DeviceCard({ device, navigation }) {
  const [telemetry, setTelemetry] = useState(null);
  const flashAnim = useRef(new Animated.Value(0)).current;

  const fetchTelemetry = async () => {
    try {
      const response = await api.get(`/sensors/device/${device.id}/telemetry?days=1`);
      if (response.data && response.data.length > 0) {
        setTelemetry(response.data[0]);
      }
    } catch (err) {
      console.log('Error fetching telemetry for', device.name, err);
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

  const warningBackgroundColor = flashAnim.interpolate({
    inputRange: [0, 1],
    outputRange: ['#1F2937', '#7F1D1D']
  });

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

  return (
    <Animated.View style={[styles.card, { backgroundColor: (telemetry && telemetry.temperature > 4.0) ? warningBackgroundColor : '#1F2937' }]}>
      <Text style={styles.cardTitle}>{device.icon} {device.name} — Live</Text>
      <Text style={styles.sensorId}>Sensor: {device.id}</Text>
      
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

      <TouchableOpacity style={[styles.exportBtn]} onPress={handleExportCSV}>
        <Text style={styles.exportBtnText}>📥 Export CSV</Text>
      </TouchableOpacity>
    </Animated.View>
  );
}

export default function DashboardScreen({ navigation }) {
  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20 }}>
      <Text style={styles.header}>🏭 Ground Up Monitor</Text>
      <Text style={styles.subheader}>{DEVICES.length} devices · Threshold 4.0°C</Text>
      
      {DEVICES.map(device => (
        <DeviceCard key={device.id} device={device} navigation={navigation} />
      ))}

      <TouchableOpacity style={styles.actionButton} onPress={() => navigation.navigate('Analytics')}>
        <Text style={styles.buttonText}>📊 View 7-Day Analytics</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#111827' },
  header: { fontSize: 28, fontWeight: 'bold', color: '#fff', marginBottom: 4 },
  subheader: { fontSize: 14, color: '#9CA3AF', marginBottom: 20 },
  card: { borderRadius: 16, padding: 20, marginBottom: 16, borderWidth: 1, borderColor: '#374151' },
  cardTitle: { color: '#fff', fontSize: 18, fontWeight: 'bold', marginBottom: 2 },
  sensorId: { color: '#60A5FA', fontSize: 11, fontFamily: 'monospace', marginBottom: 16 },
  row: { flexDirection: 'row', justifyContent: 'space-between' },
  metricBox: { flex: 1 },
  metricLabel: { color: '#9CA3AF', fontSize: 12, marginBottom: 4 },
  metricValue: { color: '#fff', fontSize: 24, fontWeight: 'bold' },
  warningText: { color: '#FECACA', fontWeight: 'bold', marginTop: 16, textAlign: 'center', backgroundColor: '#991B1B', padding: 8, borderRadius: 8 },
  exportBtn: { marginTop: 16, backgroundColor: '#374151', borderRadius: 10, padding: 10, alignItems: 'center' },
  exportBtnText: { color: '#9CA3AF', fontSize: 13, fontWeight: 'bold' },
  actionButton: { backgroundColor: '#3B82F6', borderRadius: 12, padding: 16, alignItems: 'center', marginBottom: 12 },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: 'bold' },
});
