import { useEffect } from 'react';
import { Alert, Platform, StatusBar, PermissionsAndroid } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import AppNavigator from './src/navigation/AppNavigator';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { requestNotificationPermissions, triggerLocalNotification } from './src/services/notificationService';
import { api, getAuthToken } from './src/services/api';

async function requestLocationPermission() {
  if (Platform.OS === 'android') {
    try {
      const granted = await PermissionsAndroid.request(
        PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION
      );
      if (granted === PermissionsAndroid.RESULTS.GRANTED) {
        console.log('Location permission granted');
      } else {
        console.log('Location permission denied');
      }
    } catch (err) {
      console.warn(err);
    }
  }
}

export default function App() {
  useEffect(() => {
    // Request GPS and Notification permissions on app launch
    (async () => {
      await requestLocationPermission();
      await requestNotificationPermissions();
    })();

    // Alert polling for local notifications (runs every 15 seconds)
    // Map of alertId -> lastNotifiedTime (timestamp in ms)
    let lastNotifiedAlerts = {};
    const checkAlerts = async () => {
      try {
        const token = await getAuthToken();
        if (!token) return;
        const res = await api.get('/alerts?resolved=false');
        if (res.data && res.data.length > 0) {
          const now = Date.now();
          const ONE_HOUR_MS = 60 * 60 * 1000; // 1 hour reminder interval
          
          res.data.forEach(alertItem => {
            const lastTime = lastNotifiedAlerts[alertItem.id];
            
            // Notify if it's a new alert OR if 1 hour has passed since the last notification for this alert
            if (!lastTime || (now - lastTime >= ONE_HOUR_MS)) {
              lastNotifiedAlerts[alertItem.id] = now;
              const isOffline = alertItem.message && alertItem.message.toLowerCase().includes('offline');
              triggerLocalNotification(
                isOffline ? '🚨 Sensor Offline!' : '🚨 Temperature Alert!',
                alertItem.message || 'A sensor has crossed critical limits.'
              );
            }
          });
        }
      } catch (err) {
        console.log('Error checking alerts for local notifications:', err);
      }
    };

    const alertPollInterval = setInterval(checkAlerts, 15000);
    return () => clearInterval(alertPollInterval);
  }, []);

  return (
    <SafeAreaProvider>
      <NavigationContainer>
        <StatusBar barStyle="light-content" backgroundColor="#0F172A" />
        <AppNavigator />
      </NavigationContainer>
    </SafeAreaProvider>
  );
}
