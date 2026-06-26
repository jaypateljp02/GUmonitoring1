import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, ScrollView, Dimensions, ActivityIndicator, TouchableOpacity, Alert, useWindowDimensions } from 'react-native';
import { api } from '../services/api';
import { LineChart } from 'react-native-chart-kit';
import RNFS from 'react-native-fs';
import Share from 'react-native-share';

export default function AnalyticsScreen({ route }) {
  const { width } = useWindowDimensions();
  const device = route?.params?.device || { id: 'a4b002884e', name: 'Device 1', icon: '❄️' };
  const SENSOR_ID = device.id;
  const [telemetryLogs, setTelemetryLogs] = useState([]);
  const [offlinePeriods, setOfflinePeriods] = useState([]);
  const [loading, setLoading] = useState(true);
  
  // Custom selection states
  const [timeFrame, setTimeFrame] = useState('1D');
  const [intervalMinutes, setIntervalMinutes] = useState(1);
  const [monthlyData, setMonthlyData] = useState([]);
  
  // Dynamic threshold states
  const [tempMin, setTempMin] = useState(null);
  const [tempMax, setTempMax] = useState(null);
  const [humMin, setHumMin] = useState(null);
  const [humMax, setHumMax] = useState(null);
  
  // Historical alerts states
  const [alertLogs, setAlertLogs] = useState([]);
  const [deviceSensors, setDeviceSensors] = useState([]);

  // Collapse states for logs
  const [isOfflineCollapsed, setIsOfflineCollapsed] = useState(true);
  const [isAlertsCollapsed, setIsAlertsCollapsed] = useState(true);
  const [isDoorCollapsed, setIsDoorCollapsed] = useState(true);
  const [deviceIsOnline, setDeviceIsOnline] = useState(false);

  const [compressorData, setCompressorData] = useState([]);
  const [doorLogs, setDoorLogs] = useState([]);
  const [aiSummary, setAiSummary] = useState(null);
  const [loadingNewAnalytics, setLoadingNewAnalytics] = useState(true);

  // Chart Tooltips states
  const [selectedTempPoint, setSelectedTempPoint] = useState(null);
  const [selectedHumPoint, setSelectedHumPoint] = useState(null);

  useEffect(() => {
    const fetchNewAnalytics = async () => {
      try {
        setLoadingNewAnalytics(true);
        const [compRes, doorRes, aiRes] = await Promise.all([
          api.get(`/sensors/device/${SENSOR_ID}/compressor-analytics`),
          api.get(`/sensors/device/${SENSOR_ID}/door-logs`),
          api.get(`/sensors/device/${SENSOR_ID}/ai-summary`)
        ]);
        setCompressorData(compRes.data || []);
        setDoorLogs(doorRes.data || []);
        setAiSummary(aiRes.data || null);
      } catch (err) {
        console.log('Error fetching new analytics fields:', err);
      } finally {
        setLoadingNewAnalytics(false);
      }
    };
    fetchNewAnalytics();
  }, [SENSOR_ID]);

  // Fetch thresholds once
  useEffect(() => {
    const fetchThresholds = async () => {
      try {
        const res = await api.get(`/sensors/device/${SENSOR_ID}/sensors`);
        setDeviceSensors(res.data || []);
        const tempSensor = res.data.find(s => s.type === 'temperature');
        if (tempSensor) {
          setTempMin(tempSensor.min_threshold);
          setTempMax(tempSensor.max_threshold);
        }
        const humSensor = res.data.find(s => s.type === 'humidity');
        if (humSensor) {
          setHumMin(humSensor.min_threshold);
          setHumMax(humSensor.max_threshold);
        }
      } catch (err) {
        console.log('Error fetching device thresholds', err);
      }
    };
    fetchThresholds();
  }, [SENSOR_ID]);

  // Auto-adjust interval when timeframe changes
  useEffect(() => {
    if (timeFrame === '1D') {
      setIntervalMinutes(1);
    } else if (timeFrame === '3D') {
      setIntervalMinutes(30);
    } else if (timeFrame === '7D') {
      setIntervalMinutes(60);
    } else if (timeFrame === '30D') {
      setIntervalMinutes(120);
    }
  }, [timeFrame]);

  // Fetch telemetry logs whenever parameters change
  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        if (timeFrame === 'Monthly' || timeFrame === '30D') {
          const isMonthly = timeFrame === 'Monthly';
          const endpoint = isMonthly ? `/sensors/device/${SENSOR_ID}/metrics/monthly` : `/sensors/device/${SENSOR_ID}/metrics/rolling?days=30`;
          const response = await api.get(endpoint);
          setMonthlyData(response.data.daily_metrics || []);
          setOfflinePeriods([]);
        } else {
          const numDays = parseInt(timeFrame.replace('D', ''));
          const response = await api.get(`/sensors/device/${SENSOR_ID}/telemetry`, {
            params: { days: numDays, interval_minutes: intervalMinutes }
          });
          const telemetryData = response.data.telemetry || [];
          setTelemetryLogs(telemetryData.reverse());
          setOfflinePeriods(response.data.offline_periods || []);
        }

        // Fetch online status from backend dashboard API (server-side UTC comparison)
        try {
          const dashRes = await api.get('/monitoring/dashboard');
          if (dashRes.data && dashRes.data.live_devices) {
            const myDevice = dashRes.data.live_devices.find(d => d.device_id === SENSOR_ID);
            setDeviceIsOnline(myDevice ? myDevice.is_online !== false : false);
          }
        } catch (e) {
          console.log('Error fetching dashboard status:', e);
        }
      } catch (err) {
        console.log('Error fetching analytics', err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [SENSOR_ID, timeFrame, intervalMinutes]);

  // Fetch alert logs for all logical sensors under this device
  useEffect(() => {
    const fetchAlertLogs = async () => {
      if (deviceSensors.length === 0) return;
      try {
        let allAlerts = [];
        for (const s of deviceSensors) {
          const res = await api.get(`/alerts?sensor_id=${s.id}`);
          if (res.data) {
            allAlerts.push(...res.data);
          }
        }
        allAlerts.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
        setAlertLogs(allAlerts.slice(0, 10)); // keep last 10 entries
      } catch (err) {
        console.log('Error fetching alerts', err);
      }
    };
    fetchAlertLogs();
  }, [deviceSensors]);

  const handleExportCSV = async () => {
    try {
      const exportDays = timeFrame === 'Monthly' ? 30 : parseInt(timeFrame.replace('D', ''));
      const response = await api.get(`/sensors/device/${SENSOR_ID}/export`, {
        params: { days: exportDays, interval_minutes: intervalMinutes },
        responseType: 'text'
      });
      const fileUri = `${RNFS.DocumentDirectoryPath}/telemetry_${SENSOR_ID}_${timeFrame}_${intervalMinutes}m.csv`;
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

  // Safe date parsing across iOS, Android, and Web platforms
  const parseDate = (timestampStr) => {
    if (!timestampStr) return new Date();
    let normalized = timestampStr.replace(' ', 'T');
    // Trim 6-digit microseconds down to 3-digit milliseconds for JS compatibility
    normalized = normalized.replace(/\.(\d{3})\d+/, '.$1');
    const parts = normalized.split('T');
    if (parts.length === 2 && !parts[1].includes('Z') && !parts[1].match(/[+-]\d{2}:?\d{2}$/)) {
      return new Date(normalized + 'Z');
    }
    return new Date(normalized);
  };

  // Format YYYY-MM-DD date into a premium readable date string
  const formatDate = (dateStr) => {
    if (!dateStr) return "";
    const parts = dateStr.split('-');
    if (parts.length === 3) {
      const year = parts[0];
      const monthIndex = parseInt(parts[1], 10) - 1;
      const day = parseInt(parts[2], 10);
      const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
      const monthName = monthNames[monthIndex] || parts[1];
      return `${monthName} ${day}, ${year}`;
    }
    return dateStr;
  };


  // Clean data logs and filter out invalid readings to prevent crashes
  const cleanedLogs = telemetryLogs
    .map(log => {
      const temp = parseFloat(log.temperature);
      const humVal = parseFloat(log.humidity);
      return {
        ...log,
        temperature: isNaN(temp) ? 0.0 : temp,
        humidity: isNaN(humVal) ? 0.0 : humVal
      };
    });

  // Unified peak-preserving sampling logic for both temperature and humidity
  let sampledLogs = [];
  if (cleanedLogs.length <= 300) {
    sampledLogs = cleanedLogs;
  } else {
    // group into 100 buckets to keep dataset size very clean
    const step = Math.floor(cleanedLogs.length / 100); 
    for (let i = 0; i < cleanedLogs.length; i += step) {
      const chunk = cleanedLogs.slice(i, i + step);
      if (chunk.length === 0) continue;
      
      let minTempIdx = 0;
      let maxTempIdx = 0;
      let minHumIdx = 0;
      let maxHumIdx = 0;
      
      for (let j = 1; j < chunk.length; j++) {
        if (chunk[j].temperature < chunk[minTempIdx].temperature) minTempIdx = j;
        if (chunk[j].temperature > chunk[maxTempIdx].temperature) maxTempIdx = j;
        if (chunk[j].humidity < chunk[minHumIdx].humidity) minHumIdx = j;
        if (chunk[j].humidity > chunk[maxHumIdx].humidity) maxHumIdx = j;
      }
      
      // Get unique sorted indices to preserve chronological order
      const uniqueIndices = Array.from(new Set([minTempIdx, maxTempIdx, minHumIdx, maxHumIdx])).sort((a, b) => a - b);
      uniqueIndices.forEach(idx => {
        sampledLogs.push(chunk[idx]);
      });
    }
  }

  // Spaced-out X-axis time labels to prevent overlap
  let lastLabelTime = null;
  const labels = sampledLogs.map((log, index) => {
    const d = parseDate(log.timestamp);
    if (isNaN(d.getTime())) return "";
    
    // Group dynamically depending on interval: e.g. every hour or every day
    let labelGroup;
    if (timeFrame === '7D' || timeFrame === '30D') {
      labelGroup = d.getDate(); // group by day
    } else {
      labelGroup = d.getHours() * 2 + (d.getMinutes() >= 30 ? 1 : 0); // group by 30 mins
    }
    
    // Label first point, last point, and whenever group changes
    if (index === 0 || index === sampledLogs.length - 1 || lastLabelTime !== labelGroup) {
      lastLabelTime = labelGroup;
      if (timeFrame === '7D' || timeFrame === '30D') {
        return `${d.getMonth() + 1}/${d.getDate()}`;
      }
      return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
    }
    return ""; // Hide label to avoid overlap
  });

  const effectiveMaxThreshold = tempMax !== null ? tempMax : 4.0;

  const chartData = {
    labels: labels.length > 0 ? labels : ["No Data"],
    datasets: [
      {
        data: sampledLogs.length > 0 ? sampledLogs.map(log => log.temperature) : [0],
        color: (opacity = 1) => `rgba(59, 130, 246, ${opacity})`, // Blue
        strokeWidth: 2
      },
      {
        // Max Limit line
        data: sampledLogs.length > 0 ? sampledLogs.map(() => effectiveMaxThreshold) : [effectiveMaxThreshold],
        color: (opacity = 1) => `rgba(239, 68, 68, ${opacity})`, // Red
        strokeWidth: 1.5,
        withDots: false,
      }
    ],
    legend: ["Temp (°C)", `Max Limit (${effectiveMaxThreshold}°C)`]
  };

  // If min threshold is set, add to datasets
  if (tempMin !== null) {
    chartData.datasets.push({
      data: sampledLogs.length > 0 ? sampledLogs.map(() => tempMin) : [tempMin],
      color: (opacity = 1) => `rgba(16, 185, 129, ${opacity})`, // Green
      strokeWidth: 1.5,
      withDots: false,
    });
    chartData.legend.push(`Min Limit (${tempMin}°C)`);
  }

  const timeFrameOptions = [
    { label: '24h', value: '1D' },
    { label: '3D', value: '3D' },
    { label: '7D', value: '7D' },
    { label: '30D', value: '30D' },
    { label: 'Monthly', value: 'Monthly' }
  ];

  // Prepare Monthly Data for Chart
  const monthlyLabels = monthlyData.map(d => {
    const date = new Date(d.date);
    return `${date.getMonth()+1}/${date.getDate()}`;
  });
  
  const monthlyChartData = {
    labels: monthlyLabels.length > 0 ? monthlyLabels : ["No Data"],
    datasets: [
      {
        data: monthlyData.length > 0 ? monthlyData.map(d => d.temp_max !== null ? parseFloat(d.temp_max) : 0) : [0],
        color: (opacity = 1) => `rgba(239, 68, 68, ${opacity})`, // Red (Max)
        strokeWidth: 2
      },
      {
        data: monthlyData.length > 0 ? monthlyData.map(d => d.temp_min !== null ? parseFloat(d.temp_min) : 0) : [0],
        color: (opacity = 1) => `rgba(59, 130, 246, ${opacity})`, // Blue (Min)
        strokeWidth: 2
      }
    ],
    legend: ["Max Temp (°C)", "Min Temp (°C)"]
  };

  const humChartData = {
    labels: labels.length > 0 ? labels : ["No Data"],
    datasets: [
      {
        data: sampledLogs.length > 0 ? sampledLogs.map(log => log.humidity) : [0],
        color: (opacity = 1) => `rgba(139, 92, 246, ${opacity})`, // Purple
        strokeWidth: 2
      }
    ],
    legend: ["Humidity (%)"]
  };

  if (humMax !== null) {
    humChartData.datasets.push({
      data: sampledLogs.length > 0 ? sampledLogs.map(() => humMax) : [humMax],
      color: (opacity = 1) => `rgba(236, 72, 153, ${opacity})`, // Pink
      strokeWidth: 1.5,
      withDots: false,
    });
    humChartData.legend.push(`Max Limit (${humMax}%)`);
  }

  if (humMin !== null) {
    humChartData.datasets.push({
      data: sampledLogs.length > 0 ? sampledLogs.map(() => humMin) : [humMin],
      color: (opacity = 1) => `rgba(16, 185, 129, ${opacity})`, // Green
      strokeWidth: 1.5,
      withDots: false,
    });
    humChartData.legend.push(`Min Limit (${humMin}%)`);
  }

  const monthlyHumChartData = {
    labels: monthlyLabels.length > 0 ? monthlyLabels : ["No Data"],
    datasets: [
      {
        data: monthlyData.length > 0 ? monthlyData.map(d => d.hum_max !== null ? parseFloat(d.hum_max) : 0) : [0],
        color: (opacity = 1) => `rgba(236, 72, 153, ${opacity})`, // Pink (Max)
        strokeWidth: 2
      },
      {
        data: monthlyData.length > 0 ? monthlyData.map(d => d.hum_min !== null ? parseFloat(d.hum_min) : 0) : [0],
        color: (opacity = 1) => `rgba(139, 92, 246, ${opacity})`, // Purple (Min)
        strokeWidth: 2
      }
    ],
    legend: ["Max Hum (%)", "Min Hum (%)"]
  };

  const intervalOptions = [
    { label: 'Raw', value: 1 },
    { label: '15m', value: 15 },
    { label: '30m', value: 30 },
    { label: '1h', value: 60 },
    { label: '2h', value: 120 }
  ];

  const chartConfigLight = {
    backgroundColor: '#FFFFFF',
    backgroundGradientFrom: '#FFFFFF',
    backgroundGradientTo: '#F9FAFB',
    decimalPlaces: 1,
    color: (opacity = 1) => `rgba(17, 24, 39, ${opacity})`,
    labelColor: (opacity = 1) => `rgba(107, 114, 128, ${opacity})`,
    style: { borderRadius: 16 },
    propsForDots: { r: "3", strokeWidth: "1", stroke: "#3B82F6" }
  };

  const latestTelemetry = telemetryLogs[telemetryLogs.length - 1] || null;
  // Use the backend's is_online flag (server-side UTC comparison, no timezone bugs)
  const isOnline = deviceIsOnline;
  const isOffline = latestTelemetry && !isOnline;

  // ─── Compressor efficiency derived variables ───────────────────────────────
  const totalCycles = compressorData.reduce((sum, d) => sum + (d.cycle_count || 0), 0);
  const numActiveDays = compressorData.length || 1;
  const avgCyclesPerDay = totalCycles > 0 ? (totalCycles / numActiveDays).toFixed(1) : '—';

  const totalRuntime = compressorData.reduce(
    (sum, d) => sum + parseFloat(d.total_runtime_minutes || 0), 0
  );
  const avgRuntime = totalCycles > 0 ? (totalRuntime / totalCycles).toFixed(0) : '—';

  // Classify each day by its average runtime per cycle
  const shortDays = compressorData.filter(d =>
    parseFloat(d.avg_runtime_per_cycle_minutes || 0) < 12 && (d.cycle_count || 0) > 0
  ).length;
  const optimalDays = compressorData.filter(d => {
    const avg = parseFloat(d.avg_runtime_per_cycle_minutes || 0);
    return avg >= 12 && avg <= 24 && (d.cycle_count || 0) > 0;
  }).length;
  const continuousDays = compressorData.filter(d =>
    parseFloat(d.avg_runtime_per_cycle_minutes || 0) > 24 && (d.cycle_count || 0) > 0
  ).length;
  const classifiedDays = Math.max(shortDays + optimalDays + continuousDays, 1);
  const shortCyclesPct = Math.round((shortDays / classifiedDays) * 100);
  const optimalCyclesPct = Math.round((optimalDays / classifiedDays) * 100);
  const continuousCyclesPct = Math.round((continuousDays / classifiedDays) * 100);

  const riskAssessment = shortCyclesPct > 40 ? 'HIGH' : shortCyclesPct > 20 ? 'MEDIUM' : 'LOW';
  const riskColor = shortCyclesPct > 40 ? '#EF4444' : shortCyclesPct > 20 ? '#F59E0B' : '#10B981';

  // ─── Energy & billing derived variables ──────────────────────────────────
  const latestCompEntry = compressorData.length > 0
    ? compressorData[compressorData.length - 1]
    : null;
  const latestMonthEnergy = latestCompEntry
    ? parseFloat(latestCompEntry.monthly_energy_kwh || 0)
    : 0;
  const latestEstimatedCost = latestMonthEnergy * 17; // ₹17/kWh

  const today = new Date();
  const daysInMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
  const dayOfMonth = today.getDate() || 1;
  const projectedMonthlyCost = latestEstimatedCost > 0
    ? ((latestEstimatedCost / dayOfMonth) * daysInMonth).toFixed(0)
    : '0';

  // ─── Energy trend chart data ──────────────────────────────────────────────
  const safeEnergyData = compressorData.length > 0
    ? compressorData.map(d => Math.max(parseFloat(d.daily_energy_kwh || 0), 0))
    : [0];
  const safeEnergyLabels = compressorData.length > 0
    ? compressorData.map(d => {
        const dt = new Date(d.date);
        return `${dt.getMonth() + 1}/${dt.getDate()}`;
      })
    : ['No Data'];
  const energyChartData = {
    labels: safeEnergyLabels,
    datasets: [{
      data: safeEnergyData,
      color: (opacity = 1) => `rgba(16, 185, 129, ${opacity})`,
      strokeWidth: 2,
    }],
    legend: ['Daily kWh'],
  };
  // ──────────────────────────────────────────────────────────────────────────

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20 }}>
      {/* Top Header Row with battery & time in the right corner */}
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 10, marginBottom: 20, flexWrap: 'wrap', gap: 10 }}>
        <Text style={[styles.header, { marginTop: 0, marginBottom: 0, flex: 1, minWidth: 150 }]} numberOfLines={1} ellipsizeMode="tail">
          {device.icon} {device.name} Analytics
        </Text>
        <View style={{ flexDirection: 'row', gap: 6, flexShrink: 0 }}>
          <View style={[styles.topBadge, { backgroundColor: isOffline ? '#E5E7EB' : '#E6F4EA', borderColor: isOffline ? '#D1D5DB' : '#C2E7C9' }]}>
            <Text style={{ color: isOffline ? '#5E5E5E' : '#137333', fontSize: 11, fontWeight: 'bold' }}>
              🔋 {latestTelemetry && latestTelemetry.battery_level !== undefined && latestTelemetry.battery_level !== null ? `${parseInt(latestTelemetry.battery_level)}%` : '--'}
            </Text>
          </View>
          <View style={[styles.topBadge, { backgroundColor: isOffline ? '#E5E7EB' : '#E8F0FE', borderColor: isOffline ? '#D1D5DB' : '#D2E3FC' }]}>
            <Text style={{ color: isOffline ? '#5E5E5E' : '#1A73E8', fontSize: 11, fontWeight: 'bold' }}>
              🕒 {latestTelemetry ? new Date(latestTelemetry.timestamp.endsWith('Z') ? latestTelemetry.timestamp : latestTelemetry.timestamp + 'Z').toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '--'}
            </Text>
          </View>
        </View>
      </View>
      
      {/* Time Frame selector */}
      <Text style={styles.selectorTitle}>Time Frame</Text>
      <View style={styles.selectorRow}>
        {timeFrameOptions.map(option => (
          <TouchableOpacity
            key={option.value}
            style={[styles.selectorButton, timeFrame === option.value && styles.selectorButtonActive]}
            onPress={() => setTimeFrame(option.value)}
          >
            <Text style={[styles.selectorButtonText, timeFrame === option.value && styles.selectorButtonTextActive]}>
              {option.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* Interval selector */}
      <Text style={styles.selectorTitle}>Data Interval</Text>
      <View style={styles.selectorRow}>
        {intervalOptions.map(option => (
          <TouchableOpacity
            key={option.value}
            style={[styles.selectorButton, intervalMinutes === option.value && styles.selectorButtonActive]}
            onPress={() => setIntervalMinutes(option.value)}
          >
            <Text style={[styles.selectorButtonText, intervalMinutes === option.value && styles.selectorButtonTextActive]}>
              {option.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* Repositioned Chart Guide Info Box */}
      <View style={[styles.infoBox, { marginBottom: 16, marginTop: 10 }]}>
        <Text style={styles.infoText}>
          📊 Chart Guide:{"\n"}
          🔴 Red line: Max Limit ({effectiveMaxThreshold}°C) (dangerous if crossed).{"\n"}
          {tempMin !== null ? `🟢 Green line: Min Limit (${tempMin}°C) (dangerous if crossed).\n` : ''}
          🔵 Blue line: Actual recorded temperatures.
        </Text>
      </View>

      {/* Gemini AI Insights Summary Card */}
      <View style={styles.aiCard}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <Text style={styles.aiCardTitle}>✨ GEMINI AI INSIGHTS & ACTIONS</Text>
          {aiSummary?.status && (
            <View style={[
              styles.aiStatusBadge, 
              aiSummary.status === 'healthy' ? styles.badgeOk : (aiSummary.status === 'warning' ? styles.badgeWarning : styles.badgeAlert)
            ]}>
              <Text style={{ fontSize: 10, fontWeight: '900', color: aiSummary.status === 'healthy' ? '#065F46' : (aiSummary.status === 'warning' ? '#B06000' : '#991B1B') }}>
                {aiSummary.status.toUpperCase()}
              </Text>
            </View>
          )}
        </View>
        {loadingNewAnalytics ? (
          <ActivityIndicator color="#3B82F6" size="small" />
        ) : aiSummary ? (
          <View>
            <Text style={styles.aiAnalysisText}>{aiSummary.analysis}</Text>
            {aiSummary.action_items && aiSummary.action_items.length > 0 && (
              <View style={{ marginTop: 14 }}>
                <Text style={{ fontSize: 11, fontWeight: '800', color: '#64748B', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.5 }}>Recommended Actions:</Text>
                {aiSummary.action_items.map((item, idx) => (
                  <View key={idx} style={{ flexDirection: 'row', alignItems: 'flex-start', marginTop: 4, paddingLeft: 4 }}>
                    <Text style={{ color: '#3B82F6', fontWeight: 'bold', marginRight: 6 }}>•</Text>
                    <Text style={{ fontSize: 13, color: '#334155', flex: 1 }}>{item}</Text>
                  </View>
                ))}
              </View>
            )}
          </View>
        ) : (
          <Text style={{ color: '#64748B', fontSize: 13 }}>No AI recommendations generated yet.</Text>
        )}
      </View>

      {loadingNewAnalytics ? (
        <View style={styles.chartLoadingContainer}>
          <ActivityIndicator size="small" color="#3B82F6" />
          <Text style={{ color: '#64748B', marginTop: 8, fontSize: 12 }}>Calculating stats...</Text>
        </View>
      ) : (
        <View>
          {/* A. Compressor Efficiency Module */}
          {compressorData && compressorData.length > 0 && (
            <View style={styles.efficiencyCard}>
              <Text style={styles.cardSectionTitle}>❄️ COMPRESSOR EFFICIENCY STATS</Text>
              
              <View style={styles.statsRow}>
                <View style={styles.statBlock}>
                  <Text style={styles.statLabel}>Avg Cycles / Day</Text>
                  <Text style={styles.statValue}>{avgCyclesPerDay}</Text>
                </View>
                <View style={styles.statBlock}>
                  <Text style={styles.statLabel}>Avg Run / Cycle</Text>
                  <Text style={styles.statValue}>{avgRuntime}m</Text>
                </View>
                <View style={styles.statBlock}>
                  <Text style={styles.statLabel}>Short-Cycle Risk</Text>
                  <Text style={[styles.statValue, { color: riskColor, fontSize: 15 }]}>{riskAssessment}</Text>
                </View>
              </View>

              <Text style={styles.subSectionTitle}>Duration Histogram (Days Share)</Text>
              
              <View style={styles.histogramRow}>
                <Text style={styles.histogramLabel}>Short (&lt;12m)</Text>
                <View style={styles.progressBarBg}>
                  <View style={[styles.progressBarFill, { width: `${shortCyclesPct}%`, backgroundColor: '#EF4444' }]} />
                </View>
                <Text style={styles.histogramPct}>{shortCyclesPct}%</Text>
              </View>

              <View style={styles.histogramRow}>
                <Text style={styles.histogramLabel}>Optimal (12-24m)</Text>
                <View style={styles.progressBarBg}>
                  <View style={[styles.progressBarFill, { width: `${optimalCyclesPct}%`, backgroundColor: '#10B981' }]} />
                </View>
                <Text style={styles.histogramPct}>{optimalCyclesPct}%</Text>
              </View>

              <View style={styles.histogramRow}>
                <Text style={styles.histogramLabel}>Continuous (&gt;24m)</Text>
                <View style={styles.progressBarBg}>
                  <View style={[styles.progressBarFill, { width: `${continuousCyclesPct}%`, backgroundColor: '#F59E0B' }]} />
                </View>
                <Text style={styles.histogramPct}>{continuousCyclesPct}%</Text>
              </View>
            </View>
          )}

          {/* B. Energy Consumption & Costing */}
          {compressorData && compressorData.length > 0 && (
            <View style={styles.energyCard}>
              <Text style={styles.cardSectionTitle}>⚡ ENERGY & BILLING (₹17/kWh)</Text>

              <View style={styles.statsRow}>
                <View style={styles.statBlock}>
                  <Text style={styles.statLabel}>Month-to-Date Energy</Text>
                  <Text style={styles.statValue}>{latestMonthEnergy.toFixed(1)} kWh</Text>
                </View>
                <View style={styles.statBlock}>
                  <Text style={styles.statLabel}>Month-to-Date Cost</Text>
                  <Text style={[styles.statValue, { color: '#059669' }]}>₹{latestEstimatedCost.toFixed(0)}</Text>
                </View>
                <View style={styles.statBlock}>
                  <Text style={styles.statLabel}>Projected Bill</Text>
                  <Text style={[styles.statValue, { color: '#2563EB' }]}>₹{parseFloat(projectedMonthlyCost).toFixed(0)}</Text>
                </View>
              </View>

              {/* Energy Trend Graph */}
              <Text style={[styles.subSectionTitle, { marginTop: 12 }]}>Daily kWh Active Curve</Text>
              <ScrollView horizontal={true} showsHorizontalScrollIndicator={true}>
                <LineChart
                  data={energyChartData}
                  width={Math.max(width - 40, compressorData.length * 40)}
                  height={180}
                  yAxisSuffix=" kWh"
                  chartConfig={{
                    ...chartConfigLight,
                    propsForDots: { r: "4", strokeWidth: "1", stroke: "#10B981" }
                  }}
                  bezier
                  style={{ marginVertical: 8, borderRadius: 16 }}
                />
              </ScrollView>
            </View>
          )}
        </View>
      )}

      {loading ? (
        <View style={styles.chartLoadingContainer}>
          <ActivityIndicator size="large" color="#3B82F6" style={{ marginBottom: 12 }} />
          <Text style={{ color: '#4B5563', fontSize: 14, fontWeight: '600', textAlign: 'center' }}>
            ⚡ Fetching historical readings...
          </Text>
        </View>
      ) : (
        <View>
          {(timeFrame === 'Monthly' || timeFrame === '30D') ? (
            <View>
              {/* Temperature Extremes Chart */}
              <View style={styles.chartContainer}>
                <Text style={styles.chartTitle}>{timeFrame === 'Monthly' ? 'Monthly' : '30-Day'} Temperature Extremes</Text>
                {monthlyData.length > 0 ? (
                  <ScrollView horizontal={true} showsHorizontalScrollIndicator={true}>
                    <LineChart
                      data={monthlyChartData}
                      width={Math.max(300, width - 40, monthlyData.length * 45)}
                      height={340}
                      yAxisSuffix="°C"
                      yAxisInterval={1}
                      chartConfig={chartConfigLight}
                      bezier
                      style={{ marginVertical: 8, borderRadius: 16 }}
                    />
                  </ScrollView>
                ) : (
                  <Text style={styles.errorText}>No temperature extremes data available for this range.</Text>
                )}
              </View>

              {/* Humidity Extremes Chart */}
              <View style={[styles.chartContainer, { marginTop: 20 }]}>
                <Text style={styles.chartTitle}>{timeFrame === 'Monthly' ? 'Monthly' : '30-Day'} Humidity Extremes</Text>
                {monthlyData.length > 0 ? (
                  <ScrollView horizontal={true} showsHorizontalScrollIndicator={true}>
                    <LineChart
                      data={monthlyHumChartData}
                      width={Math.max(300, width - 40, monthlyData.length * 45)}
                      height={340}
                      yAxisSuffix="%"
                      yAxisInterval={1}
                      chartConfig={{
                        ...chartConfigLight,
                        propsForDots: { r: "3", strokeWidth: "1", stroke: "#8B5CF6" }
                      }}
                      bezier
                      style={{ marginVertical: 8, borderRadius: 16 }}
                    />
                  </ScrollView>
                ) : (
                  <Text style={styles.errorText}>No humidity extremes data available for this range.</Text>
                )}
              </View>

              {monthlyData.length > 0 && (
                <View style={styles.extremesTableContainer}>
                  <Text style={styles.extremesTableTitle}>Daily Temperature Log</Text>
                  <View style={styles.tableHeader}>
                    <Text style={[styles.tableHeaderCell, { flex: 2 }]}>Date</Text>
                    <Text style={[styles.tableHeaderCell, { flex: 1.2, textAlign: 'right', color: '#3B82F6' }]}>Min Temp</Text>
                    <Text style={[styles.tableHeaderCell, { flex: 1.2, textAlign: 'right', color: '#EF4444' }]}>Max Temp</Text>
                  </View>
                  {monthlyData.slice().reverse().map((item, index) => {
                    const minVal = parseFloat(item.temp_min);
                    const maxVal = parseFloat(item.temp_max);
                    return (
                      <View key={item.date || index} style={styles.tableRow}>
                        <Text style={[styles.tableCell, { flex: 2, fontWeight: '600' }]}>{formatDate(item.date)}</Text>
                        <Text style={[styles.tableCell, { flex: 1.2, textAlign: 'right', color: '#2563EB', fontWeight: 'bold' }]}>
                          {!isNaN(minVal) ? `${minVal.toFixed(1)}°C` : 'N/A'}
                        </Text>
                        <Text style={[styles.tableCell, { flex: 1.2, textAlign: 'right', color: '#DC2626', fontWeight: 'bold' }]}>
                          {!isNaN(maxVal) ? `${maxVal.toFixed(1)}°C` : 'N/A'}
                        </Text>
                      </View>
                    );
                  })}
                </View>
              )}
            </View>
          ) : (
            <View>
              {sampledLogs.length < 2 ? (
                <View style={styles.errorContainer}>
                  <Text style={styles.errorText}>No temperature trend data available yet.</Text>
                </View>
              ) : (
                <View style={styles.chartContainer}>
                  <Text style={styles.chartTitle}>{timeFrame} Temperature Trend</Text>
                  <ScrollView horizontal={true} showsHorizontalScrollIndicator={true}>
                    <LineChart
                      data={chartData}
                      width={Math.max(300, width - 40, sampledLogs.length * 40)}
                      height={340}
                      yAxisSuffix="°C"
                      yAxisInterval={1}
                      chartConfig={chartConfigLight}
                      bezier
                      onDataPointClick={(data) => {
                        const log = sampledLogs[data.index];
                        if (log) {
                          const d = parseDate(log.timestamp);
                          const dateStr = `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')} (${d.getMonth()+1}/${d.getDate()})`;
                          setSelectedTempPoint({ value: data.value, time: dateStr });
                        }
                      }}
                      style={{
                        marginVertical: 8,
                        borderRadius: 16
                      }}
                    />
                  </ScrollView>
                  {selectedTempPoint && (
                    <View style={styles.tooltipBadge}>
                      <Text style={styles.tooltipText}>📍 Selected Temp: {selectedTempPoint.value.toFixed(1)}°C at {selectedTempPoint.time}</Text>
                    </View>
                  )}
                </View>
              )}

              {sampledLogs.length < 2 ? (
                <View style={[styles.errorContainer, { marginTop: 20 }]}>
                  <Text style={styles.errorText}>No humidity trend data available yet.</Text>
                </View>
              ) : (
                <View style={[styles.chartContainer, { marginTop: 20 }]}>
                  <Text style={styles.chartTitle}>{timeFrame} Humidity Trend</Text>
                  <ScrollView horizontal={true} showsHorizontalScrollIndicator={true}>
                    <LineChart
                      data={humChartData}
                      width={Math.max(300, width - 40, sampledLogs.length * 40)}
                      height={340}
                      yAxisSuffix="%"
                      yAxisInterval={1}
                      chartConfig={{
                        ...chartConfigLight,
                        propsForDots: { r: "3", strokeWidth: "1", stroke: "#8B5CF6" }
                      }}
                      bezier
                      onDataPointClick={(data) => {
                        const log = sampledLogs[data.index];
                        if (log) {
                          const d = parseDate(log.timestamp);
                          const dateStr = `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')} (${d.getMonth()+1}/${d.getDate()})`;
                          setSelectedHumPoint({ value: data.value, time: dateStr });
                        }
                      }}
                      style={{
                        marginVertical: 8,
                        borderRadius: 16
                      }}
                    />
                  </ScrollView>
                  {selectedHumPoint && (
                    <View style={styles.tooltipBadge}>
                      <Text style={styles.tooltipText}>📍 Selected Hum: {selectedHumPoint.value.toFixed(1)}% at {selectedHumPoint.time}</Text>
                    </View>
                  )}
                </View>
              )}
            </View>
          )}

          {/* C. Smart Door-Open Log Section */}
          <View style={styles.doorLogContainer}>
            <TouchableOpacity 
              style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}
              onPress={() => setIsDoorCollapsed(!isDoorCollapsed)}
              activeOpacity={0.7}
            >
              <Text style={[styles.doorTitle, { marginBottom: 0 }]}>🚪 Smart Door-Open Log</Text>
              <Text style={{ fontSize: 13, fontWeight: '800', color: '#2563EB' }}>
                {isDoorCollapsed ? 'Expand ▽' : 'Collapse ▲'}
              </Text>
            </TouchableOpacity>
            <Text style={{ fontSize: 12, color: '#6B7280', marginTop: 4, marginBottom: isDoorCollapsed ? 0 : 12 }}>
              Temperature spikes (≤ 10 mins) which were suppressed from alerting and logged here.
            </Text>
            {!isDoorCollapsed && (
              <View>
                {doorLogs && doorLogs.length > 0 ? (
                  doorLogs.map(log => {
                    const openedTime = new Date(log.opened_at);
                    const durMins = parseFloat((log.duration_seconds || 0) / 60.0).toFixed(1);
                    return (
                      <View key={log.id} style={styles.doorLogRow}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.doorLogMsg}>Suppressed Spike Event</Text>
                          <Text style={styles.doorLogTime}>
                            ⏰ {openedTime.toLocaleString()}
                          </Text>
                        </View>
                        <View style={styles.durationBadge}>
                          <Text style={styles.durationText}>{durMins} mins</Text>
                        </View>
                      </View>
                    );
                  })
                ) : (
                  <Text style={{ textAlign: 'center', color: '#9CA3AF', fontSize: 13, paddingVertical: 12, fontStyle: 'italic' }}>
                    No door events or suppressed spikes registered.
                  </Text>
                )}
              </View>
            )}
          </View>

          {/* Collapsible Offline History Log */}
          {offlinePeriods && offlinePeriods.length > 0 && (
            <View style={styles.offlineContainer}>
              <TouchableOpacity 
                style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}
                onPress={() => setIsOfflineCollapsed(!isOfflineCollapsed)}
                activeOpacity={0.7}
              >
                <Text style={[styles.offlineTitle, { marginBottom: 0 }]}>⚠️ Offline History Log</Text>
                <Text style={{ fontSize: 13, fontWeight: '800', color: '#EF4444' }}>
                  {isOfflineCollapsed ? 'Expand ▽' : 'Collapse ▲'}
                </Text>
              </TouchableOpacity>
              <Text style={{ fontSize: 12, color: '#6B7280', marginTop: 4, marginBottom: isOfflineCollapsed ? 0 : 12 }}>
                Periods where the sensor stopped sending telemetry to the gateway (offline for &gt;2 mins).
              </Text>
              {!isOfflineCollapsed && offlinePeriods.map((period, index) => (
                <View key={index} style={styles.offlineRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.offlineMsg}>
                      Offline: {period.start} to {period.end}
                    </Text>
                  </View>
                  <View style={styles.durationBadge}>
                    <Text style={styles.durationText}>{period.duration_minutes} mins</Text>
                  </View>
                </View>
              ))}
            </View>
          )}

          <TouchableOpacity style={styles.exportButton} onPress={handleExportCSV} activeOpacity={0.8}>
            <Text style={styles.exportButtonText}>📥 Export CSV Audit Log</Text>
          </TouchableOpacity>

          {/* Collapsible Outage & Alert History Log */}
          <View style={styles.historyContainer}>
            <TouchableOpacity 
              style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}
              onPress={() => setIsAlertsCollapsed(!isAlertsCollapsed)}
              activeOpacity={0.7}
            >
              <Text style={[styles.historyTitle, { marginBottom: 0 }]}>📜 Outage & Alert Logs</Text>
              <Text style={{ fontSize: 13, fontWeight: '800', color: '#2563EB' }}>
                {isAlertsCollapsed ? 'Expand ▽' : 'Collapse ▲'}
              </Text>
            </TouchableOpacity>
            {!isAlertsCollapsed && (
              <View style={{ marginTop: 16 }}>
                {alertLogs.length > 0 ? (
                  alertLogs.map(alert => (
                    <View key={alert.id} style={[styles.alertRow, alert.resolved ? styles.alertResolved : styles.alertActive]}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.alertMsg}>{alert.message}</Text>
                        <Text style={styles.alertTime}>
                          ⏰ {new Date(alert.created_at.endsWith('Z') ? alert.created_at : alert.created_at + 'Z').toLocaleString()}
                        </Text>
                      </View>
                      <View style={[styles.statusBadge, alert.resolved ? styles.statusBadgeResolved : styles.statusBadgeActive]}>
                        <Text style={styles.statusText}>{alert.resolved ? 'RESOLVED' : 'ACTIVE'}</Text>
                      </View>
                    </View>
                  ))
                ) : (
                  <Text style={styles.noAlertsText}>No historical alerts or offline events found.</Text>
                )}
              </View>
            )}
          </View>
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F3F4F6' },
  topBadge: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 10,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.05)',
  },
  header: { fontSize: 24, fontWeight: 'bold', color: '#111827', marginBottom: 20 },
  selectorTitle: { fontSize: 11, fontWeight: '800', color: '#6B7280', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 },
  selectorRow: { flexDirection: 'row', gap: 8, marginBottom: 20 },
  selectorButton: { flex: 1, backgroundColor: '#FFFFFF', borderWidth: 1, borderColor: '#E5E7EB', borderRadius: 10, paddingVertical: 10, alignItems: 'center' },
  selectorButtonActive: { backgroundColor: '#3B82F6', borderColor: '#3B82F6' },
  selectorButtonText: { fontSize: 13, fontWeight: '700', color: '#4B5563' },
  selectorButtonTextActive: { color: '#FFFFFF' },
  chartLoadingContainer: { height: 240, justifyContent: 'center', alignItems: 'center', backgroundColor: '#FFFFFF', borderRadius: 24, borderWidth: 1, borderColor: '#E5E7EB' },
  chartContainer: { 
    backgroundColor: '#FFFFFF', 
    borderRadius: 24, 
    padding: 12, 
    borderWidth: 1, 
    borderColor: '#E5E7EB',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.05,
    shadowRadius: 10,
    elevation: 2,
    marginBottom: 16
  },
  chartTitle: { color: '#6B7280', fontSize: 12, fontWeight: '800', marginBottom: 14, alignSelf: 'flex-start', letterSpacing: 0.5 },
  errorContainer: { height: 240, justifyContent: 'center', alignItems: 'center', backgroundColor: '#FFFFFF', borderRadius: 24, borderWidth: 1, borderColor: '#E5E7EB', padding: 20 },
  errorText: { color: '#6B7280', fontSize: 16, fontWeight: 'bold', textAlign: 'center' },
  errorSubText: { color: '#9CA3AF', fontSize: 13, textAlign: 'center' },
  exportButton: { backgroundColor: '#10B981', borderRadius: 14, padding: 16, alignItems: 'center', marginTop: 24 },
  exportButtonText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  infoBox: {
    marginTop: 24,
    backgroundColor: '#EFF6FF',
    padding: 18,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: '#BFDBFE',
    marginBottom: 20,
  },
  infoText: {
    color: '#1E40AF',
    fontSize: 14,
    lineHeight: 22,
  },
  historyContainer: {
    marginTop: 12,
    backgroundColor: '#FFFFFF',
    borderRadius: 24,
    padding: 20,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    marginBottom: 40,
  },
  historyTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: '#111827',
    marginBottom: 16,
  },
  alertRow: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 14,
    borderRadius: 14,
    marginBottom: 10,
    borderWidth: 1,
  },
  alertActive: {
    backgroundColor: '#FEF2F2',
    borderColor: '#FCA5A5',
  },
  alertResolved: {
    backgroundColor: '#F0FDF4',
    borderColor: '#86EFAC',
  },
  alertMsg: {
    fontSize: 13,
    fontWeight: '600',
    color: '#1F2937',
    lineHeight: 18,
  },
  alertTime: {
    fontSize: 11,
    color: '#6B7280',
    marginTop: 6,
  },
  statusBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
    marginLeft: 10,
  },
  statusBadgeActive: {
    backgroundColor: '#EF4444',
  },
  statusBadgeResolved: {
    backgroundColor: '#10B981',
  },
  statusText: {
    fontSize: 10,
    fontWeight: '800',
    color: '#FFFFFF',
  },
  noAlertsText: {
    color: '#9CA3AF',
    fontSize: 13,
    textAlign: 'center',
    paddingVertical: 12,
  },
  extremesTableContainer: {
    marginTop: 20,
    backgroundColor: '#FFFFFF',
    borderRadius: 20,
    padding: 16,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    width: '100%',
  },
  extremesTableTitle: {
    fontSize: 15,
    fontWeight: '800',
    color: '#111827',
    marginBottom: 12,
  },
  tableHeader: {
    flexDirection: 'row',
    borderBottomWidth: 1,
    borderBottomColor: '#E5E7EB',
    paddingBottom: 8,
    marginBottom: 8,
  },
  tableHeaderCell: {
    fontSize: 12,
    fontWeight: '800',
    color: '#6B7280',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  tableRow: {
    flexDirection: 'row',
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#F3F4F6',
    alignItems: 'center',
  },
  tableCell: {
    fontSize: 14,
    color: '#374151',
  },
  offlineContainer: {
    marginTop: 20,
    backgroundColor: '#FFFFFF',
    borderRadius: 24,
    padding: 20,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.05,
    shadowRadius: 10,
    elevation: 2,
  },
  offlineTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: '#EF4444',
    marginBottom: 16,
  },
  offlineRow: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 14,
    borderRadius: 14,
    marginBottom: 10,
    borderWidth: 1,
    backgroundColor: '#FEF2F2',
    borderColor: '#FCA5A5',
  },
  offlineMsg: {
    fontSize: 13,
    fontWeight: '600',
    color: '#991B1B',
    lineHeight: 18,
  },
  durationBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
    backgroundColor: '#EF4444',
    marginLeft: 10,
    justifyContent: 'center',
    alignItems: 'center'
  },
  durationText: {
    fontSize: 10,
    fontWeight: '800',
    color: '#FFFFFF',
  },

  // Newly Added V2 Styles
  aiCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 24,
    padding: 20,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    marginBottom: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.05,
    shadowRadius: 10,
    elevation: 2,
  },
  aiCardTitle: {
    color: '#1E293B',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  aiStatusBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
  },
  badgeOk: {
    backgroundColor: '#D1FAE5',
    borderColor: '#A7F3D0',
    borderWidth: 1,
  },
  badgeWarning: {
    backgroundColor: '#FEF3C7',
    borderColor: '#FDE68A',
    borderWidth: 1,
  },
  badgeAlert: {
    backgroundColor: '#FEE2E2',
    borderColor: '#FCA5A5',
    borderWidth: 1,
  },
  aiAnalysisText: {
    color: '#334155',
    fontSize: 13,
    lineHeight: 20,
  },
  efficiencyCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 24,
    padding: 20,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    marginBottom: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.05,
    shadowRadius: 10,
    elevation: 2,
  },
  energyCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 24,
    padding: 20,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    marginBottom: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.05,
    shadowRadius: 10,
    elevation: 2,
  },
  cardSectionTitle: {
    color: '#0F172A',
    fontSize: 12,
    fontWeight: '800',
    letterSpacing: 0.5,
    marginBottom: 14,
  },
  subSectionTitle: {
    color: '#475569',
    fontSize: 11,
    fontWeight: '800',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginTop: 14,
    marginBottom: 8,
  },
  statsRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 10,
  },
  statBlock: {
    flex: 1,
    backgroundColor: '#F8FAFC',
    borderRadius: 14,
    padding: 12,
    borderWidth: 1,
    borderColor: '#E2E8F0',
    alignItems: 'center',
  },
  statLabel: {
    fontSize: 9,
    color: '#64748B',
    fontWeight: '700',
    marginBottom: 4,
  },
  statValue: {
    fontSize: 16,
    fontWeight: '900',
    color: '#0F172A',
  },
  histogramRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 8,
  },
  histogramLabel: {
    flex: 1.5,
    fontSize: 12,
    color: '#475569',
    fontWeight: '600',
  },
  progressBarBg: {
    flex: 3,
    height: 8,
    backgroundColor: '#E2E8F0',
    borderRadius: 4,
    overflow: 'hidden',
    marginHorizontal: 8,
  },
  progressBarFill: {
    height: '100%',
    borderRadius: 4,
  },
  histogramPct: {
    flex: 0.8,
    fontSize: 12,
    fontWeight: '700',
    color: '#1E293B',
    textAlign: 'right',
  },
  tooltipBadge: {
    backgroundColor: '#0F172A',
    borderRadius: 10,
    paddingVertical: 8,
    paddingHorizontal: 12,
    marginTop: 8,
    alignSelf: 'center',
  },
  tooltipText: {
    color: '#38BDF8',
    fontSize: 11,
    fontWeight: '800',
  },
  doorLogContainer: {
    backgroundColor: '#FFFFFF',
    borderRadius: 24,
    padding: 20,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    marginBottom: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.05,
    shadowRadius: 10,
    elevation: 2,
  },
  doorTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: '#1E293B',
    marginBottom: 16,
  },
  doorLogRow: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 12,
    borderRadius: 14,
    marginBottom: 8,
    borderWidth: 1,
    backgroundColor: '#F0FDF4',
    borderColor: '#BBF7D0',
  },
  doorLogMsg: {
    fontSize: 13,
    fontWeight: '700',
    color: '#166534',
  },
  doorLogTime: {
    fontSize: 10,
    color: '#6B7280',
    marginTop: 4,
  }
});

