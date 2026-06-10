import React, { useState, useEffect, useRef } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView, Animated, TextInput, Alert, ActivityIndicator } from 'react-native';
import { api } from '../services/api';
import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';

export default function DashboardScreen({ route, navigation }) {
  const device = route.params.device;
  const [telemetry, setTelemetry] = useState(null);
  const [metrics24h, setMetrics24h] = useState(null);
  const [loading, setLoading] = useState(true);
  const [minThreshold, setMinThreshold] = useState('');
  const [maxThreshold, setMaxThreshold] = useState('');
  const flashAnim = useRef(new Animated.Value(0)).current;

  const fetchThresholds = async () => {
    try {
      const res = await api.get(`/sensors/device/${device.id}/sensors`);
      const tempSensor = res.data.find(s => s.type === 'temperature');
      if (tempSensor) {
        setMinThreshold(tempSensor.min_threshold !== null ? String(tempSensor.min_threshold) : '');
        setMaxThreshold(tempSensor.max_threshold !== null ? String(tempSensor.max_threshold) : '');
      }
    } catch (e) {
      console.log('Error fetching thresholds:', e);
    }
  };

  const fetchTelemetry = async () => {
    try {
      const response = await api.get(`/sensors/device/${device.id}/telemetry?days=1`);
      if (response.data && response.data.length > 0) {
        setTelemetry(response.data[0]);
      }
      
      const metricsRes = await api.get(`/sensors/device/${device.id}/metrics/24h`);
      if (metricsRes.data) {
        setMetrics24h(metricsRes.data);
      }
    } catch (err) {
      console.log('Error fetching telemetry:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchThresholds();
    fetchTelemetry();
    const interval = setInterval(fetchTelemetry, 5000);
    return () => clearInterval(interval);
  }, []);

  const parseDate = (timestampStr) => {
    if (!timestampStr) return null;
    const normalized = timestampStr.replace(' ', 'T');
    const parts = normalized.split('T');
    if (parts.length === 2 && !parts[1].includes('Z') && !parts[1].match(/[+-]\d{2}:?\d{2}$/)) {
      return new Date(normalized + 'Z');
    }
    return new Date(normalized);
  };

  const lastUpdate = telemetry ? parseDate(telemetry.timestamp) : null;
  const isOnline = lastUpdate ? (new Date() - lastUpdate) < 2 * 60 * 1000 : false;
  const isOffline = telemetry && !isOnline;

  const tMin = minThreshold !== '' ? parseFloat(minThreshold) : null;
  const tMax = maxThreshold !== '' ? parseFloat(maxThreshold) : null;
  const temp = telemetry ? parseFloat(telemetry.temperature) : null;
  const isAlert = temp !== null && !isOffline && (
    (tMin !== null && temp < tMin) ||
    (tMax !== null && temp > tMax)
  );

  useEffect(() => {
    if (isAlert) {
      Animated.loop(
        Animated.sequence([
          Animated.timing(flashAnim, { toValue: 1, duration: 600, useNativeDriver: false }),
          Animated.timing(flashAnim, { toValue: 0, duration: 600, useNativeDriver: false })
        ])
      ).start();
    } else {
      flashAnim.stopAnimation();
      flashAnim.setValue(0);
    }
  }, [isAlert]);

  const handleExportCSV = async () => {
    try {
      const response = await api.get(`/sensors/device/${device.id}/export`, {
        responseType: 'text'
      });
      const fileUri = `${FileSystem.documentDirectory}telemetry_${device.id}.csv`;
      await FileSystem.writeAsStringAsync(fileUri, response.data, { encoding: FileSystem.EncodingType.UTF8 });
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(fileUri);
      }
    } catch (e) {
      console.log('Export failed', e);
      Alert.alert('Error', 'Failed to export telemetry data');
    }
  };


  const handleSaveThresholds = async () => {
    const minVal = minThreshold !== '' ? parseFloat(minThreshold) : null;
    const maxVal = maxThreshold !== '' ? parseFloat(maxThreshold) : null;

    if (minVal !== null && maxVal !== null && minVal >= maxVal) {
      Alert.alert('Invalid Thresholds', 'Min temperature must be strictly less than Max temperature.');
      return;
    }

    const body = {
      temp_min: minVal,
      temp_max: maxVal
    };
    try {
      await api.put(`/sensors/device/${device.id}/thresholds`, body);
      Alert.alert('Success', 'Alert thresholds updated successfully!');
      fetchThresholds();
      fetchTelemetry();
    } catch (e) {
      Alert.alert('Error', 'Failed to update thresholds');
    }
  };

  const warningBackgroundColor = flashAnim.interpolate({
    inputRange: [0, 1],
    outputRange: ['#FFFFFF', '#FEE2E2']
  });

  if (loading && !telemetry) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#3B82F6" />
      </View>
    );
  }

  const cardBgColor = isOffline ? '#E5E7EB' : (isAlert ? warningBackgroundColor : '#FFFFFF');

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20 }}>
      <Text style={styles.header}>{device.icon} {device.name}</Text>
      
      <Animated.View style={[styles.card, { backgroundColor: cardBgColor }]}>
        <Text style={styles.cardTitle}>LIVE METRIC READINGS</Text>
        
        {/* Large Temperature Indicator */}
        <View style={styles.mainTelemetryDisplay}>
          <Text style={styles.largeLabel}>Temperature</Text>
          <Text style={[
            styles.largeValue, 
            isAlert && styles.largeValueAlert,
            isOffline && styles.largeValueOffline
          ]}>
            {telemetry ? `${parseFloat(telemetry.temperature).toFixed(1)}°C` : '--'}
          </Text>
          
          {metrics24h && metrics24h.temp_avg !== null && (
            <Text style={styles.metricSub}>
              24h Avg: {parseFloat(metrics24h.temp_avg).toFixed(1)}°C | Min: {parseFloat(metrics24h.temp_min).toFixed(1)}°C | Max: {parseFloat(metrics24h.temp_max).toFixed(1)}°C
            </Text>
          )}
        </View>

        {/* Humidity Indicator */}
        <View style={[styles.secondaryTelemetryDisplay, { marginTop: 20 }]}>
          <Text style={styles.largeLabel}>Relative Humidity</Text>
          <Text style={[styles.humidityValue, isOffline && styles.humidityValueOffline]}>
            {telemetry ? `${parseFloat(telemetry.humidity).toFixed(1)}%` : '--'}
          </Text>
          
          {metrics24h && metrics24h.hum_avg !== null && (
            <Text style={styles.metricSub}>
              24h Avg: {parseFloat(metrics24h.hum_avg).toFixed(1)}% | Min: {parseFloat(metrics24h.hum_min).toFixed(1)}% | Max: {parseFloat(metrics24h.hum_max).toFixed(1)}%
            </Text>
          )}
        </View>

        <View style={styles.batteryTimeRow}>
          <View style={styles.miniMetricBox}>
            <Text style={styles.miniLabel}>BATTERY LEVEL</Text>
            <Text style={styles.miniValue}>
              🔋 {telemetry ? `${parseInt(telemetry.battery_level)}%` : '--'}
            </Text>
          </View>
          <View style={[styles.miniMetricBox, { alignItems: 'flex-end' }]}>
            <Text style={styles.miniLabel}>LAST UPDATE</Text>
            <Text style={styles.miniValue}>
              🕒 {telemetry ? new Date(telemetry.timestamp.endsWith('Z') ? telemetry.timestamp : telemetry.timestamp + 'Z').toLocaleTimeString() : '--'}
            </Text>
          </View>
        </View>

        {isOffline ? (
          <Text style={[styles.warningText, styles.warningTextOffline]}>
            {`⚠️ DEVICE IS OFFLINE (No data for >10 mins)\nLast Active: ${telemetry ? new Date(telemetry.timestamp.endsWith('Z') ? telemetry.timestamp : telemetry.timestamp + 'Z').toLocaleTimeString() : 'Never'}`}
          </Text>
        ) : isAlert ? (
          <Text style={styles.warningText}>
            {tMin !== null && temp < tMin ? `⚠️ TEMPERATURE BELOW SAFE LIMIT (${tMin}°C)` : `⚠️ TEMPERATURE EXCEEDS SAFE LIMIT (${tMax}°C)`}
          </Text>
        ) : null}
      </Animated.View>

      <TouchableOpacity 
        style={styles.actionButton} 
        onPress={() => navigation.navigate('Analytics', { device })}
        activeOpacity={0.8}
      >
        <Text style={styles.buttonText}>📊 View 7-Day Analytics</Text>
      </TouchableOpacity>
      
      <TouchableOpacity 
        style={[styles.actionButton, styles.exportButton]} 
        onPress={handleExportCSV}
        activeOpacity={0.8}
      >
        <Text style={styles.buttonText}>📥 Export CSV Audit Log</Text>
      </TouchableOpacity>

      {/* Spacious Threshold Config Panel */}
      <View style={styles.thresholdPanel}>
        <Text style={styles.thresholdPanelTitle}>Threshold Configurations</Text>
        
        <View style={styles.thresholdRow}>
          <View style={styles.inputContainer}>
            <Text style={styles.inputLabel}>MIN TEMPERATURE (°C)</Text>
            <TextInput
              style={styles.textInput}
              keyboardType="numeric"
              value={minThreshold}
              onChangeText={setMinThreshold}
              placeholder="None"
              placeholderTextColor="#9CA3AF"
            />
          </View>
          <View style={styles.inputContainer}>
            <Text style={styles.inputLabel}>MAX TEMPERATURE (°C)</Text>
            <TextInput
              style={styles.textInput}
              keyboardType="numeric"
              value={maxThreshold}
              onChangeText={setMaxThreshold}
              placeholder="None"
              placeholderTextColor="#9CA3AF"
            />
          </View>
        </View>
        
        <TouchableOpacity 
          style={styles.saveButton} 
          onPress={handleSaveThresholds}
          activeOpacity={0.8}
        >
          <Text style={styles.saveButtonText}>Apply Threshold Updates</Text>
        </TouchableOpacity>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F3F4F6' },
  loadingContainer: { flex: 1, backgroundColor: '#F3F4F6', justifyContent: 'center', alignItems: 'center' },
  header: { fontSize: 26, fontWeight: '800', color: '#111827', marginTop: 20, marginBottom: 20, letterSpacing: 0.5 },
  card: {
    borderRadius: 24, padding: 24, marginBottom: 24,
    borderWidth: 1, borderColor: '#E5E7EB',
    backgroundColor: '#FFFFFF',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.05,
    shadowRadius: 10,
    elevation: 2,
  },
  cardTitle: { color: '#6B7280', fontSize: 11, marginBottom: 20, fontWeight: '800', letterSpacing: 1 },
  mainTelemetryDisplay: {
    alignItems: 'center',
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#E5E7EB',
  },
  secondaryTelemetryDisplay: {
    alignItems: 'center',
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#E5E7EB',
  },
  largeLabel: {
    color: '#6B7280',
    fontSize: 12,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 8,
  },
  largeValue: {
    color: '#111827',
    fontSize: 64,
    fontWeight: '900',
    textAlign: 'center',
  },
  largeValueAlert: {
    color: '#EF4444',
  },
  largeValueOffline: {
    color: '#6B7280',
  },
  humidityValue: {
    color: '#2563EB',
    fontSize: 40,
    fontWeight: '800',
    textAlign: 'center',
  },
  humidityValueOffline: {
    color: '#9CA3AF',
  },
  metricSub: { color: '#6B7280', fontSize: 11, marginTop: 8, textAlign: 'center', fontWeight: '500' },
  batteryTimeRow: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 18 },
  miniMetricBox: { flex: 1 },
  miniLabel: { color: '#6B7280', fontSize: 10, fontWeight: '800', letterSpacing: 0.5, marginBottom: 4 },
  miniValue: { color: '#111827', fontSize: 14, fontWeight: 'bold' },
  warningText: { color: '#FFFFFF', fontWeight: 'bold', marginTop: 20, textAlign: 'center', backgroundColor: '#EF4444', padding: 12, borderRadius: 12, overflow: 'hidden' },
  warningTextOffline: { backgroundColor: '#6B7280' },
  
  actionButton: { backgroundColor: '#2563EB', borderRadius: 14, padding: 18, alignItems: 'center', marginBottom: 12 },
  exportButton: { backgroundColor: '#10B981' },
  buttonText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  
  thresholdPanel: { padding: 20, backgroundColor: '#FFFFFF', borderRadius: 24, borderWidth: 1, borderColor: '#E5E7EB', marginBottom: 30, shadowColor: '#000', shadowOffset: { width: 0, height: 4 }, shadowOpacity: 0.05, shadowRadius: 10, elevation: 2 },
  thresholdPanelTitle: { color: '#111827', fontSize: 14, fontWeight: '800', marginBottom: 18, letterSpacing: 0.5 },
  thresholdRow: { flexDirection: 'row', justifyContent: 'space-between', gap: 16 },
  inputContainer: { flex: 1 },
  inputLabel: { color: '#6B7280', fontSize: 10, fontWeight: '800', marginBottom: 8, letterSpacing: 0.5 },
  textInput: { backgroundColor: '#F3F4F6', borderWidth: 1, borderColor: '#E5E7EB', borderRadius: 12, padding: 14, color: '#111827', fontSize: 15 },
  saveButton: { backgroundColor: '#3B82F6', borderRadius: 12, padding: 16, alignItems: 'center', marginTop: 20 },
  saveButtonText: { color: '#FFFFFF', fontSize: 15, fontWeight: 'bold' }
});
