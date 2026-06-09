import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView, Alert, ActivityIndicator, TextInput, Linking } from 'react-native';
import { api, clearAuthToken } from '../services/api';

function RoomCard({ room, telemetry, onPress }) {
  // Find temperature and humidity sensors
  const tempSensor = room.sensors?.find(s => s.type === 'temperature');
  const humSensor = room.sensors?.find(s => s.type === 'humidity');

  const hasTemp = tempSensor && telemetry[tempSensor.id];
  const hasHum = humSensor && telemetry[humSensor.id];

  const temp = hasTemp ? parseFloat(telemetry[tempSensor.id].temperature) : null;
  const hum = hasHum ? parseFloat(telemetry[humSensor.id].humidity) : null;

  // Determine alert status based on thresholds
  let isAlert = false;
  if (tempSensor && temp !== null) {
    if (tempSensor.min_threshold !== null && temp < tempSensor.min_threshold) isAlert = true;
    if (tempSensor.max_threshold !== null && temp > tempSensor.max_threshold) isAlert = true;
  }
  if (humSensor && hum !== null) {
    if (humSensor.min_threshold !== null && hum < humSensor.min_threshold) isAlert = true;
    if (humSensor.max_threshold !== null && hum > humSensor.max_threshold) isAlert = true;
  }

  const getIcon = () => {
    if (room.type === 'fridge') return '❄️';
    if (room.type === 'freezer') return '🧊';
    return '🏢';
  };

  return (
    <TouchableOpacity 
      style={[styles.sensorCard, isAlert && styles.sensorCardAlert]} 
      onPress={onPress} 
      activeOpacity={0.8}
    >
      <View style={styles.sensorCardLeft}>
        <Text style={styles.sensorIcon}>{getIcon()}</Text>
        <View>
          <Text style={styles.sensorName}>{room.name}</Text>
          <Text style={styles.sensorIdText}>
            {tempSensor?.device_id || 'No Device Linked'}
          </Text>
        </View>
      </View>
      
      <View style={styles.sensorCardRight}>
        {temp !== null ? (
          <View style={{ alignItems: 'flex-end' }}>
            <Text style={[styles.sensorTemp, isAlert && styles.sensorTempAlert]}>{temp.toFixed(1)}°C</Text>
            {hum !== null && <Text style={styles.sensorHum}>{hum.toFixed(1)}% RH</Text>}
            <Text style={[styles.sensorBadge, isAlert ? styles.badgeAlert : styles.badgeOk]}>
              {isAlert ? '⚠️ ALERT' : '✅ OK'}
            </Text>
          </View>
        ) : (
          <Text style={styles.sensorTemp}>--</Text>
        )}
      </View>
    </TouchableOpacity>
  );
}

