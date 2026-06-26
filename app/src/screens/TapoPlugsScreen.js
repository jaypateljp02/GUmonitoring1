import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  Alert,
  ActivityIndicator,
  RefreshControl,
  useWindowDimensions,
  Modal,
  TextInput,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { LineChart } from 'react-native-chart-kit';
import RNFS from 'react-native-fs';
import Share from 'react-native-share';
import { api } from '../services/api';

export default function TapoPlugsScreen({ navigation }) {
  const { width } = useWindowDimensions();
  const [rooms, setRooms] = useState([]);
  const [tapoPlugs, setTapoPlugs] = useState({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [togglingPlugs, setTogglingPlugs] = useState({});
  const [expandedPlugs, setExpandedPlugs] = useState({});
  const [plugHistory, setPlugHistory] = useState({});
  const [loadingHistory, setLoadingHistory] = useState({});

  const fetchRoomsAndPlugs = async () => {
    try {
      // 1. Fetch rooms
      const roomsRes = await api.get('/rooms');
      const allRooms = Array.isArray(roomsRes.data) ? roomsRes.data : [];
      setRooms(allRooms);

      // 2. Filter rooms that have a temperature sensor
      const tapoRooms = allRooms.filter(room => {
        return room.sensors?.some(s => s.type === 'temperature');
      });

      // 3. Fetch plug telemetry for each room concurrently
      const plugDataMap = {};
      const fetchPromises = tapoRooms.map(async (room) => {
        const tempSensor = room.sensors.find(s => s.type === 'temperature');
        if (!tempSensor.tapo_ip) {
          plugDataMap[room.id] = { room, data: null, error: 'Unconfigured' };
          return;
        }
        try {
          const res = await api.get(`/sensors/device/${tempSensor.device_id}/plug`);
          if (res.status === 200) {
            plugDataMap[room.id] = { room, data: res.data, error: null };
          } else {
            plugDataMap[room.id] = { room, data: null, error: 'Failed to fetch status' };
          }
        } catch (err) {
          const errMsg = err.response?.data?.detail || 'Offline/Network error';
          plugDataMap[room.id] = { room, data: null, error: errMsg };
        }
      });

      await Promise.all(fetchPromises);
      setTapoPlugs(plugDataMap);
    } catch (e) {
      console.log('Error fetching rooms or Tapo plugs:', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  // Poll plug status every 5 seconds when screen is focused
  useFocusEffect(
    useCallback(() => {
      fetchRoomsAndPlugs();
      const interval = setInterval(() => {
        fetchRoomsAndPlugs();
      }, 5000);
      return () => clearInterval(interval);
    }, [])
  );

  const handleRefresh = () => {
    setRefreshing(true);
    fetchRoomsAndPlugs();
  };

  const handleTogglePlug = async (roomId, currentStatus) => {
    const item = tapoPlugs[roomId];
    if (!item) return;

    const tempSensor = item.room.sensors.find(s => s.type === 'temperature');
    if (!tempSensor) return;

    const nextState = currentStatus === 'on' ? 'off' : 'on';

    setTogglingPlugs(prev => ({ ...prev, [roomId]: true }));

    try {
      const res = await api.post(`/sensors/device/${tempSensor.device_id}/plug/toggle`, {
        state: nextState,
      });

      if (res.status === 200) {
        const statusRes = await api.get(`/sensors/device/${tempSensor.device_id}/plug`);
        setTapoPlugs(prev => ({
          ...prev,
          [roomId]: { ...prev[roomId], data: statusRes.data, error: null }
        }));
      }
    } catch (e) {
      const errMsg = e.response?.data?.detail || 'Failed to toggle smart plug.';
      Alert.alert('Control Error', errMsg);
    } finally {
      setTogglingPlugs(prev => ({ ...prev, [roomId]: false }));
    }
  };

  // Tapo Settings Modal States
  const [tapoModalVisible, setTapoModalVisible] = useState(false);
  const [editingDeviceId, setEditingDeviceId] = useState(null);
  const [tapoIpInput, setTapoIpInput] = useState('');
  const [tapoUsernameInput, setTapoUsernameInput] = useState('');
  const [tapoPasswordInput, setTapoPasswordInput] = useState('');
  const [tapoRateInput, setTapoRateInput] = useState('');
  const [savingSettings, setSavingSettings] = useState(false);
  const [tapoModalMode, setTapoModalMode] = useState('edit'); // 'edit' or 'add'
  const [clearingConfig, setClearingConfig] = useState(false);

  // Timeframe and Interval States per Plug
  const [plugTimeframes, setPlugTimeframes] = useState({});
  const [plugIntervals, setPlugIntervals] = useState({});
  const [plugOfflinePeriods, setPlugOfflinePeriods] = useState({});

  const getPlugTimeframe = (roomId) => plugTimeframes[roomId] || '1D';
  const getPlugInterval = (roomId) => plugIntervals[roomId] || 1;

  const changePlugTimeframe = (roomId, deviceId, timeframe) => {
    let interval = 1;
    if (timeframe === '3D') {
      interval = 30;
    } else if (timeframe === '7D') {
      interval = 60;
    }
    
    setPlugTimeframes(prev => ({ ...prev, [roomId]: timeframe }));
    setPlugIntervals(prev => ({ ...prev, [roomId]: interval }));
    
    fetchPlugHistory(roomId, deviceId, timeframe, interval);
  };

  const openTapoModal = (deviceId, currentIp, currentUsername, currentPassword, currentRate) => {
    setEditingDeviceId(deviceId);
    setTapoIpInput(currentIp || '');
    setTapoUsernameInput(currentUsername || '');
    setTapoPasswordInput(currentPassword || '');
    setTapoRateInput(currentRate !== null && currentRate !== undefined ? String(currentRate) : '10.0');
    setTapoModalMode('edit');
    setTapoModalVisible(true);
  };

  const openAddTapoModal = () => {
    setEditingDeviceId(null);
    setTapoIpInput('');
    setTapoUsernameInput('');
    setTapoPasswordInput('');
    setTapoRateInput('10.0');
    setTapoModalMode('add');
    setTapoModalVisible(true);
  };

  const handleSaveTapoSettings = async () => {
    const rateVal = parseFloat(tapoRateInput);
    if (isNaN(rateVal) || rateVal < 0) {
      Alert.alert('Invalid Input', 'Please enter a valid billing rate.');
      return;
    }
    if (!tapoIpInput.trim()) {
      Alert.alert('Invalid Input', 'Please enter a valid Tapo IP address.');
      return;
    }
    setSavingSettings(true);
    try {
      const res = await api.put(`/sensors/device/${editingDeviceId}/thresholds`, {
        temp_tapo_ip: tapoIpInput.trim() || null,
        temp_tapo_username: tapoUsernameInput.trim() || null,
        temp_tapo_password: tapoPasswordInput || null,
        temp_tapo_billing_rate: rateVal
      });
      if (res.status === 200) {
        setTapoModalVisible(false);
        fetchRoomsAndPlugs();
      } else {
        Alert.alert('Error', 'Failed to update Tapo settings.');
      }
    } catch (err) {
      console.log('Error updating Tapo settings:', err);
      Alert.alert('Error', 'Failed to update Tapo settings.');
    } finally {
      setSavingSettings(false);
    }
  };

  const handleClearConfig = () => {
    if (!editingDeviceId) return;
    Alert.alert(
      'Clear Configuration',
      'Are you sure you want to remove this Tapo plug configuration? The plug will be removed from the dashboard.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Clear',
          style: 'destructive',
          onPress: async () => {
            setClearingConfig(true);
            try {
              const res = await api.put(`/sensors/device/${editingDeviceId}/thresholds`, {
                temp_tapo_ip: null,
                temp_tapo_username: null,
                temp_tapo_password: null,
                temp_tapo_billing_rate: 10.0
              });
              if (res.status === 200) {
                setTapoModalVisible(false);
                fetchRoomsAndPlugs();
              } else {
                Alert.alert('Error', 'Failed to clear Tapo configuration.');
              }
            } catch (err) {
              Alert.alert('Error', 'Connection error while clearing configuration.');
            } finally {
              setClearingConfig(false);
            }
          }
        }
      ]
    );
  };

  const fetchPlugHistory = async (roomId, deviceId, timeframe, interval) => {
    setLoadingHistory(prev => ({ ...prev, [roomId]: true }));
    try {
      const days = parseInt(timeframe.replace('D', ''));
      const res = await api.get(`/sensors/device/${deviceId}/plug/history`, {
        params: { days, interval_minutes: interval }
      });
      setPlugHistory(prev => ({ ...prev, [roomId]: res.data.history || [] }));
      setPlugOfflinePeriods(prev => ({ ...prev, [roomId]: res.data.offline_periods || [] }));
    } catch (err) {
      console.log('Failed to fetch plug history:', err);
    } finally {
      setLoadingHistory(prev => ({ ...prev, [roomId]: false }));
    }
  };

  const toggleAccordion = async (roomId, deviceId) => {
    const isExpanding = !expandedPlugs[roomId];
    setExpandedPlugs(prev => ({ ...prev, [roomId]: isExpanding }));

    if (isExpanding) {
      const tf = getPlugTimeframe(roomId);
      const iv = getPlugInterval(roomId);
      fetchPlugHistory(roomId, deviceId, tf, iv);
    }
  };

  const handleExportCSV = async (deviceId, roomId) => {
    try {
      const tf = getPlugTimeframe(roomId);
      const iv = getPlugInterval(roomId);
      const days = parseInt(tf.replace('D', ''));
      const response = await api.get(`/sensors/device/${deviceId}/plug/export`, {
        params: { days, interval_minutes: iv },
        responseType: 'text'
      });
      const fileUri = `${RNFS.DocumentDirectoryPath}/plug_telemetry_${deviceId}_${tf}_${iv}m.csv`;
      await RNFS.writeFile(fileUri, response.data, 'utf8');
      await Share.open({
        url: `file://${fileUri}`,
        type: 'text/csv',
      });
    } catch (e) {
      console.log('Export failed', e);
      Alert.alert('Error', 'Failed to export plug telemetry data');
    }
  };

  const tapoRoomIds = Object.keys(tapoPlugs);
  const configuredRoomIds = tapoRoomIds.filter(id => tapoPlugs[id] && tapoPlugs[id].error !== 'Unconfigured');
  const unconfiguredRooms = tapoRoomIds.filter(id => tapoPlugs[id] && tapoPlugs[id].error === 'Unconfigured').map(id => tapoPlugs[id]);

  // Chart configuration
  const chartConfig = {
    backgroundColor: '#FFFFFF',
    backgroundGradientFrom: '#FFFFFF',
    backgroundGradientTo: '#F9FAFB',
    decimalPlaces: 1,
    color: (opacity = 1) => `rgba(59, 130, 246, ${opacity})`, // Blue
    labelColor: (opacity = 1) => `rgba(107, 114, 128, ${opacity})`,
    style: { borderRadius: 16 },
    propsForDots: { r: "2", strokeWidth: "1", stroke: "#3B82F6" }
  };

  const isBillingDay = new Date().getDate() === 30 || new Date().getDate() === 31;

  return (
    <View style={{ flex: 1 }}>
      <ScrollView
        style={styles.container}
        contentContainerStyle={styles.contentContainer}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={handleRefresh} colors={['#3B82F6']} />
        }
      >
        <View style={styles.headerRow}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text style={styles.header}>Tapo Plugs</Text>
            {unconfiguredRooms.length > 0 && (
              <TouchableOpacity
                style={styles.addPlugBtn}
                onPress={openAddTapoModal}
                activeOpacity={0.8}
              >
                <Text style={styles.addPlugBtnText}>+ Add Plug</Text>
              </TouchableOpacity>
            )}
          </View>
          <Text style={styles.subheader}>Direct Smart Plug Controls & Load Telemetry</Text>
        </View>

        {loading && tapoRoomIds.length === 0 ? (
          <View style={styles.centerContainer}>
            <ActivityIndicator size="large" color="#3B82F6" />
          </View>
        ) : configuredRoomIds.length === 0 && unconfiguredRooms.length > 0 ? (
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyIcon}>🔌</Text>
            <Text style={styles.emptyTitle}>No Tapo Plugs Configured Yet</Text>
            <Text style={styles.emptyDescription}>
              Tap "+ Add Plug" above to configure your first smart plug connection.
            </Text>
          </View>
        ) : tapoRoomIds.length === 0 ? (
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyIcon}>🔌</Text>
            <Text style={styles.emptyTitle}>No Rooms Found</Text>
            <Text style={styles.emptyDescription}>
              Add rooms with temperature sensors to see their power telemetry here.
            </Text>
          </View>
        ) : (
          configuredRoomIds.map(roomId => {
            const item = tapoPlugs[roomId];
            if (!item) return null;
            const { room, data, error } = item;
            const tempSensor = room.sensors.find(s => s.type === 'temperature');
            const isToggling = !!togglingPlugs[roomId];
            const isExpanded = !!expandedPlugs[roomId];
            const isLoadingHist = !!loadingHistory[roomId];
            const history = plugHistory[roomId] || [];

            // Determine status
            let isPlugOn = false;
            let badgeText = 'OFFLINE';
            let badgeColor = '#EF4444'; // Red
            let badgeBg = 'rgba(239, 68, 68, 0.08)';
            let badgeBorder = 'rgba(239, 68, 68, 0.2)';

            if (error === 'Unconfigured') {
              badgeText = 'UNCONFIGURED';
              badgeColor = '#6B7280';
              badgeBg = 'rgba(107, 114, 128, 0.08)';
              badgeBorder = 'rgba(107, 114, 128, 0.2)';
            } else if (!error && data && data.supported) {
              isPlugOn = data.state === 'on';
              badgeText = isPlugOn ? 'ACTIVE' : 'STANDBY';
              badgeColor = isPlugOn ? '#10B981' : '#6B7280'; // Green vs Grey
              badgeBg = isPlugOn ? 'rgba(16, 185, 129, 0.08)' : 'rgba(107, 114, 128, 0.08)';
              badgeBorder = isPlugOn ? 'rgba(16, 185, 129, 0.2)' : 'rgba(107, 114, 128, 0.2)';
            } else if (error) {
              badgeText = 'ERROR';
            }

            // Process history data for charts
            let powerChartData = null;
            let energyChartData = null;
            if (history.length >= 2) {
              // Sample data to maximum of 25 points to fit comfortably on mobile screen
              let sampled = [];
              if (history.length <= 25) {
                sampled = history;
              } else {
                const step = Math.floor(history.length / 25);
                for (let i = 0; i < history.length; i += step) {
                  if (history[i]) sampled.push(history[i]);
                }
              }

              const chartLabels = sampled.map((h, idx) => {
                if (idx === 0 || idx === sampled.length - 1 || idx % 6 === 0) {
                  const parts = h.timestamp.split(' ');
                  if (parts.length === 2) {
                    const timeParts = parts[1].split(':');
                    return `${timeParts[0]}:${timeParts[1]}`;
                  }
                }
                return "";
              });

              powerChartData = {
                labels: chartLabels,
                datasets: [{
                  data: sampled.map(h => parseFloat(h.apower)),
                  color: (opacity = 1) => `rgba(59, 130, 246, ${opacity})`,
                  strokeWidth: 2
                }],
                legend: ["Load (W)"]
              };

              energyChartData = {
                labels: chartLabels,
                datasets: [{
                  data: sampled.map(h => parseFloat(h.today_energy)),
                  color: (opacity = 1) => `rgba(16, 185, 129, ${opacity})`,
                  strokeWidth: 2
                }],
                legend: ["Energy (kWh)"]
              };
            }

            if (error === 'Unconfigured') {
              return null; // Skip unconfigured - they use the Add button now
            }

            return (
              <View key={roomId} style={styles.plugCard}>
                {/* Card Header */}
                <View style={styles.cardHeader}>
                  <View style={{ flex: 1, paddingRight: 10 }}>
                    <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                      <Text style={styles.roomName}>{room.name}</Text>
                      <TouchableOpacity 
                        onPress={() => openTapoModal(
                          tempSensor.device_id,
                          tempSensor.tapo_ip,
                          tempSensor.tapo_username,
                          tempSensor.tapo_password,
                          tempSensor.tapo_billing_rate
                        )}
                        style={{ marginLeft: 8, padding: 4 }}
                      >
                        <Text style={{ fontSize: 16 }}>⚙️</Text>
                      </TouchableOpacity>
                    </View>
                    <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: 2, flexWrap: 'wrap' }}>
                      <Text style={styles.tapoIp}>
                        IP: {tempSensor.tapo_ip} (Rate: ₹{(data && data.billing_rate) ? parseFloat(data.billing_rate).toFixed(1) : (tempSensor.tapo_billing_rate ? parseFloat(tempSensor.tapo_billing_rate).toFixed(1) : '10.0')}/kWh)
                      </Text>
                    </View>
                  </View>
                  <View style={[styles.badge, { backgroundColor: badgeBg, borderColor: badgeBorder }]}>
                    <Text style={[styles.badgeText, { color: badgeColor }]}>{badgeText}</Text>
                  </View>
                </View>

                {/* Card Body (Telemetry) */}
                {error ? (
                  <View style={styles.errorContainer}>
                    <Text style={styles.errorText}>⚠️ {error}</Text>
                  </View>
                ) : data && data.supported ? (
                  <View>
                    <View style={styles.telemetryContainer}>
                      <View style={styles.telemetryBox}>
                        <Text style={styles.telemetryLabel}>ACTIVE POWER</Text>
                        <Text style={styles.telemetryValue}>{parseFloat(data.apower).toFixed(1)} W</Text>
                      </View>
                      <View style={[styles.telemetryBox, styles.borderLeft]}>
                        <Text style={styles.telemetryLabel}>VOLTAGE</Text>
                        <Text style={styles.telemetryValue}>{parseFloat(data.voltage).toFixed(1)} V</Text>
                      </View>
                      <View style={[styles.telemetryBox, styles.borderLeft]}>
                        <Text style={styles.telemetryLabel}>CURRENT</Text>
                        <Text style={styles.telemetryValue}>{parseFloat(data.current).toFixed(3)} A</Text>
                      </View>
                    </View>

                    {/* Billing Details Box */}
                    <View style={styles.billingContainer}>
                      <View style={styles.billingBox}>
                        <Text style={styles.billingLabel}>TODAY'S USE</Text>
                        <Text style={styles.billingValue}>{parseFloat(data.today_kwh).toFixed(3)} kWh</Text>
                        <Text style={styles.billingSubtext}>₹ {parseFloat(data.today_bill).toFixed(2)}</Text>
                      </View>
                      {isBillingDay && (
                        <>
                          <View style={styles.billingDivider} />
                          <View style={styles.billingBox}>
                            <Text style={styles.billingLabel}>MONTH'S BILL</Text>
                            <Text style={styles.billingValue}>{parseFloat(data.month_kwh).toFixed(3)} kWh</Text>
                            <Text style={styles.billingSubtextBlue}>₹ {parseFloat(data.month_bill).toFixed(2)}</Text>
                          </View>
                        </>
                      )}
                    </View>
                  </View>
                ) : (
                  <View style={styles.errorContainer}>
                    <Text style={styles.errorText}>⚠️ Plug telemetry is unsupported or offline.</Text>
                  </View>
                )}

                {/* Card Footer (Toggle Switch/Button) */}
                {!error && data && data.supported && (
                  <View style={{ gap: 10 }}>
                    <TouchableOpacity
                      style={[
                        styles.toggleButton,
                        isPlugOn ? styles.buttonOff : styles.buttonOn
                      ]}
                      onPress={() => handleTogglePlug(roomId, data.state)}
                      disabled={isToggling}
                      activeOpacity={0.8}
                    >
                      {isToggling ? (
                        <ActivityIndicator size="small" color="#FFFFFF" />
                      ) : (
                        <Text style={styles.toggleButtonText}>
                          Turn {isPlugOn ? 'OFF' : 'ON'}
                        </Text>
                      )}
                    </TouchableOpacity>

                    {/* Expandable Accordion Button */}
                    <TouchableOpacity
                      style={styles.accordionButton}
                      onPress={() => toggleAccordion(roomId, tempSensor.device_id)}
                      activeOpacity={0.8}
                    >
                      <Text style={styles.accordionButtonText}>
                        {isExpanded ? '▲ Hide Analytics' : '▼ View Power Trend & Logs'}
                      </Text>
                    </TouchableOpacity>

                    {/* Expandable Content */}
                    {isExpanded && (
                      <View style={styles.expandedContent}>
                        {isLoadingHist ? (
                          <ActivityIndicator size="small" color="#3B82F6" style={{ marginVertical: 20 }} />
                        ) : (
                          <View>
                            {/* Timeframe selector */}
                            <Text style={styles.selectorTitle}>Time Frame</Text>
                            <View style={styles.selectorRow}>
                              {[{ label: '1D', value: '1D' }, { label: '3D', value: '3D' }, { label: '7D', value: '7D' }].map(opt => (
                                <TouchableOpacity
                                  key={opt.value}
                                  style={[
                                    styles.smallSelectorButton,
                                    getPlugTimeframe(roomId) === opt.value && styles.smallSelectorButtonActive
                                  ]}
                                  onPress={() => changePlugTimeframe(roomId, tempSensor.device_id, opt.value)}
                                >
                                  <Text style={[
                                    styles.smallSelectorButtonText,
                                    getPlugTimeframe(roomId) === opt.value && styles.smallSelectorButtonTextActive
                                  ]}>
                                    {opt.label}
                                  </Text>
                                </TouchableOpacity>
                              ))}
                            </View>

                            {/* Interval selector */}
                            <Text style={styles.selectorTitle}>Data Interval</Text>
                            <View style={styles.selectorRow}>
                              {[
                                { label: 'Raw', value: 1 },
                                { label: '15m', value: 15 },
                                { label: '30m', value: 30 },
                                { label: '1h', value: 60 }
                              ].map(opt => (
                                <TouchableOpacity
                                  key={opt.value}
                                  style={[
                                    styles.smallSelectorButton,
                                    getPlugInterval(roomId) === opt.value && styles.smallSelectorButtonActive
                                  ]}
                                  onPress={() => {
                                    setPlugIntervals(prev => ({ ...prev, [roomId]: opt.value }));
                                    fetchPlugHistory(roomId, tempSensor.device_id, getPlugTimeframe(roomId), opt.value);
                                  }}
                                >
                                  <Text style={[
                                    styles.smallSelectorButtonText,
                                    getPlugInterval(roomId) === opt.value && styles.smallSelectorButtonTextActive
                                  ]}>
                                    {opt.label}
                                  </Text>
                                </TouchableOpacity>
                              ))}
                            </View>

                            {powerChartData ? (
                              <View style={{ marginTop: 14 }}>
                                <View style={styles.expandedHeader}>
                                  <Text style={styles.chartTitle}>Load Trend (Watts)</Text>
                                  <TouchableOpacity
                                    style={styles.exportBtn}
                                    onPress={() => handleExportCSV(tempSensor.device_id, roomId)}
                                    activeOpacity={0.8}
                                  >
                                    <Text style={styles.exportBtnText}>📥 Export CSV</Text>
                                  </TouchableOpacity>
                                </View>
                                <ScrollView horizontal={true} showsHorizontalScrollIndicator={true}>
                                  <LineChart
                                    data={powerChartData}
                                    width={Math.max(width - 70, history.length * 15)}
                                    height={160}
                                    yAxisSuffix="W"
                                    chartConfig={chartConfig}
                                    bezier
                                    style={{ marginVertical: 8, borderRadius: 12 }}
                                  />
                                </ScrollView>

                                <View style={[styles.expandedHeader, { marginTop: 14 }]}>
                                  <Text style={styles.chartTitle}>Energy Accumulation (kWh)</Text>
                                </View>
                                <ScrollView horizontal={true} showsHorizontalScrollIndicator={true}>
                                  <LineChart
                                    data={energyChartData}
                                    width={Math.max(width - 70, history.length * 15)}
                                    height={160}
                                    yAxisSuffix=" kWh"
                                    chartConfig={{
                                      ...chartConfig,
                                      color: (opacity = 1) => `rgba(16, 185, 129, ${opacity})`,
                                      propsForDots: { r: "2", strokeWidth: "1", stroke: "#10B981" }
                                    }}
                                    bezier
                                    style={{ marginVertical: 8, borderRadius: 12 }}
                                  />
                                </ScrollView>
                              </View>
                            ) : (
                              <Text style={styles.noDataText}>No history logs recorded in the selected range.</Text>
                            )}

                            {/* Offline Logs */}
                            {!isLoadingHist && plugOfflinePeriods[roomId] && plugOfflinePeriods[roomId].length > 0 && (
                              <View style={styles.plugOfflineContainer}>
                                <Text style={styles.plugOfflineTitle}>⚠️ Offline History Log</Text>
                                {plugOfflinePeriods[roomId].map((period, index) => (
                                  <View key={index} style={styles.plugOfflineRow}>
                                    <View style={{ flex: 1 }}>
                                      <Text style={styles.plugOfflineMsg}>
                                        Offline: {period.start} to {period.end}
                                      </Text>
                                    </View>
                                    <View style={styles.plugDurationBadge}>
                                      <Text style={styles.plugDurationText}>{period.duration_minutes} mins</Text>
                                    </View>
                                  </View>
                                ))}
                              </View>
                            )}
                          </View>
                        )}
                      </View>
                    )}
                  </View>
                )}
              </View>
            );
          })
        )}
      </ScrollView>

      {/* Tapo Connection Modal */}
      <Modal
        animationType="slide"
        transparent={true}
        visible={tapoModalVisible}
        onRequestClose={() => setTapoModalVisible(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <Text style={styles.modalTitle}>Tapo Connection Config</Text>
            <Text style={styles.modalSubtitle}>
              {tapoModalMode === 'add' ? 'Select a room and configure its Smart Plug:' : 'Configure Smart Plug IP, credentials, and billing rate:'}
            </Text>
            
            {/* Room Selector */}
            {tapoModalMode === 'add' && (
              <View style={{ marginBottom: 12 }}>
                <Text style={styles.fieldLabel}>Select Room</Text>
                <View style={styles.roomPickerContainer}>
                  {unconfiguredRooms.map(item => {
                    const ts = item.room.sensors.find(s => s.type === 'temperature');
                    if (!ts) return null;
                    const isSelected = editingDeviceId === ts.device_id;
                    return (
                      <TouchableOpacity
                        key={item.room.id}
                        style={[styles.roomPickerItem, isSelected && styles.roomPickerItemActive]}
                        onPress={() => setEditingDeviceId(ts.device_id)}
                        activeOpacity={0.7}
                      >
                        <Text style={[styles.roomPickerItemText, isSelected && styles.roomPickerItemTextActive]}>
                          {item.room.name}
                        </Text>
                      </TouchableOpacity>
                    );
                  })}
                </View>
              </View>
            )}
            
            {tapoModalMode === 'edit' && (
              <View style={[styles.roomPickerItemActive, { marginBottom: 12, alignSelf: 'flex-start' }]}>
                <Text style={styles.roomPickerItemTextActive}>
                  {(() => {
                    const found = Object.values(tapoPlugs).find(item => {
                      if (!item || !item.room) return false;
                      const ts = item.room.sensors.find(s => s.type === 'temperature');
                      return ts && ts.device_id === editingDeviceId;
                    });
                    return found ? found.room.name : 'Unknown Room';
                  })()}
                </Text>
              </View>
            )}
            
            <Text style={styles.fieldLabel}>Tapo IP Address</Text>
            <TextInput
              style={styles.modalInput}
              value={tapoIpInput}
              onChangeText={setTapoIpInput}
              placeholder="e.g. 192.168.1.100"
              placeholderTextColor="#94A3B8"
            />

            <Text style={styles.fieldLabel}>Tapo Username (Email)</Text>
            <TextInput
              style={styles.modalInput}
              value={tapoUsernameInput}
              onChangeText={setTapoUsernameInput}
              placeholder="e.g. user@email.com"
              placeholderTextColor="#94A3B8"
              autoCapitalize="none"
              keyboardType="email-address"
            />

            <Text style={styles.fieldLabel}>Tapo Password</Text>
            <TextInput
              style={styles.modalInput}
              value={tapoPasswordInput}
              onChangeText={setTapoPasswordInput}
              placeholder="Password"
              placeholderTextColor="#94A3B8"
              secureTextEntry={true}
              autoCapitalize="none"
            />

            <Text style={styles.fieldLabel}>Billing Rate (₹/kWh)</Text>
            <TextInput
              style={styles.modalInput}
              value={tapoRateInput}
              onChangeText={setTapoRateInput}
              keyboardType="numeric"
              placeholder="e.g. 10.0"
              placeholderTextColor="#94A3B8"
            />
            
            <View style={styles.modalButtons}>
              <TouchableOpacity
                style={[styles.modalBtn, styles.modalBtnCancel]}
                onPress={() => setTapoModalVisible(false)}
                disabled={savingSettings || clearingConfig}
              >
                <Text style={styles.modalBtnTextCancel}>Cancel</Text>
              </TouchableOpacity>
              
              <TouchableOpacity
                style={[styles.modalBtn, styles.modalBtnSave, (tapoModalMode === 'add' && !editingDeviceId) && { opacity: 0.5 }]}
                onPress={handleSaveTapoSettings}
                disabled={savingSettings || clearingConfig || (tapoModalMode === 'add' && !editingDeviceId)}
              >
                {savingSettings ? (
                  <ActivityIndicator size="small" color="#FFFFFF" />
                ) : (
                  <Text style={styles.modalBtnTextSave}>Save Settings</Text>
                )}
              </TouchableOpacity>
            </View>
            
            {tapoModalMode === 'edit' && (
              <TouchableOpacity
                style={styles.clearConfigBtn}
                onPress={handleClearConfig}
                disabled={clearingConfig || savingSettings}
                activeOpacity={0.7}
              >
                {clearingConfig ? (
                  <ActivityIndicator size="small" color="#EF4444" />
                ) : (
                  <Text style={styles.clearConfigBtnText}>🗑️ Clear Configuration</Text>
                )}
              </TouchableOpacity>
            )}
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F3F4F6' },
  contentContainer: { padding: 20, paddingTop: 40 },
  headerRow: { marginBottom: 24 },
  header: { fontSize: 30, fontWeight: '800', color: '#111827', marginBottom: 4 },
  subheader: { fontSize: 13, color: '#6B7280' },
  centerContainer: { paddingVertical: 80, justifyContent: 'center', alignItems: 'center' },
  emptyContainer: {
    backgroundColor: '#FFFFFF',
    borderRadius: 24,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    padding: 30,
    alignItems: 'center',
    marginTop: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.03,
    shadowRadius: 8,
    elevation: 2,
  },
  emptyIcon: { fontSize: 44, marginBottom: 16 },
  emptyTitle: { fontSize: 18, fontWeight: '800', color: '#111827', marginBottom: 8 },
  emptyDescription: { fontSize: 13, color: '#6B7280', textAlign: 'center', lineHeight: 20 },
  plugCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 24,
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
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderBottomWidth: 1,
    borderBottomColor: '#F1F5F9',
    paddingBottom: 14,
    marginBottom: 14,
  },
  roomName: { fontSize: 16, fontWeight: '800', color: '#111827' },
  tapoIp: { fontSize: 11, color: '#64748B', fontFamily: 'monospace', marginTop: 2 },
  badge: {
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 8,
    borderWidth: 1,
  },
  badgeText: { fontSize: 11, fontWeight: '800', letterSpacing: 0.5 },
  telemetryContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
    marginTop: 6,
  },
  telemetryBox: { flex: 1, alignItems: 'center' },
  borderLeft: { borderLeftWidth: 1, borderLeftColor: '#E2E8F0' },
  telemetryLabel: { fontSize: 9, fontWeight: '800', color: '#64748B', letterSpacing: 0.5, marginBottom: 4 },
  telemetryValue: { fontSize: 18, fontWeight: '900', color: '#0F172A' },
  
  billingContainer: {
    flexDirection: 'row',
    backgroundColor: '#F8FAFC',
    borderRadius: 16,
    padding: 12,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#E2E8F0',
    justifyContent: 'space-around',
    alignItems: 'center',
  },
  billingBox: { flex: 1, alignItems: 'center' },
  billingDivider: { borderLeftWidth: 1, borderLeftColor: '#E2E8F0', height: '100%' },
  billingLabel: { fontSize: 8.5, fontWeight: '800', color: '#64748B', letterSpacing: 0.5, marginBottom: 4 },
  billingValue: { fontSize: 13, fontWeight: '800', color: '#0F172A' },
  billingSubtext: { fontSize: 11, fontWeight: '700', color: '#10B981', marginTop: 2 },
  billingSubtextBlue: { fontSize: 11, fontWeight: '700', color: '#2563EB', marginTop: 2 },

  errorContainer: {
    backgroundColor: '#FFFBEB',
    borderWidth: 1,
    borderColor: '#FDE68A',
    borderRadius: 12,
    padding: 12,
    marginBottom: 14,
    marginTop: 4,
  },
  errorText: { fontSize: 12, color: '#B45309', fontWeight: 'bold' },
  toggleButton: {
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.05,
    shadowRadius: 4,
    elevation: 1,
  },
  buttonOn: { backgroundColor: '#3B82F6' },
  buttonOff: { backgroundColor: '#EF4444' },
  toggleButtonText: { color: '#FFFFFF', fontSize: 14, fontWeight: 'bold', letterSpacing: 0.5 },
  
  accordionButton: {
    backgroundColor: '#F1F5F9',
    borderColor: '#E2E8F0',
    borderWidth: 1,
    borderRadius: 12,
    paddingVertical: 10,
    alignItems: 'center',
  },
  accordionButtonText: { color: '#475569', fontSize: 12, fontWeight: 'bold' },
  expandedContent: {
    marginTop: 12,
    borderTopWidth: 1,
    borderTopColor: '#F1F5F9',
    paddingTop: 12,
  },
  expandedHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
  },
  chartTitle: { fontSize: 10, fontWeight: '800', color: '#64748B', letterSpacing: 0.5 },
  exportBtn: {
    backgroundColor: '#10B981',
    borderRadius: 8,
    paddingVertical: 4,
    paddingHorizontal: 8,
  },
  exportBtnText: { color: '#FFFFFF', fontSize: 10, fontWeight: 'bold' },
  noDataText: { fontSize: 11, color: '#94A3B8', textAlign: 'center', marginVertical: 12 },
  selectorTitle: { fontSize: 9, fontWeight: '800', color: '#6B7280', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6, marginTop: 8 },
  selectorRow: { flexDirection: 'row', gap: 6, marginBottom: 10 },
  smallSelectorButton: {
    flex: 1,
    backgroundColor: '#F1F5F9',
    borderWidth: 1,
    borderColor: '#E2E8F0',
    borderRadius: 8,
    paddingVertical: 6,
    alignItems: 'center',
  },
  smallSelectorButtonActive: {
    backgroundColor: '#3B82F6',
    borderColor: '#3B82F6',
  },
  smallSelectorButtonText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#475569',
  },
  smallSelectorButtonTextActive: {
    color: '#FFFFFF',
  },
  plugOfflineContainer: {
    marginTop: 14,
    backgroundColor: '#FFF5F5',
    borderRadius: 16,
    padding: 14,
    borderWidth: 1,
    borderColor: '#FEB2B2',
  },
  plugOfflineTitle: {
    fontSize: 13,
    fontWeight: '800',
    color: '#E53E3E',
    marginBottom: 10,
  },
  plugOfflineRow: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 8,
    borderRadius: 10,
    marginBottom: 6,
    borderWidth: 1,
    backgroundColor: '#FFF',
    borderColor: '#FED7D7',
  },
  plugOfflineMsg: {
    fontSize: 11,
    fontWeight: '600',
    color: '#C53030',
  },
  plugDurationBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 6,
    backgroundColor: '#E53E3E',
    marginLeft: 8,
  },
  plugDurationText: {
    fontSize: 9,
    fontWeight: '800',
    color: '#FFFFFF',
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(15, 23, 42, 0.6)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
  },
  modalContent: {
    backgroundColor: '#FFFFFF',
    borderRadius: 24,
    padding: 24,
    width: '100%',
    maxWidth: 340,
    borderWidth: 1,
    borderColor: '#E2E8F0',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.1,
    shadowRadius: 24,
    elevation: 8,
  },
  modalTitle: {
    fontSize: 18,
    fontWeight: '800',
    color: '#0F172A',
    marginBottom: 8,
  },
  modalSubtitle: {
    fontSize: 13,
    color: '#64748B',
    lineHeight: 18,
    marginBottom: 16,
  },
  modalInput: {
    backgroundColor: '#F8FAFC',
    borderWidth: 1,
    borderColor: '#CBD5E1',
    borderRadius: 12,
    padding: 12,
    fontSize: 16,
    color: '#0F172A',
    fontWeight: '600',
    marginBottom: 12,
  },
  fieldLabel: {
    fontSize: 10,
    fontWeight: '800',
    color: '#64748B',
    letterSpacing: 0.5,
    marginBottom: 4,
    textTransform: 'uppercase',
  },
  modalButtons: {
    flexDirection: 'row',
    gap: 12,
  },
  modalBtn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  modalBtnCancel: {
    backgroundColor: '#F1F5F9',
  },
  modalBtnSave: {
    backgroundColor: '#3B82F6',
  },
  modalBtnTextCancel: {
    color: '#64748B',
    fontWeight: '700',
    fontSize: 14,
  },
  modalBtnTextSave: {
    color: '#FFFFFF',
    fontWeight: '700',
    fontSize: 14,
  },
  addPlugBtn: {
    backgroundColor: '#3B82F6',
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 10,
  },
  addPlugBtnText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '800',
  },
  roomPickerContainer: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  roomPickerItem: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 10,
    backgroundColor: '#F1F5F9',
    borderWidth: 1,
    borderColor: '#E2E8F0',
  },
  roomPickerItemActive: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 10,
    backgroundColor: 'rgba(59, 130, 246, 0.1)',
    borderWidth: 1,
    borderColor: '#3B82F6',
  },
  roomPickerItemText: {
    fontSize: 12,
    fontWeight: '700',
    color: '#64748B',
  },
  roomPickerItemTextActive: {
    fontSize: 12,
    fontWeight: '700',
    color: '#3B82F6',
  },
  clearConfigBtn: {
    marginTop: 12,
    paddingVertical: 12,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(239, 68, 68, 0.06)',
    borderWidth: 1,
    borderColor: 'rgba(239, 68, 68, 0.2)',
  },
  clearConfigBtnText: {
    color: '#EF4444',
    fontWeight: '800',
    fontSize: 13,
  },
});
