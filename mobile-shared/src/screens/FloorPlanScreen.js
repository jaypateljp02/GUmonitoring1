import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, Image, TouchableOpacity, Dimensions, ActivityIndicator } from 'react-native';
import { api } from '../services/api';
import { useNavigation } from '@react-navigation/native';



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

const { width, height } = Dimensions.get('window');

// Premium Floor Plan View Component
export default function FloorPlanScreen() {
  const [rooms, setRooms] = useState([]);
  const [liveData, setLiveData] = useState({});
  const [loading, setLoading] = useState(true);
  const navigation = useNavigation();

  useEffect(() => {
    const fetchData = async () => {
      try {
        // Fetch static layout (Rooms and their coordinates)
        const roomsRes = await api.get('/rooms');
        setRooms(roomsRes.data);

        // Fetch live telemetry for all sensors
        const dashboardRes = await api.get('/monitoring/dashboard');
        const telemetryMap = {};
        if (dashboardRes.data && dashboardRes.data.live_devices) {
          dashboardRes.data.live_devices.forEach(d => {
            telemetryMap[d.sensor_id] = d;
          });
        }
        setLiveData(telemetryMap);
      } catch (e) {
        console.error("Failed to load map data", e);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    const unsubscribe = navigation.addListener('focus', () => {
      fetchData();
    });
    const interval = setInterval(fetchData, 10000); // Poll every 10s for live data
    return () => {
      unsubscribe();
      clearInterval(interval);
    };
  }, [navigation]);

  const handleMarkerPress = (room) => {
    // Navigate to the specific room's detail view
    // The device logic currently relies on device_id, we'll pass the first sensor's device_id
    if (room.sensors && room.sensors.length > 0) {
      const mainSensor = room.sensors.find(s => s.type === 'temperature') || room.sensors[0];
      const mockDevice = {
        id: mainSensor.device_id || room.id,
        name: room.name,
        icon: room.type === 'fridge' ? '❄️' : (room.type === 'freezer' ? '🧊' : '🏢')
      };
      navigation.navigate('DeviceDetail', { device: mockDevice });
    }
  };

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#3B82F6" />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <Text style={styles.header}>Facility Map</Text>
      
      <View style={styles.mapWrapper}>
        <Image 
          source={require('../../assets/floorplan.jpg')} 
          style={styles.mapImage}
          resizeMode="contain"
        />

        {(() => {
          let unplacedCount = 0;
          return rooms.map(room => {
            const isPlaced = room.map_x && room.map_y;
            let left, top;
            if (isPlaced) {
              left = room.map_x;
              top = room.map_y;
            } else {
              left = `${8 + (unplacedCount * 23)}%`;
              top = '88%';
              unplacedCount++;
            }
            
            // Get live temperature for this room
            const tempSensor = room.sensors?.find(s => s.type === 'temperature');
            const data = tempSensor ? liveData[tempSensor.id] : null;
            const temp = data ? `${parseFloat(data.temperature)}°C` : '--';
            
            const latestTimeStr = data ? data.timestamp : null;
            const latestTime = latestTimeStr ? parseDate(latestTimeStr) : null;
            const isOnline = latestTime ? (new Date() - latestTime) < 2 * 60 * 1000 : false;
            const isOffline = data && !isOnline;

            // Check for alerts
            const isAlert = data && !isOffline && (
              (tempSensor.min_threshold !== null && data.temperature < tempSensor.min_threshold) ||
              (tempSensor.max_threshold !== null && data.temperature > tempSensor.max_threshold)
            );

            return (
              <TouchableOpacity 
                key={room.id}
                style={[styles.markerContainer, { left, top }]}
                onPress={() => handleMarkerPress(room)}
                activeOpacity={0.7}
              >
                {isAlert && <View style={styles.pulseRing} />}
                <View 
                  style={[
                    styles.markerBadge, 
                    isAlert && styles.markerBadgeAlert,
                    isOffline && styles.markerBadgeOffline,
                    !isPlaced && styles.markerBadgeUnplaced
                  ]}
                >
                  {!isPlaced && (
                    <View style={styles.newBadge}>
                      <Text style={styles.newBadgeText}>NEW</Text>
                    </View>
                  )}
                  {isPlaced && <View style={[styles.markerDot, isOffline && styles.markerDotOffline]} />}
                  <View>
                    <Text style={styles.markerName} numberOfLines={1} ellipsizeMode="tail">{room.name}</Text>
                    <Text style={[
                      styles.markerTemp, 
                      isAlert && styles.markerTempAlert,
                      isOffline && styles.markerTempOffline
                    ]}>
                      {temp}
                    </Text>
                  </View>
                </View>
              </TouchableOpacity>
            );
          });
        })()}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#111827',
  },
  loadingContainer: {
    flex: 1,
    backgroundColor: '#111827',
    justifyContent: 'center',
    alignItems: 'center',
  },
  header: {
    fontSize: 24,
    fontWeight: '800',
    color: '#FFFFFF',
    paddingHorizontal: 20,
    paddingTop: 60,
    paddingBottom: 16,
    letterSpacing: 0.5,
  },
  mapWrapper: {
    flex: 1,
    position: 'relative',
    marginHorizontal: 16,
    marginBottom: 24,
    borderRadius: 24,
    overflow: 'hidden',
    backgroundColor: '#1F2937',
    borderWidth: 1,
    borderColor: '#374151',
  },
  mapImage: {
    width: '100%',
    height: '100%',
    opacity: 0.8,
  },
  markerContainer: {
    position: 'absolute',
    transform: [{ translateX: -40 }, { translateY: -20 }], // Center the marker
    zIndex: 10,
  },
  markerBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(31, 41, 55, 0.85)',
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.1)',
    overflow: 'hidden',
  },
  markerBadgeAlert: {
    backgroundColor: 'rgba(153, 27, 27, 0.85)',
    borderColor: '#EF4444',
  },
  markerBadgeOffline: {
    backgroundColor: 'rgba(55, 65, 81, 0.85)',
    borderColor: 'rgba(156, 163, 175, 0.4)',
  },
  markerDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: '#3B82F6',
    marginRight: 8,
  },
  markerDotOffline: {
    backgroundColor: '#9CA3AF',
  },
  markerName: {
    color: '#9CA3AF',
    fontSize: 10,
    fontWeight: '600',
    textTransform: 'uppercase',
    maxWidth: 70,
  },
  markerBadgeUnplaced: {
    borderColor: '#F97316',
    borderStyle: 'dashed',
    borderWidth: 1.5,
    backgroundColor: 'rgba(249, 115, 22, 0.15)',
  },
  newBadge: {
    backgroundColor: '#F97316',
    paddingHorizontal: 4,
    paddingVertical: 2,
    borderRadius: 4,
    marginRight: 6,
  },
  newBadgeText: {
    color: '#FFFFFF',
    fontSize: 8,
    fontWeight: '800',
  },
  markerTemp: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: 'bold',
  },
  markerTempAlert: {
    color: '#FECACA',
  },
  markerTempOffline: {
    color: '#D1D5DB',
  },
  pulseRing: {
    position: 'absolute',
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: 'rgba(239, 68, 68, 0.3)',
    top: -10,
    left: -10,
    zIndex: -1,
  }
});
