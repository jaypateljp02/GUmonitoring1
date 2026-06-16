import React, { useState, useEffect, useRef } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView, Animated, TextInput, Alert, ActivityIndicator } from 'react-native';
import { api } from '../services/api';
import RNFS from 'react-native-fs';
import Share from 'react-native-share';

export default function DashboardScreen({ route, navigation }) {
  const device = route.params.device;
  const [telemetry, setTelemetry] = useState(null);
  const [metrics24h, setMetrics24h] = useState(null);
  const [loading, setLoading] = useState(true);
  const [minThreshold, setMinThreshold] = useState('');
  const [maxThreshold, setMaxThreshold] = useState('');
  const [minHumThreshold, setMinHumThreshold] = useState('');
  const [maxHumThreshold, setMaxHumThreshold] = useState('');
  
  // Webhook alert URLs
  const [alertWebhook, setAlertWebhook] = useState('');
  const [recoveryWebhook, setRecoveryWebhook] = useState('');

  // Plug Live Data state
  const [plugData, setPlugData] = useState(null);
  const [isTogglingPlug, setIsTogglingPlug] = useState(false);
  const flashAnim = useRef(new Animated.Value(0)).current;
  const [isConfigCollapsed, setIsConfigCollapsed] = useState(true);

  const fetchThresholds = async () => {
    try {
      const res = await api.get(`/sensors/device/${device.id}/sensors`);
      const tempSensor = res.data.find(s => s.type === 'temperature');
      if (tempSensor) {
        setMinThreshold(tempSensor.min_threshold !== null ? String(tempSensor.min_threshold) : '');
        setMaxThreshold(tempSensor.max_threshold !== null ? String(tempSensor.max_threshold) : '');
        setAlertWebhook(tempSensor.alert_webhook_url || '');
        setRecoveryWebhook(tempSensor.recovery_webhook_url || '');
      } else {
        setMinThreshold('');
        setMaxThreshold('');
        setAlertWebhook('');
        setRecoveryWebhook('');
      }

      const humSensor = res.data.find(s => s.type === 'humidity');
      if (humSensor) {
        setMinHumThreshold(humSensor.min_threshold !== null ? String(humSensor.min_threshold) : '');
        setMaxHumThreshold(humSensor.max_threshold !== null ? String(humSensor.max_threshold) : '');
      } else {
        setMinHumThreshold('');
        setMaxHumThreshold('');
      }
    } catch (e) {
      console.log('Error fetching thresholds:', e);
    }
  };

  const fetchTelemetry = async () => {
    try {
      const response = await api.get(`/sensors/device/${device.id}/telemetry?days=1`);
      const telemetryData = (response.data && response.data.telemetry) ? response.data.telemetry : (Array.isArray(response.data) ? response.data : null);
      if (telemetryData && telemetryData.length > 0) {
        setTelemetry(telemetryData[0]);
      }
      
      const metricsRes = await api.get(`/sensors/device/${device.id}/metrics/24h`);
      if (metricsRes.data) {
        setMetrics24h(metricsRes.data);
      }

      // Poll smart plug state
      const plugRes = await api.get(`/sensors/device/${device.id}/plug`);
      if (plugRes.data && plugRes.data.supported) {
        setPlugData(plugRes.data);
      } else {
        setPlugData(null);
      }
    } catch (err) {
      console.log('Error fetching telemetry/plug:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleTogglePlug = async () => {
    if (!plugData) return;
    const nextState = plugData.state === 'on' ? 'off' : 'on';
    try {
      setIsTogglingPlug(true);
      await api.post(`/sensors/device/${device.id}/plug/toggle`, { state: nextState });
      
      // Refresh plug state
      const plugRes = await api.get(`/sensors/device/${device.id}/plug`);
      if (plugRes.data && plugRes.data.supported) {
        setPlugData(plugRes.data);
      }
    } catch (e) {
      Alert.alert('Error', 'Failed to toggle plug power state.');
    } finally {
      setIsTogglingPlug(false);
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
    let normalized = timestampStr.replace(' ', 'T');
    // Trim 6-digit microseconds down to 3-digit milliseconds for JS compatibility
    normalized = normalized.replace(/\.(\d{3})\d+/, '.$1');
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
  const hMin = minHumThreshold !== '' ? parseFloat(minHumThreshold) : null;
  const hMax = maxHumThreshold !== '' ? parseFloat(maxHumThreshold) : null;

  const temp = telemetry ? parseFloat(telemetry.temperature) : null;
  const hum = telemetry ? parseFloat(telemetry.humidity) : null;

  const isTempAlert = temp !== null && (
    (tMin !== null && temp < tMin) ||
    (tMax !== null && temp > tMax)
  );

  const isHumAlert = hum !== null && (
    (hMin !== null && hum < hMin) ||
    (hMax !== null && hum > hMax)
  );

  const isAlert = !isOffline && (isTempAlert || isHumAlert);

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
      const fileUri = `${RNFS.DocumentDirectoryPath}/telemetry_${device.id}.csv`;
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


  const handleSaveThresholds = async () => {
    const minVal = minThreshold !== '' ? parseFloat(minThreshold) : null;
    const maxVal = maxThreshold !== '' ? parseFloat(maxThreshold) : null;
    const minHumVal = minHumThreshold !== '' ? parseFloat(minHumThreshold) : null;
    const maxHumVal = maxHumThreshold !== '' ? parseFloat(maxHumThreshold) : null;

    if (minVal !== null && maxVal !== null && minVal >= maxVal) {
      Alert.alert('Invalid Thresholds', 'Min temperature must be strictly less than Max temperature.');
      return;
    }
    if (minHumVal !== null && maxHumVal !== null && minHumVal >= maxHumVal) {
      Alert.alert('Invalid Thresholds', 'Min humidity must be strictly less than Max humidity.');
      return;
    }

    const body = {
      temp_min: minVal,
      temp_max: maxVal,
      temp_alert_webhook_url: alertWebhook || null,
      temp_recovery_webhook_url: recoveryWebhook || null,
      hum_min: minHumVal,
      hum_max: maxHumVal
    };
    try {
      await api.put(`/sensors/device/${device.id}/thresholds`, body);
      Alert.alert('Success', 'Configuration settings updated successfully!');
      fetchThresholds();
      fetchTelemetry();
    } catch (e) {
      Alert.alert('Error', 'Failed to update configuration settings');
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
      {/* Top Header Row with battery & time in the right corner */}
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 10, marginBottom: 20, flexWrap: 'wrap', gap: 10 }}>
        <Text style={[styles.header, { marginTop: 0, marginBottom: 0, flex: 1, minWidth: 150 }]} numberOfLines={1} ellipsizeMode="tail">
          {device.icon} {device.name}
        </Text>
        <View style={{ flexDirection: 'row', gap: 6, flexShrink: 0 }}>
          <View style={[styles.topBadge, { backgroundColor: isOffline ? '#E5E7EB' : '#E6F4EA', borderColor: isOffline ? '#D1D5DB' : '#C2E7C9' }]}>
            <Text style={{ color: isOffline ? '#5E5E5E' : '#137333', fontSize: 11, fontWeight: 'bold' }}>
              🔋 {telemetry ? `${parseInt(telemetry.battery_level)}%` : '--'}
            </Text>
          </View>
          <View style={[styles.topBadge, { backgroundColor: isOffline ? '#E5E7EB' : '#E8F0FE', borderColor: isOffline ? '#D1D5DB' : '#D2E3FC' }]}>
            <Text style={{ color: isOffline ? '#5E5E5E' : '#1A73E8', fontSize: 11, fontWeight: 'bold' }}>
              🕒 {telemetry ? (parseDate(telemetry.timestamp)?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) || '--') : '--'}
            </Text>
          </View>
        </View>
      </View>
      
      <Animated.View style={[styles.card, { backgroundColor: cardBgColor }]}>
        {/* Top bar with LIVE TELEMETRY label */}
        <View style={styles.topStatusRow}>
          <Text style={styles.topStatusLabel}>LIVE TELEMETRY</Text>
        </View>

        {/* Live Grid (Temp & Humidity side-by-side) */}
        <View style={styles.liveGrid}>
          {/* Temperature Box */}
          <View style={styles.liveBox}>
            <Text style={styles.liveBoxLabel}>🌡️ TEMPERATURE</Text>
            <Text style={[
              styles.liveBoxValue, 
              isAlert && styles.liveBoxValueAlert,
              isOffline && styles.liveBoxValueOffline
            ]}>
              {telemetry ? `${parseFloat(telemetry.temperature).toFixed(1)}°C` : '--'}
            </Text>
          </View>
          
          {/* Humidity Box */}
          <View style={[styles.liveBox, { borderLeftWidth: 1, borderLeftColor: '#E5E7EB' }]}>
            <Text style={styles.liveBoxLabel}>💧 HUMIDITY</Text>
            <Text style={[styles.liveBoxValue, { color: '#2563EB' }, isOffline && styles.liveBoxValueOffline]}>
              {telemetry ? `${parseFloat(telemetry.humidity).toFixed(1)}%` : '--'}
            </Text>
          </View>
        </View>

        {/* 24h Temperature Statistics (Big UI Box) */}
        {metrics24h && metrics24h.temp_min !== null && (
          <View style={styles.statsSection}>
            <Text style={styles.statsSectionTitle}>📈 24-HOUR TEMPERATURE BOUNDS</Text>
            <View style={styles.statsGrid}>
              <View style={[styles.statsCard, { backgroundColor: '#EFF6FF', borderColor: '#BFDBFE' }]}>
                <Text style={[styles.statsLabel, { color: '#1D4ED8' }]}>MINIMUM</Text>
                <Text style={[styles.statsValue, { color: '#1E40AF' }]}>
                  {parseFloat(metrics24h.temp_min).toFixed(1)}°C
                </Text>
              </View>
              <View style={[styles.statsCard, styles.statsCardAvg, { backgroundColor: '#F8FAFC', borderColor: '#E2E8F0' }]}>
                <Text style={[styles.statsLabel, styles.statsLabelAvg, { color: '#475569' }]}>AVERAGE</Text>
                <Text style={[styles.statsValueAvg, { color: '#1E293B' }]}>
                  {parseFloat(metrics24h.temp_avg).toFixed(1)}°C
                </Text>
              </View>
              <View style={[styles.statsCard, { backgroundColor: '#FEF2F2', borderColor: '#FCA5A5' }]}>
                <Text style={[styles.statsLabel, { color: '#B91C1C' }]}>MAXIMUM</Text>
                <Text style={[styles.statsValue, { color: '#991B1B' }]}>
                  {parseFloat(metrics24h.temp_max).toFixed(1)}°C
                </Text>
              </View>
            </View>
          </View>
        )}

        {/* 24h Humidity Statistics (Big UI Box) */}
        {metrics24h && metrics24h.hum_min !== null && (
          <View style={[styles.statsSection, { marginTop: 14 }]}>
            <Text style={styles.statsSectionTitle}>📈 24-HOUR HUMIDITY BOUNDS</Text>
            <View style={styles.statsGrid}>
              <View style={[styles.statsCard, { backgroundColor: '#EFF6FF', borderColor: '#BFDBFE' }]}>
                <Text style={[styles.statsLabel, { color: '#1D4ED8' }]}>MINIMUM</Text>
                <Text style={[styles.statsValue, { color: '#1E40AF' }]}>
                  {parseFloat(metrics24h.hum_min).toFixed(1)}%
                </Text>
              </View>
              <View style={[styles.statsCard, styles.statsCardAvg, { backgroundColor: '#F8FAFC', borderColor: '#E2E8F0' }]}>
                <Text style={[styles.statsLabel, styles.statsLabelAvg, { color: '#475569' }]}>AVERAGE</Text>
                <Text style={[styles.statsValueAvg, { color: '#1E293B' }]}>
                  {parseFloat(metrics24h.hum_avg).toFixed(1)}%
                </Text>
              </View>
              <View style={[styles.statsCard, { backgroundColor: '#FEF2F2', borderColor: '#FCA5A5' }]}>
                <Text style={[styles.statsLabel, { color: '#B91C1C' }]}>MAXIMUM</Text>
                <Text style={[styles.statsValue, { color: '#991B1B' }]}>
                  {parseFloat(metrics24h.hum_max).toFixed(1)}%
                </Text>
              </View>
            </View>
          </View>
        )}

        {isOffline ? (
          <Text style={[styles.warningText, styles.warningTextOffline, { marginTop: 14 }]}>
            {`⚠️ DEVICE IS OFFLINE (No data for >2 mins)\nLast Active: ${telemetry ? (parseDate(telemetry.timestamp)?.toLocaleTimeString() || 'Never') : 'Never'}`}
          </Text>
        ) : isAlert ? (
          <Text style={[styles.warningText, { marginTop: 14 }]}>
            {isTempAlert 
              ? (tMin !== null && temp < tMin ? `⚠️ TEMPERATURE BELOW SAFE LIMIT (${tMin}°C)` : `⚠️ TEMPERATURE EXCEEDS SAFE LIMIT (${tMax}°C)`)
              : (hMin !== null && hum < hMin ? `⚠️ HUMIDITY BELOW SAFE LIMIT (${hMin}%)` : `⚠️ HUMIDITY EXCEEDS SAFE LIMIT (${hMax}%)`)}
          </Text>
        ) : null}
      </Animated.View>

      <TouchableOpacity 
        style={styles.actionButton} 
        onPress={() => navigation.navigate('Analytics', { device })}
        activeOpacity={0.8}
      >
        <Text style={styles.buttonText}>📊 View Analytics</Text>
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
        <TouchableOpacity 
          style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 4 }}
          onPress={() => setIsConfigCollapsed(!isConfigCollapsed)}
          activeOpacity={0.7}
        >
          <Text style={[styles.thresholdPanelTitle, { marginBottom: 0 }]}>⚙️ Settings & Thresholds</Text>
          <Text style={{ fontSize: 13, fontWeight: '800', color: '#2563EB' }}>
            {isConfigCollapsed ? 'Expand ▽' : 'Collapse ▲'}
          </Text>
        </TouchableOpacity>
        
        {!isConfigCollapsed && (
          <View style={{ marginTop: 16 }}>
            <Text style={{ fontSize: 11, fontWeight: '800', color: '#4B5563', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Temperature Thresholds</Text>
            <View style={[styles.thresholdRow, { marginBottom: 16 }]}>
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

            <Text style={{ fontSize: 11, fontWeight: '800', color: '#4B5563', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Humidity Thresholds</Text>
            <View style={styles.thresholdRow}>
              <View style={styles.inputContainer}>
                <Text style={styles.inputLabel}>MIN HUMIDITY (%)</Text>
                <TextInput
                  style={styles.textInput}
                  keyboardType="numeric"
                  value={minHumThreshold}
                  onChangeText={setMinHumThreshold}
                  placeholder="None"
                  placeholderTextColor="#9CA3AF"
                />
              </View>
              <View style={styles.inputContainer}>
                <Text style={styles.inputLabel}>MAX HUMIDITY (%)</Text>
                <TextInput
                  style={styles.textInput}
                  keyboardType="numeric"
                  value={maxHumThreshold}
                  onChangeText={setMaxHumThreshold}
                  placeholder="None"
                  placeholderTextColor="#9CA3AF"
                />
              </View>
            </View>

            <Text style={{ fontSize: 11, fontWeight: '800', color: '#4B5563', marginBottom: 8, marginTop: 16, textTransform: 'uppercase', letterSpacing: 0.5 }}>Webhook Alert URLs</Text>
            <View style={[styles.thresholdRow, { marginBottom: 16 }]}>
              <View style={styles.inputContainer}>
                <Text style={styles.inputLabel}>ALERT WEBHOOK (ON)</Text>
                <TextInput
                  style={styles.textInput}
                  value={alertWebhook}
                  onChangeText={setAlertWebhook}
                  placeholder="https://..."
                  placeholderTextColor="#9CA3AF"
                />
              </View>
              <View style={styles.inputContainer}>
                <Text style={styles.inputLabel}>RECOVERY WEBHOOK (OFF)</Text>
                <TextInput
                  style={styles.textInput}
                  value={recoveryWebhook}
                  onChangeText={setRecoveryWebhook}
                  placeholder="https://..."
                  placeholderTextColor="#9CA3AF"
                />
              </View>
            </View>

            <TouchableOpacity 
              style={styles.saveButton} 
              onPress={handleSaveThresholds}
              activeOpacity={0.8}
            >
              <Text style={styles.saveButtonText}>Apply Settings Updates</Text>
            </TouchableOpacity>
          </View>
        )}
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
  cardTitle: { color: '#6B7280', fontSize: 10, marginBottom: 16, fontWeight: '800', letterSpacing: 1 },
  topStatusRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
    paddingBottom: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#F1F5F9',
  },
  topStatusLabel: {
    fontSize: 10,
    fontWeight: '800',
    color: '#64748B',
    letterSpacing: 0.5,
  },
  smallStatusText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#64748B',
  },
  topBadge: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 10,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.05)',
  },
  liveGrid: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 20,
    paddingBottom: 20,
    borderBottomWidth: 1,
    borderBottomColor: '#F1F5F9',
  },
  liveBox: {
    flex: 1,
    alignItems: 'center',
    paddingVertical: 4,
  },
  liveBoxLabel: {
    color: '#64748B',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.5,
    marginBottom: 6,
  },
  liveBoxValue: {
    color: '#0F172A',
    fontSize: 38,
    fontWeight: '900',
  },
  liveBoxValueAlert: {
    color: '#EF4444',
  },
  liveBoxValueOffline: {
    color: '#94A3B8',
  },
  statsSection: {
    marginTop: 4,
  },
  statsSectionTitle: {
    fontSize: 10,
    fontWeight: '800',
    color: '#64748B',
    letterSpacing: 0.5,
    marginBottom: 12,
  },
  statsGrid: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  statsCard: {
    flex: 1.25,
    paddingVertical: 18,
    paddingHorizontal: 8,
    borderRadius: 16,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  statsCardAvg: {
    flex: 0.85,
    paddingVertical: 12,
    borderRadius: 12,
  },
  statsLabel: {
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 0.5,
    marginBottom: 6,
  },
  statsLabelAvg: {
    fontSize: 9,
    marginBottom: 4,
  },
  statsValue: {
    fontSize: 22,
    fontWeight: '900',
  },
  statsValueAvg: {
    fontSize: 16,
    fontWeight: '800',
  },
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
  saveButtonText: { color: '#FFFFFF', fontSize: 15, fontWeight: 'bold' },
  
  plugCard: {
    borderRadius: 24, padding: 24, marginBottom: 24,
    borderWidth: 1, borderColor: '#E5E7EB',
    backgroundColor: '#FFFFFF',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.05,
    shadowRadius: 10,
    elevation: 2,
  }
});
