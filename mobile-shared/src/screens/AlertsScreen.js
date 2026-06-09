import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView, ActivityIndicator, Alert, Dimensions } from 'react-native';
import { api } from '../services/api';
import { BlurView } from 'expo-blur';

export default function AlertsScreen() {
  const [unresolvedAlerts, setUnresolvedAlerts] = useState([]);
  const [resolvedAlerts, setResolvedAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showResolved, setShowResolved] = useState(false);

  const fetchAlerts = async () => {
    try {
      const activeRes = await api.get('/alerts?resolved=false');
      setUnresolvedAlerts(activeRes.data);

      if (showResolved) {
        const resolvedRes = await api.get('/alerts?resolved=true');
        // Limit to 20 historical items
        setResolvedAlerts(resolvedRes.data.slice(0, 20));
      }
    } catch (e) {
      console.log('Error fetching alerts:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAlerts();
    const interval = setInterval(fetchAlerts, 8000);
    return () => clearInterval(interval);
  }, [showResolved]);

  const handleResolve = (alertId) => {
    Alert.alert(
      'Resolve Alert',
      'Confirm that you have checked the physical cold room and resolved the issue.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Mark Resolved',
          onPress: async () => {
            try {
              await api.put(`/alerts/${alertId}/resolve`);
              Alert.alert('Resolved', 'Alert status updated successfully.');
              fetchAlerts();
            } catch (err) {
              Alert.alert('Error', 'Failed to resolve the alert. Please try again.');
            }
          }
        }
      ]
    );
  };

  const formatDate = (dateStr) => {
    const d = new Date(dateStr.endsWith('Z') ? dateStr : dateStr + 'Z');
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + ' - ' + d.toLocaleDateString();
  };

  if (loading && unresolvedAlerts.length === 0) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#EF4444" />
      </View>
    );
  }

  const alertsToShow = showResolved ? resolvedAlerts : unresolvedAlerts;

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20 }}>
      <Text style={styles.header}>System Alerts</Text>

      {/* Tabs */}
      <View style={styles.tabBar}>
        <TouchableOpacity 
          style={[styles.tab, !showResolved && styles.tabActive]} 
          onPress={() => { setLoading(true); setShowResolved(false); }}
        >
          <Text style={[styles.tabText, !showResolved && styles.tabTextActive]}>
            Active ({unresolvedAlerts.length})
          </Text>
        </TouchableOpacity>
        <TouchableOpacity 
          style={[styles.tab, showResolved && styles.tabActive]} 
          onPress={() => { setLoading(true); setShowResolved(true); }}
        >
          <Text style={[styles.tabText, showResolved && styles.tabTextActive]}>
            History
          </Text>
        </TouchableOpacity>
      </View>

      {alertsToShow.length === 0 ? (
        <View style={styles.emptyContainer}>
          <Text style={styles.emptyIcon}>{showResolved ? '📜' : '✅'}</Text>
          <Text style={styles.emptyText}>
            {showResolved ? 'No historical alerts found.' : 'All systems normal. No active alarms!'}
          </Text>
        </View>
      ) : (
        alertsToShow.map((item) => (
          <View key={item.id} style={[styles.alertCard, !item.resolved && styles.alertCardActive]}>
            <View style={styles.cardHeader}>
              <View style={styles.tagContainer}>
                <View style={[styles.dot, !item.resolved ? styles.dotAlert : styles.dotResolved]} />
                <Text style={[styles.statusText, !item.resolved ? styles.textAlert : styles.textResolved]}>
                  {item.resolved ? 'RESOLVED' : 'ACTIVE ALARM'}
                </Text>
              </View>
              <Text style={styles.timeText}>{formatDate(item.created_at)}</Text>
            </View>

            <Text style={styles.messageText}>{item.message}</Text>
            <Text style={styles.valueText}>Triggered Value: <Text style={styles.highlightText}>{parseFloat(item.value)}</Text></Text>

            {!item.resolved && (
              <TouchableOpacity 
                style={styles.resolveButton} 
                onPress={() => handleResolve(item.id)}
                activeOpacity={0.8}
              >
                <Text style={styles.resolveButtonText}>Acknowledge & Resolve</Text>
              </TouchableOpacity>
            )}
          </View>
        ))
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F3F4F6',
  },
  loadingContainer: {
    flex: 1,
    backgroundColor: '#F3F4F6',
    justifyContent: 'center',
    alignItems: 'center',
  },
  header: {
    fontSize: 28,
    fontWeight: '800',
    color: '#111827',
    paddingTop: 40,
    marginBottom: 20,
    letterSpacing: 0.5,
  },
  tabBar: {
    flexDirection: 'row',
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 4,
    marginBottom: 24,
    borderWidth: 1,
    borderColor: '#E5E7EB',
  },
  tab: {
    flex: 1,
    paddingVertical: 12,
    alignItems: 'center',
    borderRadius: 8,
  },
  tabActive: {
    backgroundColor: '#F3F4F6',
  },
  tabText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#6B7280',
  },
  tabTextActive: {
    color: '#111827',
    fontWeight: '700',
  },
  emptyContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 80,
  },
  emptyIcon: {
    fontSize: 64,
    marginBottom: 16,
  },
  emptyText: {
    color: '#6B7280',
    fontSize: 16,
    textAlign: 'center',
    fontWeight: '500',
  },
  alertCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 16,
    padding: 20,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.05,
    shadowRadius: 10,
    elevation: 2,
  },
  alertCardActive: {
    borderColor: '#FCA5A5',
    backgroundColor: '#FEF2F2',
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  tagContainer: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 8,
  },
  dotAlert: {
    backgroundColor: '#EF4444',
  },
  dotResolved: {
    backgroundColor: '#10B981',
  },
  statusText: {
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 0.5,
  },
  textAlert: {
    color: '#EF4444',
  },
  textResolved: {
    color: '#10B981',
  },
  timeText: {
    color: '#6B7280',
    fontSize: 12,
  },
  messageText: {
    color: '#111827',
    fontSize: 16,
    fontWeight: 'bold',
    lineHeight: 22,
    marginBottom: 8,
  },
  valueText: {
    color: '#6B7280',
    fontSize: 14,
    marginBottom: 16,
  },
  highlightText: {
    color: '#111827',
    fontWeight: 'bold',
  },
  resolveButton: {
    backgroundColor: '#EF4444',
    paddingVertical: 14,
    borderRadius: 10,
    alignItems: 'center',
    shadowColor: '#EF4444',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.1,
    shadowRadius: 6,
    elevation: 2,
  },
  resolveButtonText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: 'bold',
  },
});
