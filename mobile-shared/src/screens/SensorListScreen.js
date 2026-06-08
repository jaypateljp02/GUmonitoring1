import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView, Alert } from 'react-native';
import { api, clearAuthToken } from '../services/api';

const DEVICES = [
  { id: 'a4b002884e', name: 'Device 1',      icon: '❄️' },
  { id: 'a4b002898f', name: 'Miso Room',      icon: '🍶' },
  { id: 'a4b0028991', name: 'Vinegar Room',   icon: '🫗' },
];

function SensorCard({ device, onPress }) {
  const [telemetry, setTelemetry] = useState(null);
  const [thresholds, setThresholds] = useState({ min: null, max: 4.0 });

  useEffect(() => {
    const fetchThresholds = async () => {
      try {
        const res = await api.get(`/sensors/device/${device.id}/sensors`);
        const tempSensor = res.data.find(s => s.type === 'temperature');
        if (tempSensor) {
          setThresholds({
            min: tempSensor.min_threshold !== null ? parseFloat(tempSensor.min_threshold) : null,
            max: tempSensor.max_threshold !== null ? parseFloat(tempSensor.max_threshold) : null
          });
        }
      } catch (e) {}
    };
    fetchThresholds();

    const fetchTelemetry = async () => {
      try {
        const res = await api.get(`/sensors/device/${device.id}/telemetry?days=1`);
        if (res.data && res.data.length > 0) setTelemetry(res.data[0]);
      } catch (e) {}
    };
    fetchTelemetry();
    const interval = setInterval(fetchTelemetry, 10000);
    return () => clearInterval(interval);
  }, []);

  const temp = telemetry ? parseFloat(telemetry.temperature) : null;
  const isAlert = temp !== null && (
    (thresholds.min !== null && temp < thresholds.min) ||
    (thresholds.max !== null && temp > thresholds.max)
  );

  return (
    <TouchableOpacity style={[styles.sensorCard, isAlert && styles.sensorCardAlert]} onPress={onPress} activeOpacity={0.7}>
      <View style={styles.sensorCardLeft}>
        <Text style={styles.sensorIcon}>{device.icon}</Text>
        <View>
          <Text style={styles.sensorName}>{device.name}</Text>
          <Text style={styles.sensorIdText}>{device.id}</Text>
        </View>
      </View>
      <View style={styles.sensorCardRight}>
        {temp !== null ? (
          <>
            <Text style={[styles.sensorTemp, isAlert && styles.sensorTempAlert]}>{temp}°C</Text>
            <Text style={[styles.sensorBadge, isAlert ? styles.badgeAlert : styles.badgeOk]}>
              {isAlert ? '⚠️ ALERT' : '✅ OK'}
            </Text>
          </>
        ) : (
          <Text style={styles.sensorTemp}>--</Text>
        )}
      </View>
    </TouchableOpacity>
  );
}

export default function SensorListScreen({ navigation }) {
  const [currentTime, setCurrentTime] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 60000);
    return () => clearInterval(timer);
  }, []);

  const handleLogout = () => {
    Alert.alert(
      'Logout',
      'Are you sure you want to log out?',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Logout',
          style: 'destructive',
          onPress: async () => {
            await clearAuthToken();
            navigation.replace('Login');
          },
        },
      ],
    );
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20 }}>
      <View style={styles.headerRow}>
        <View>
          <Text style={styles.header}>🏭 Ground Up</Text>
          <Text style={styles.subheader}>Cold Room Monitoring</Text>
          <Text style={styles.timeText}>
            {currentTime.toLocaleDateString()} {currentTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </Text>
        </View>
        <TouchableOpacity style={styles.logoutBtn} onPress={handleLogout}>
          <Text style={styles.logoutText}>Logout</Text>
        </TouchableOpacity>
      </View>
      <Text style={styles.sectionTitle}>All Sensors ({DEVICES.length})</Text>

      {DEVICES.map(device => (
        <SensorCard
          key={device.id}
          device={device}
          onPress={() => navigation.navigate('DeviceDetail', { device })}
        />
      ))}

      <Text style={styles.footerText}>Tap a sensor to view live details</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F3F4F6' },
  header: { fontSize: 30, fontWeight: 'bold', color: '#111827', marginBottom: 2 },
  subheader: { fontSize: 14, color: '#6B7280', marginBottom: 4 },
  timeText: { fontSize: 16, fontWeight: 'bold', color: '#3B82F6', marginBottom: 24 },
  sectionTitle: { fontSize: 16, fontWeight: 'bold', color: '#4B5563', marginBottom: 12, textTransform: 'uppercase', letterSpacing: 1 },

  sensorCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 16, padding: 18, marginBottom: 12,
    borderWidth: 1, borderColor: '#E5E7EB',
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.05,
    shadowRadius: 8,
    elevation: 2,
  },
  sensorCardAlert: { borderColor: '#EF4444', backgroundColor: '#FEF2F2' },

  sensorCardLeft: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  sensorIcon: { fontSize: 32 },
  sensorName: { fontSize: 18, fontWeight: 'bold', color: '#111827' },
  sensorIdText: { fontSize: 11, color: '#2563EB', fontFamily: 'monospace', marginTop: 2 },

  sensorCardRight: { alignItems: 'flex-end' },
  sensorTemp: { fontSize: 28, fontWeight: 'bold', color: '#111827' },
  sensorTempAlert: { color: '#EF4444' },

  sensorBadge: { fontSize: 11, fontWeight: 'bold', marginTop: 4, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 6, overflow: 'hidden' },
  badgeOk: { backgroundColor: '#D1FAE5', color: '#065F46' },
  badgeAlert: { backgroundColor: '#FEE2E2', color: '#991B1B' },

  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 },
  logoutBtn: { backgroundColor: '#FEE2E2', paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8, marginTop: 4 },
  logoutText: { color: '#991B1B', fontWeight: 'bold', fontSize: 13 },
  footerText: { textAlign: 'center', color: '#9CA3AF', fontSize: 12, marginTop: 20 },
});