export default function SensorListScreen({ navigation }) {
  const [rooms, setRooms] = useState([]);
  const [liveData, setLiveData] = useState({});
  const [loading, setLoading] = useState(true);
  const [linked, setLinked] = useState(false);
  const [linkingMode, setLinkingMode] = useState(false);
  const [oauthCode, setOauthCode] = useState('');
  const [currentTime, setCurrentTime] = useState(new Date());

  const checkOAuthStatus = async () => {
    try {
      const res = await api.get('/monitoring/oauth/status');
      if (res.data) {
        setLinked(res.data.linked);
      }
    } catch (e) {
      console.log('Failed to check OAuth status', e);
    }
  };

  const fetchData = async () => {
    try {
      const roomsRes = await api.get('/rooms');
      if (Array.isArray(roomsRes.data)) {
        setRooms(roomsRes.data);
      } else {
        setRooms([]);
      }

      const dashboardRes = await api.get('/monitoring/dashboard');
      const telemetryMap = {};
      if (dashboardRes.data && dashboardRes.data.live_devices) {
        dashboardRes.data.live_devices.forEach(d => {
          telemetryMap[d.sensor_id] = d;
        });
      }
      setLiveData(telemetryMap);
    } catch (e) {
      console.log('Failed to fetch rooms & live telemetry in list view', e);
      setRooms([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    checkOAuthStatus();
    fetchData();
    const interval = setInterval(() => {
      fetchData();
      checkOAuthStatus();
    }, 10000);
    const timer = setInterval(() => setCurrentTime(new Date()), 60000);
    return () => {
      clearInterval(interval);
      clearInterval(timer);
    };
  }, []);

  const handleLinkPress = async () => {
    try {
      const res = await api.get('/monitoring/oauth/url');
      if (res.data && res.data.url) {
        setLinkingMode(true);
        Linking.openURL(res.data.url);
      }
    } catch (e) {
      Alert.alert('Error', 'Failed to retrieve authorization URL.');
    }
  };

  const submitCode = async () => {
    let cleanCode = oauthCode.trim();
    if (!cleanCode) {
      Alert.alert('Error', 'Please enter a code or URL.');
      return;
    }

    if (cleanCode.includes('code=')) {
      const parts = cleanCode.split('code=');
      if (parts.length > 1) {
        cleanCode = parts[1].split('&')[0];
      }
    }

    try {
      const res = await api.post('/monitoring/oauth/callback', { code: cleanCode });
      if (res.status === 200) {
        Alert.alert('Success', 'eWeLink account linked successfully! Discovering devices...');
        setLinkingMode(false);
        setOauthCode('');
        checkOAuthStatus();
        fetchData();
      }
    } catch (err) {
      Alert.alert('Linking Failed', err.response?.data?.detail || 'Invalid code provided.');
    }
  };

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

  if (loading && rooms.length === 0) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#3B82F6" />
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20 }}>
      <View style={styles.headerRow}>
        <View>
          <Text style={styles.header}>Ground Up</Text>
          <Text style={styles.subheader}>Cold Storage & Room Telemetry</Text>
          <Text style={styles.timeText}>
            {currentTime.toLocaleDateString()} {currentTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </Text>
        </View>
        <TouchableOpacity style={styles.logoutBtn} onPress={handleLogout}>
          <Text style={styles.logoutText}>Logout</Text>
        </TouchableOpacity>
      </View>

      {/* OAuth Integration Panel */}
      <View style={styles.oauthBanner}>
        {linked ? (
          <View style={styles.oauthStatusRow}>
            <View style={styles.statusDotGreen} />
            <Text style={styles.oauthStatusText}>Connected to eWeLink cloud</Text>
          </View>
        ) : linkingMode ? (
          <View style={styles.linkingContainer}>
            <Text style={styles.linkingTitle}>Link eWeLink Account</Text>
            <Text style={styles.linkingStep}>1. Complete authorization in browser.</Text>
            <Text style={styles.linkingStep}>2. Paste code or callback URL below:</Text>
            <TextInput
              style={styles.codeInput}
              placeholder="Paste code or redirect URL here..."
              value={oauthCode}
              onChangeText={setOauthCode}
              autoCapitalize="none"
              autoCorrect={false}
            />
            <View style={styles.linkingBtnRow}>
              <TouchableOpacity style={[styles.modalBtn, styles.cancelBtn]} onPress={() => setLinkingMode(false)}>
                <Text style={styles.cancelText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity style={[styles.modalBtn, styles.confirmBtn]} onPress={submitCode}>
                <Text style={styles.confirmText}>Submit Code</Text>
              </TouchableOpacity>
            </View>
          </View>
        ) : (
          <View style={styles.oauthStatusRowSpace}>
            <View style={styles.oauthStatusRow}>
              <View style={styles.statusDotOrange} />
              <Text style={styles.oauthStatusTextOrange}>eWeLink Not Connected (Simulator)</Text>
            </View>
            <TouchableOpacity style={styles.linkButton} onPress={handleLinkPress}>
              <Text style={styles.linkButtonText}>Link</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>

      <Text style={styles.sectionTitle}>Locations ({rooms.length})</Text>

      {rooms.map(room => (
        <RoomCard
          key={room.id}
          room={room}
          telemetry={liveData}
          onPress={() => {
            if (room.sensors && room.sensors.length > 0) {
              const mainSensor = room.sensors.find(s => s.type === 'temperature') || room.sensors[0];
              const mockDevice = {
                id: mainSensor.device_id || room.id,
                name: room.name,
                icon: room.type === 'fridge' ? '❄️' : (room.type === 'freezer' ? '🧊' : '🏢')
              };
              navigation.navigate('DeviceDetail', { device: mockDevice });
            } else {
              Alert.alert('No Sensors', 'This location has no linked temperature or humidity sensors.');
            }
          }}
        />
      ))}

      <Text style={styles.footerText}>Tap a room to configure thresholds & view logs</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F3F4F6' },
  loadingContainer: { flex: 1, backgroundColor: '#F3F4F6', justifyContent: 'center', alignItems: 'center' },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', paddingTop: 40, marginBottom: 20 },
  header: { fontSize: 30, fontWeight: '800', color: '#111827', marginBottom: 2 },
  subheader: { fontSize: 14, color: '#6B7280', marginBottom: 6 },
  timeText: { fontSize: 13, fontWeight: 'bold', color: '#3B82F6', marginBottom: 8 },
  logoutBtn: { backgroundColor: '#FEE2E2', borderWidth: 1, borderColor: '#FCA5A5', paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8, marginTop: 4 },
  logoutText: { color: '#991B1B', fontWeight: 'bold', fontSize: 13 },
  
  // OAuth Panel styles
  oauthBanner: {
    backgroundColor: '#FFFFFF',
    borderRadius: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    marginBottom: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.02,
    shadowRadius: 4,
    elevation: 1,
  },
  oauthStatusRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  oauthStatusRowSpace: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  statusDotGreen: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#10B981' },
  statusDotOrange: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#F59E0B' },
  oauthStatusText: { fontSize: 13, fontWeight: '700', color: '#059669' },
  oauthStatusTextOrange: { fontSize: 13, fontWeight: '700', color: '#B45309' },
  linkButton: { backgroundColor: '#2563EB', paddingHorizontal: 16, paddingVertical: 8, borderRadius: 8 },
  linkButtonText: { color: '#FFFFFF', fontSize: 12, fontWeight: 'bold' },
  
  // Linking inside app styles
  linkingContainer: { width: '100%' },
  linkingTitle: { fontSize: 15, fontWeight: 'bold', color: '#111827', marginBottom: 8 },
  linkingStep: { fontSize: 12, color: '#4B5563', marginBottom: 4 },
  codeInput: { backgroundColor: '#F3F4F6', borderWidth: 1, borderColor: '#E5E7EB', borderRadius: 10, padding: 12, fontSize: 13, color: '#111827', marginTop: 10, marginBottom: 14 },
  linkingBtnRow: { flexDirection: 'row', justifyContent: 'flex-end', gap: 8 },
  modalBtn: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8 },
  cancelBtn: { backgroundColor: '#F3F4F6' },
  confirmBtn: { backgroundColor: '#2563EB' },
  cancelText: { color: '#4B5563', fontWeight: 'bold', fontSize: 13 },
  confirmText: { color: '#FFFFFF', fontWeight: 'bold', fontSize: 13 },

  sectionTitle: { fontSize: 12, fontWeight: '800', color: '#4B5563', marginBottom: 16, textTransform: 'uppercase', letterSpacing: 1 },

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
  sensorIcon: { fontSize: 28 },
  sensorName: { fontSize: 18, fontWeight: '700', color: '#111827' },
  sensorIdText: { fontSize: 11, color: '#2563EB', fontFamily: 'monospace', marginTop: 2 },

  sensorCardRight: { alignItems: 'flex-end' },
  sensorTemp: { fontSize: 24, fontWeight: 'bold', color: '#111827' },
  sensorTempAlert: { color: '#EF4444' },
  sensorHum: { fontSize: 13, color: '#6B7280', marginTop: 2 },

  sensorBadge: { fontSize: 10, fontWeight: 'bold', marginTop: 6, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6, overflow: 'hidden' },
  badgeOk: { backgroundColor: '#D1FAE5', color: '#065F46', borderWidth: 1, borderColor: '#A7F3D0' },
  badgeAlert: { backgroundColor: '#FEE2E2', color: '#991B1B', borderWidth: 1, borderColor: '#FCA5A5' },

  footerText: { textAlign: 'center', color: '#9CA3AF', fontSize: 12, marginTop: 20, marginBottom: 30 },
});
