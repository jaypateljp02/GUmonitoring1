import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView, Alert, ActivityIndicator, RefreshControl, TextInput, Platform } from 'react-native';
import { api, clearAuthToken, getApiUrl, setApiUrl, tasksApi } from '../services/api';
import { isMuted, setMuted, playAudio } from '../services/audioService';
import { startRecording, stopRecording } from '../services/audioRecorderService';

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

function RoomCard({ room, telemetry, onPress }) {
  // Find temperature and humidity sensors
  const tempSensor = room.sensors?.find(s => s.type === 'temperature');
  const humSensor = room.sensors?.find(s => s.type === 'humidity');

  const hasTemp = tempSensor && telemetry[tempSensor.id];
  const hasHum = humSensor && telemetry[humSensor.id];

  const temp = hasTemp ? parseFloat(telemetry[tempSensor.id].temperature) : null;
  const hum = hasHum ? parseFloat(telemetry[humSensor.id].humidity) : null;
  const hasPlug = hasTemp ? telemetry[tempSensor.id].has_plug : false;
  const apower = hasTemp ? telemetry[tempSensor.id].apower : null;

  const latestTimeStr = hasTemp ? telemetry[tempSensor.id].timestamp : null;
  const latestTime = latestTimeStr ? parseDate(latestTimeStr) : null;
  // Use the backend's is_online flag (server-side UTC comparison, no timezone bugs)
  const isOnline = hasTemp ? telemetry[tempSensor.id].is_online !== false : false;
  const isOffline = hasTemp && !isOnline;

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
      style={[
        styles.sensorCard, 
        isAlert && styles.sensorCardAlert,
        isOffline && styles.sensorCardOffline
      ]} 
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
            <Text style={[
              styles.sensorTemp, 
              isAlert && styles.sensorTempAlert,
              isOffline && styles.sensorTempOffline
            ]}>
              {isOffline ? '--' : `${temp.toFixed(1)}°C`}
            </Text>
            {hasPlug && (
              <Text style={{
                color: isOffline ? '#6B7280' : '#10B981',
                fontSize: 11,
                fontWeight: '700',
                marginTop: 1
              }}>
                {isOffline ? '🔌 Offline' : `🔌 ${parseFloat(apower).toFixed(0)}W`}
              </Text>
            )}
            <Text style={[
              styles.sensorBadge, 
              isOffline ? styles.badgeOffline : (isAlert ? styles.badgeAlert : styles.badgeOk)
            ]}>
              {isOffline ? '⚠️ OFFLINE' : (isAlert ? '⚠️ ALERT' : '✅ OK')}
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
  const [currentTime, setCurrentTime] = useState(new Date());
  const [refreshing, setRefreshing] = useState(false);
  const [currentUrl, setCurrentUrl] = useState('');
  const [showUrlSettings, setShowUrlSettings] = useState(false);
  const [newUrlInput, setNewUrlInput] = useState('');
  const [headerPressCount, setHeaderPressCount] = useState(0);
  const [showDevMode, setShowDevMode] = useState(false);

  const [muted, setMutedState] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [voiceProcessing, setVoiceProcessing] = useState(false);

  useEffect(() => {
    const checkMute = async () => {
      const m = await isMuted();
      setMutedState(m);
    };
    checkMute();
  }, []);

  const toggleMute = async () => {
    const nextMuted = !muted;
    await setMuted(nextMuted);
    setMutedState(nextMuted);
    Alert.alert("Audio Settings", nextMuted ? "Audio announcements muted" : "Audio announcements enabled");
  };

  const handleStartMic = async () => {
    try {
      setIsRecording(true);
      await startRecording();
    } catch (e) {
      setIsRecording(false);
      Alert.alert("Mic Error", e.message || "Failed to start recording");
    }
  };

  const handleStopMic = async () => {
    try {
      setIsRecording(false);
      setVoiceProcessing(true);
      const recordResult = await stopRecording();
      
      const formData = new FormData();
      if (Platform.OS === 'web') {
        formData.append('file', recordResult.blob, 'voice.webm');
      } else {
        formData.append('file', {
          uri: recordResult.uri,
          name: 'voice.m4a',
          type: 'audio/m4a'
        });
      }

      const response = await tasksApi.post('/ai/transcribe', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        }
      });

      setVoiceProcessing(false);
      const data = response.data;

      // Handle fallback/clarification prompt
      if (data.clarification_prompt && data.tts_url) {
        const absoluteUrl = `${tasksApi.defaults.baseURL}${data.tts_url}`;
        await playAudio(absoluteUrl);
        Alert.alert("Voice Assistant", data.clarification_prompt);
      } else {
        Alert.alert(
          "Voice Command Processed",
          `Detected: "${data.transcription}"\n\nTranslation: "${data.english_translation}"`
        );
        // Trigger data refresh after completing a voice task
        fetchData();
      }
    } catch (e) {
      setVoiceProcessing(false);
      Alert.alert("Voice Assistant Error", e.response?.data?.detail || e.message || "Failed to process command");
    }
  };

  const handleHeaderPress = () => {
    const nextCount = headerPressCount + 1;
    setHeaderPressCount(nextCount);
    if (nextCount >= 5) {
      setShowDevMode(!showDevMode);
      setHeaderPressCount(0);
      Alert.alert(
        "Developer Mode",
        !showDevMode 
          ? "Developer settings revealed! You can now configure the connection URL." 
          : "Developer settings hidden."
      );
    }
  };

  const loadUrl = async () => {
    try {
      const url = await getApiUrl();
      setCurrentUrl(url);
      setNewUrlInput(url);
    } catch (err) {
      console.log('Error loading API URL:', err);
    }
  };

  useEffect(() => {
    loadUrl();
  }, []);

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
      if (e.response && e.response.status === 401) {
        await clearAuthToken();
        navigation.replace('Login');
        return;
      }
      setRooms([]);
    } finally {
      setLoading(false);
    }
  };

  const onRefresh = async () => {
    setRefreshing(true);
    await fetchData();
    setRefreshing(false);
  };

  useEffect(() => {
    fetchData();
    loadUrl();
    const unsubscribe = navigation.addListener('focus', () => {
      fetchData();
      loadUrl();
    });
    const interval = setInterval(fetchData, 10000);
    const timer = setInterval(() => setCurrentTime(new Date()), 60000);
    return () => {
      unsubscribe();
      clearInterval(interval);
      clearInterval(timer);
    };
  }, [navigation]);

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

  const getRoomPriority = (room) => {
    const tempSensor = room.sensors?.find(s => s.type === 'temperature');
    const humSensor = room.sensors?.find(s => s.type === 'humidity');

    const hasTemp = tempSensor && liveData[tempSensor.id];
    const hasHum = humSensor && liveData[humSensor.id];

    const temp = hasTemp ? parseFloat(liveData[tempSensor.id].temperature) : null;
    const hum = hasHum ? parseFloat(liveData[humSensor.id].humidity) : null;

    const isOnline = hasTemp ? liveData[tempSensor.id].is_online !== false : false;
    const isOffline = hasTemp && !isOnline;

    let isAlert = false;
    if (tempSensor && temp !== null) {
      if (tempSensor.min_threshold !== null && temp < tempSensor.min_threshold) isAlert = true;
      if (tempSensor.max_threshold !== null && temp > tempSensor.max_threshold) isAlert = true;
    }
    if (humSensor && hum !== null) {
      if (humSensor.min_threshold !== null && hum < humSensor.min_threshold) isAlert = true;
      if (humSensor.max_threshold !== null && hum > humSensor.max_threshold) isAlert = true;
    }

    if (isAlert) return 1;    // ALERT at top
    if (isOffline) return 2;  // OFFLINE next
    return 3;                 // OK at bottom
  };

  const sortedRooms = [...rooms].sort((a, b) => {
    return getRoomPriority(a) - getRoomPriority(b);
  });

  if (loading && rooms.length === 0) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#3B82F6" />
      </View>
    );
  }

  return (
    <View style={{ flex: 1, backgroundColor: '#F3F4F6' }}>
      <ScrollView 
        style={styles.container} 
        contentContainerStyle={{ padding: 12 }}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} colors={["#3B82F6"]} />
        }
      >
        <View style={styles.headerContainer}>
          <View style={styles.headerTopRow}>
            <TouchableOpacity onPress={handleHeaderPress} activeOpacity={1}>
              <Text style={styles.header}>Ground Up</Text>
            </TouchableOpacity>
            <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
              <TouchableOpacity 
                style={[styles.muteBtn, muted && styles.muteBtnActive]} 
                onPress={toggleMute}
                activeOpacity={0.8}
              >
                <Text style={[styles.muteText, { color: muted ? '#4B5563' : '#0369A1' }]}>
                  {muted ? '🔇 Muted' : '🔊 Speak'}
                </Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.logoutBtn} onPress={handleLogout}>
                <Text style={styles.logoutText}>Logout</Text>
              </TouchableOpacity>
            </View>
          </View>
          <View style={styles.headerBottomRow}>
            <Text style={styles.subheader}>Cold Storage & Room Telemetry</Text>
            <View style={[styles.topBadge, { backgroundColor: '#E8F0FE', borderColor: '#D2E3FC' }]}>
              <Text style={{ color: '#1A73E8', fontSize: 10, fontWeight: 'bold' }}>
                🕒 {currentTime.toLocaleDateString()} {currentTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </Text>
            </View>
          </View>
        </View>

        {showDevMode && (
          <View style={{ marginBottom: 16 }}>
            <TouchableOpacity 
              onPress={() => setShowUrlSettings(!showUrlSettings)}
              style={{ flexDirection: 'row', alignItems: 'center', backgroundColor: '#E8F0FE', padding: 10, borderRadius: 10, borderWidth: 1, borderColor: '#D2E3FC' }}
              activeOpacity={0.7}
            >
              <Text style={{ fontSize: 12, fontWeight: '700', color: '#1A73E8', flex: 1 }}>
                ⚙️ Connected to: {currentUrl || 'Default'}
              </Text>
              <Text style={{ fontSize: 11, fontWeight: 'bold', color: '#1A73E8' }}>
                {showUrlSettings ? 'Hide' : 'Change'}
              </Text>
            </TouchableOpacity>

            {showUrlSettings && (
              <View style={{ marginTop: 8, backgroundColor: '#FFFFFF', padding: 14, borderRadius: 16, borderWidth: 1, borderColor: '#E5E7EB' }}>
                <Text style={{ fontSize: 10, fontWeight: '800', color: '#6B7280', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                  Backend Server URL
                </Text>
                <TextInput
                  style={{ backgroundColor: '#F3F4F6', borderWidth: 1, borderColor: '#E5E7EB', borderRadius: 10, padding: 12, fontSize: 14, color: '#111827', marginBottom: 12 }}
                  value={newUrlInput}
                  onChangeText={setNewUrlInput}
                  placeholder="e.g. https://gumonitoring.onrender.com"
                  placeholderTextColor="#9CA3AF"
                  autoCapitalize="none"
                  keyboardType="url"
                />
                <View style={{ flexDirection: 'row', gap: 8 }}>
                  <TouchableOpacity 
                    onPress={async () => {
                      if (newUrlInput.trim()) {
                        await setApiUrl(newUrlInput.trim());
                        setCurrentUrl(newUrlInput.trim());
                        setShowUrlSettings(false);
                        setLoading(true);
                        fetchData();
                      }
                    }}
                    style={{ flex: 1, backgroundColor: '#3B82F6', paddingVertical: 12, borderRadius: 10, alignItems: 'center' }}
                    activeOpacity={0.8}
                  >
                    <Text style={{ color: '#FFFFFF', fontSize: 14, fontWeight: 'bold' }}>Save & Connect</Text>
                  </TouchableOpacity>
                  <TouchableOpacity 
                    onPress={async () => {
                      const defaultUrl = 'https://gumonitoring.onrender.com';
                      await setApiUrl(defaultUrl);
                      setCurrentUrl(defaultUrl);
                      setNewUrlInput(defaultUrl);
                      setShowUrlSettings(false);
                      setLoading(true);
                      fetchData();
                    }}
                    style={{ backgroundColor: '#E5E7EB', paddingHorizontal: 16, paddingVertical: 12, borderRadius: 10, alignItems: 'center', justifyContent: 'center' }}
                    activeOpacity={0.8}
                  >
                    <Text style={{ color: '#4B5563', fontSize: 14, fontWeight: 'bold' }}>Reset</Text>
                  </TouchableOpacity>
                </View>
              </View>
            )}
          </View>
        )}

        <Text style={styles.sectionTitle}>Locations ({rooms.length})</Text>

        {sortedRooms.map(room => (
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

      {/* Voice Status Overlay Banner */}
      {(isRecording || voiceProcessing) && (
        <View style={styles.voiceStatusBanner}>
          <Text style={styles.voiceStatusText}>
            {isRecording ? '🎙️ Listening... Tap again to send' : '⚡ Processing voice command...'}
          </Text>
        </View>
      )}

      {/* Voice Assistant FAB */}
      <TouchableOpacity 
        style={[
          styles.fab, 
          isRecording && styles.fabRecording,
          voiceProcessing && styles.fabProcessing
        ]} 
        onPress={isRecording ? handleStopMic : handleStartMic}
        disabled={voiceProcessing}
        activeOpacity={0.8}
      >
        {voiceProcessing ? (
          <ActivityIndicator color="#FFFFFF" size="small" />
        ) : (
          <Text style={styles.fabText}>{isRecording ? '⏹️' : '🎤'}</Text>
        )}
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F3F4F6' },
  loadingContainer: { flex: 1, backgroundColor: '#F3F4F6', justifyContent: 'center', alignItems: 'center' },
  headerContainer: { paddingTop: 30, marginBottom: 12 },
  headerTopRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  headerBottomRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 },
  header: { fontSize: 26, fontWeight: '800', color: '#111827', marginBottom: 0 },
  subheader: { fontSize: 13, color: '#6B7280', marginBottom: 0 },
  topBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.05)',
  },
  logoutBtn: { backgroundColor: '#FEE2E2', borderWidth: 1, borderColor: '#FCA5A5', paddingHorizontal: 10, paddingVertical: 6, borderRadius: 6 },
  logoutText: { color: '#991B1B', fontWeight: 'bold', fontSize: 12 },
  sectionTitle: { fontSize: 11, fontWeight: '800', color: '#4B5563', marginBottom: 10, textTransform: 'uppercase', letterSpacing: 1 },

  sensorCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12, padding: 10, marginBottom: 8,
    borderWidth: 1, borderColor: '#E5E7EB',
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.05,
    shadowRadius: 8,
    elevation: 2,
  },
  sensorCardAlert: { borderColor: '#EF4444', backgroundColor: '#FEF2F2' },
  sensorCardOffline: { borderColor: '#9CA3AF', backgroundColor: '#F3F4F6' },

  sensorCardLeft: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  sensorIcon: { fontSize: 22 },
  sensorName: { fontSize: 15, fontWeight: '700', color: '#111827' },
  sensorIdText: { fontSize: 9, color: '#2563EB', fontFamily: 'monospace', marginTop: 1 },

  sensorCardRight: { alignItems: 'flex-end' },
  sensorTemp: { fontSize: 20, fontWeight: 'bold', color: '#111827' },
  sensorTempAlert: { color: '#EF4444' },
  sensorTempOffline: { color: '#6B7280' },
  sensorHum: { fontSize: 11, color: '#6B7280', marginTop: 1 },

  sensorBadge: { fontSize: 9, fontWeight: 'bold', marginTop: 4, paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, overflow: 'hidden' },
  badgeOk: { backgroundColor: '#D1FAE5', color: '#065F46', borderWidth: 1, borderColor: '#A7F3D0' },
  badgeAlert: { backgroundColor: '#FEE2E2', color: '#991B1B', borderWidth: 1, borderColor: '#FCA5A5' },
  badgeOffline: { backgroundColor: '#E5E7EB', color: '#4B5563', borderWidth: 1, borderColor: '#D1D5DB' },

  footerText: { textAlign: 'center', color: '#9CA3AF', fontSize: 12, marginTop: 20, marginBottom: 30 },
  muteBtn: { backgroundColor: '#E0F2FE', borderWidth: 1, borderColor: '#BAE6FD', paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, justifyContent: 'center', alignItems: 'center' },
  muteBtnActive: { backgroundColor: '#F3F4F6', borderColor: '#D1D5DB' },
  muteText: { fontWeight: 'bold', fontSize: 12 },
  fab: {
    position: 'absolute',
    bottom: 24,
    right: 24,
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: '#3B82F6',
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 6,
    elevation: 5,
  },
  fabRecording: {
    backgroundColor: '#EF4444',
  },
  fabProcessing: {
    backgroundColor: '#9B51E0',
  },
  fabText: {
    fontSize: 28,
  },
  voiceStatusBanner: {
    position: 'absolute',
    bottom: 100,
    left: 24,
    right: 24,
    backgroundColor: 'rgba(15, 23, 42, 0.9)',
    paddingVertical: 12,
    paddingHorizontal: 18,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  voiceStatusText: {
    color: '#FFFFFF',
    fontWeight: 'bold',
    fontSize: 13,
  }
});
