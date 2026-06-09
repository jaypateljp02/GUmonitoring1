import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Alert, ActivityIndicator, ScrollView } from 'react-native';
import { api, clearAuthToken } from '../services/api';

function StatCard({ icon, label, value }) {
  return (
    <View style={styles.statCard}>
      <Text style={styles.statIcon}>{icon}</Text>
      <View style={styles.statTextContainer}>
        <Text style={styles.statLabel}>{label}</Text>
        <Text style={styles.statValue}>{value}</Text>
      </View>
    </View>
  );
}

export default function SensorListScreen({ navigation }) {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [currentTime, setCurrentTime] = useState(new Date());

  const fetchData = async () => {
    try {
      const response = await api.get('/monitoring/dashboard');
      if (response.data && response.data.summary) {
        setSummary(response.data.summary);
      }
    } catch (e) {
      console.log('Failed to fetch dashboard summary in mobile list view', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 8000);
    const timer = setInterval(() => setCurrentTime(new Date()), 30000);
    return () => {
      clearInterval(interval);
      clearInterval(timer);
    };
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

  if (loading && !summary) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#3B82F6" />
      </View>
    );
  }

  const lastPollTime = summary?.last_updated
    ? new Date(summary.last_updated.endsWith('Z') ? summary.last_updated : summary.last_updated + 'Z')
    : null;

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.contentContainer}>
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

      <Text style={styles.sectionTitle}>Facility Device Summary</Text>

      <View style={styles.statsGrid}>
        <View style={styles.statsRow}>
          <StatCard
            icon="🏢"
            label="Monitored Rooms"
            value={summary?.total_rooms ?? 0}
          />
          <StatCard
            icon="❄️"
            label="Active Fridges"
            value={summary?.total_fridges ?? 0}
          />
        </View>
        <View style={styles.statsRow}>
          <StatCard
            icon="🧊"
            label="Deep Freezers"
            value={summary?.total_freezers ?? 0}
          />
          <StatCard
            icon="🔌"
            label="Total Sensors"
            value={summary?.total_sensors ?? 0}
          />
        </View>
      </View>

      <View style={styles.statusCard}>
        <View style={styles.statusHeaderRow}>
          <View style={styles.statusIndicator}>
            <View style={styles.statusDot} />
            <Text style={styles.statusText}>System Connected</Text>
          </View>
          <Text style={styles.pollTimeText}>
            Last Poll: {lastPollTime ? lastPollTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '--'}
          </Text>
        </View>
      </View>

      <Text style={styles.footerText}>Refactoring under new developer console App ID</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F3F4F6' },
  contentContainer: { padding: 20 },
  loadingContainer: { flex: 1, backgroundColor: '#F3F4F6', justifyContent: 'center', alignItems: 'center' },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', paddingTop: 40, marginBottom: 24 },
  header: { fontSize: 30, fontWeight: '800', color: '#111827', marginBottom: 2 },
  subheader: { fontSize: 14, color: '#6B7280', marginBottom: 6 },
  timeText: { fontSize: 13, fontWeight: 'bold', color: '#3B82F6', marginBottom: 8 },
  logoutBtn: { backgroundColor: '#FEE2E2', borderWidth: 1, borderColor: '#FCA5A5', paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8, marginTop: 4 },
  logoutText: { color: '#991B1B', fontWeight: 'bold', fontSize: 13 },
  
  sectionTitle: { fontSize: 12, fontWeight: '800', color: '#4B5563', marginBottom: 16, textTransform: 'uppercase', letterSpacing: 1 },

  statsGrid: { marginBottom: 16 },
  statsRow: { flexDirection: 'row', justifyContent: 'space-between', gap: 12, marginBottom: 12 },
  statCard: {
    flex: 1,
    backgroundColor: '#FFFFFF',
    borderRadius: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.05,
    shadowRadius: 6,
    elevation: 2,
  },
  statIcon: {
    fontSize: 24,
    backgroundColor: '#F3F4F6',
    padding: 8,
    borderRadius: 10,
    overflow: 'hidden',
    textAlign: 'center',
    width: 40,
    height: 40,
  },
  statTextContainer: { flex: 1 },
  statLabel: { fontSize: 10, fontWeight: '800', color: '#6B7280', textTransform: 'uppercase', letterSpacing: 0.5 },
  statValue: { fontSize: 20, fontWeight: '800', color: '#111827', marginTop: 2 },

  statusCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    marginTop: 8,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.02,
    shadowRadius: 4,
    elevation: 1,
  },
  statusHeaderRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  statusIndicator: { flexDirection: 'row', alignItems: 'center' },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: '#10B981',
    marginRight: 8,
  },
  statusText: { fontSize: 13, fontWeight: 'bold', color: '#059669' },
  pollTimeText: { fontSize: 11, color: '#6B7280', fontWeight: '500' },

  footerText: { textAlign: 'center', color: '#9CA3AF', fontSize: 12, marginTop: 24, marginBottom: 30 },
});
