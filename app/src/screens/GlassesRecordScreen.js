/**
 * GlassesRecordScreen.js
 * ─────────────────────────────────────────────────────────────────
 * Bandhu Vision (Smart Glasses) Mobile Screen
 * Allows factory managers to pair Pebble Onyx smart glasses,
 * stream 15-second audio chunks in the background, view live
 * Gemini rolling summaries, and manage offline cached chunks.
 * ─────────────────────────────────────────────────────────────────
 */
import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  Alert,
  Linking,
} from 'react-native';
import {
  startGlassesSession,
  stopGlassesSession,
  getOfflineQueue,
  flushOfflineQueue,
} from '../services/glassesAudioService';

export default function GlassesRecordScreen() {
  const [isRecording, setIsRecording] = useState(false);
  const [chunkCount, setChunkCount] = useState(0);
  const [seconds, setSeconds] = useState(0);
  const [rollingSummary, setRollingSummary] = useState('Summary will update automatically as you speak...');
  const [lastTranscript, setLastTranscript] = useState('');
  const [offlineCount, setOfflineCount] = useState(0);
  const [isFlushing, setIsFlushing] = useState(false);

  // Timer loop
  useEffect(() => {
    let interval = null;
    if (isRecording) {
      interval = setInterval(() => {
        setSeconds((prev) => prev + 1);
      }, 1000);
    } else {
      clearInterval(interval);
    }
    return () => clearInterval(interval);
  }, [isRecording]);

  // Check offline queue on mount
  useEffect(() => {
    checkQueue();
  }, []);

  const checkQueue = async () => {
    const queue = await getOfflineQueue();
    setOfflineCount(queue.length);
  };

  const handleStart = async () => {
    try {
      setSeconds(0);
      setChunkCount(0);
      setRollingSummary('Starting session... Connect smart glasses now.');
      setIsRecording(true);

      await startGlassesSession(
        (count, text, fullText) => {
          setChunkCount(count);
          setLastTranscript(fullText);
        },
        (summaryText) => {
          setRollingSummary(summaryText);
        }
      );
    } catch (err) {
      Alert.alert('Session Error', err.message);
      setIsRecording(false);
    }
  };

  const handleStop = async () => {
    setIsRecording(false);
    setRollingSummary('Saving meeting to server...');
    const result = await stopGlassesSession(seconds);
    if (result && result.status === 'ok') {
      Alert.alert('Meeting Saved!', 'Smart Glasses meeting has been saved to the Bandhu Meeting Hub.');
      setRollingSummary('Session saved successfully!');
    } else {
      Alert.alert('Saved Offline', 'Server unreachable. Chunk saved locally in offline queue.');
    }
    await checkQueue();
  };

  const handleFlushOffline = async () => {
    if (offlineCount === 0) return;
    setIsFlushing(true);
    const count = await flushOfflineQueue();
    setIsFlushing(false);
    await checkQueue();
    Alert.alert('Queue Flushed', `Successfully uploaded ${count} offline chunks to the server.`);
  };

  const formatTime = (secs) => {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.title}>👓 Bandhu Vision</Text>
        <Text style={styles.subtitle}>Pebble Onyx Smart Glasses Audio Studio</Text>
      </View>

      {/* Connection Card */}
      <View style={styles.card}>
        <View style={styles.cardHeader}>
          <Text style={styles.cardTitle}>Bluetooth Audio Bridge</Text>
          <View style={[styles.statusPill, isRecording ? styles.statusPillActive : styles.statusPillIdle]}>
            <Text style={styles.statusText}>{isRecording ? 'LIVE RECORDING' : 'READY'}</Text>
          </View>
        </View>

        <Text style={styles.infoText}>
          Dual Noise-Reduction Mic Array: {isRecording ? 'Connected (SCO Active)' : 'Paired & Standby'}
        </Text>

        {/* Timer & Counters */}
        <View style={styles.metricsRow}>
          <View style={styles.metricBox}>
            <Text style={styles.metricVal}>{formatTime(seconds)}</Text>
            <Text style={styles.metricLbl}>Duration</Text>
          </View>
          <View style={styles.metricBox}>
            <Text style={styles.metricVal}>{chunkCount}</Text>
            <Text style={styles.metricLbl}>15s Chunks</Text>
          </View>
          <View style={styles.metricBox}>
            <Text style={styles.metricVal}>{offlineCount}</Text>
            <Text style={styles.metricLbl}>Offline Cached</Text>
          </View>
        </View>

        {/* Start / Stop Control */}
        {!isRecording ? (
          <TouchableOpacity style={styles.startBtn} onPress={handleStart}>
            <Text style={styles.startBtnText}>🎙️ Start Glasses Session</Text>
          </TouchableOpacity>
        ) : (
          <TouchableOpacity style={styles.stopBtn} onPress={handleStop}>
            <Text style={styles.stopBtnText}>⏹️ Stop & Save Meeting</Text>
          </TouchableOpacity>
        )}
      </View>

      {/* Live Rolling Gemini Summary */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>✨ Gemini Rolling Summary</Text>
        <Text style={styles.summaryBox}>{rollingSummary}</Text>
      </View>

      {/* Offline Queue Section */}
      {offlineCount > 0 && (
        <View style={[styles.card, styles.offlineCard]}>
          <Text style={styles.cardTitle}>📦 Offline Chunks Pending ({offlineCount})</Text>
          <Text style={styles.infoText}>
            Audio recorded while in factory Wi-Fi dead zones is safely cached on phone storage.
          </Text>
          <TouchableOpacity style={styles.flushBtn} onPress={handleFlushOffline} disabled={isFlushing}>
            {isFlushing ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.flushBtnText}>🚀 Flush & Upload Queued Chunks</Text>
            )}
          </TouchableOpacity>
        </View>
      )}

      {/* Link to Production Web App */}
      <TouchableOpacity
        style={styles.webLink}
        onPress={() => Linking.openURL('https://gubandhu.initiativesewafoundation.com/meetings/')}
      >
        <Text style={styles.webLinkText}>🌐 Open Ground Up Meeting Hub Dashboard ›</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f172a' },
  content: { padding: 20 },
  header: { marginBottom: 20 },
  title: { fontSize: 24, fontWeight: 'bold', color: '#f8fafc' },
  subtitle: { fontSize: 13, color: '#94a3b8', marginTop: 4 },
  card: {
    backgroundColor: '#1e293b',
    borderRadius: 16,
    padding: 18,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#334155',
  },
  cardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 },
  cardTitle: { fontSize: 16, fontWeight: '600', color: '#f1f5f9' },
  statusPill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  statusPillIdle: { backgroundColor: '#334155' },
  statusPillActive: { backgroundColor: '#7c3aed' },
  statusText: { fontSize: 11, fontWeight: 'bold', color: '#fff' },
  infoText: { fontSize: 13, color: '#94a3b8', lineHeight: 18 },
  metricsRow: { flexDirection: 'row', justifyContent: 'space-between', marginVertical: 16 },
  metricBox: {
    backgroundColor: '#0f172a',
    flex: 1,
    marginHorizontal: 4,
    borderRadius: 10,
    padding: 12,
    alignItems: 'center',
  },
  metricVal: { fontSize: 20, fontWeight: 'bold', color: '#38bdf8' },
  metricLbl: { fontSize: 11, color: '#64748b', marginTop: 2 },
  startBtn: { backgroundColor: '#7c3aed', borderRadius: 12, paddingVertical: 14, alignItems: 'center' },
  startBtnText: { color: '#fff', fontWeight: 'bold', fontSize: 15 },
  stopBtn: { backgroundColor: '#ef4444', borderRadius: 12, paddingVertical: 14, alignItems: 'center' },
  stopBtnText: { color: '#fff', fontWeight: 'bold', fontSize: 15 },
  summaryBox: {
    backgroundColor: '#0f172a',
    borderRadius: 10,
    padding: 14,
    marginTop: 10,
    color: '#cbd5e1',
    fontSize: 14,
    lineHeight: 20,
    minHeight: 80,
  },
  offlineCard: { borderColor: '#f59e0b' },
  flushBtn: { backgroundColor: '#d97706', borderRadius: 10, paddingVertical: 12, alignItems: 'center', marginTop: 12 },
  flushBtnText: { color: '#fff', fontWeight: 'bold', fontSize: 14 },
  webLink: { paddingVertical: 14, alignItems: 'center', marginTop: 10 },
  webLinkText: { color: '#38bdf8', fontSize: 14, fontWeight: '600' },
});
