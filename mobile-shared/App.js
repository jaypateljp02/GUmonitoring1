import { useEffect, useRef } from 'react';
import { Alert, Platform, StatusBar, PermissionsAndroid, AppState } from 'react-native';
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
  const appState = useRef(AppState.currentState);

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
        
        if (res.data) {
          const now = Date.now();
          const ONE_HOUR_MS = 60 * 60 * 1000; // 1 hour reminder interval
          
          // Clean up resolved alerts from lastNotifiedAlerts map
          const activeIds = res.data.map(item => String(item.id));
          Object.keys(lastNotifiedAlerts).forEach(id => {
            if (!activeIds.includes(id)) {
              delete lastNotifiedAlerts[id];
            }
          });

          res.data.forEach(alertItem => {
            const alertId = String(alertItem.id);
            let alertState = lastNotifiedAlerts[alertId];
            
            if (!alertState) {
              alertState = {
                firstNotifiedTime: now,
                lastNotifiedTime: 0,
                notifyCount: 0
              };
              lastNotifiedAlerts[alertId] = alertState;
            }

            // Notify if it's the first time OR if 1 hour has passed since the last notification
            if (alertState.notifyCount === 0 || (now - alertState.lastNotifiedTime >= ONE_HOUR_MS)) {
              alertState.lastNotifiedTime = now;
              alertState.notifyCount += 1;
              
              const isOffline = alertItem.message && alertItem.message.toLowerCase().includes('offline');
              let title = isOffline ? '🚨 Sensor Offline!' : '🚨 Temperature Alert!';
              let message = alertItem.message || 'A sensor has crossed critical limits.';
              
              // If it's a recurring reminder, make the text dynamic and show how many hours it has been active
              if (alertState.notifyCount > 1) {
                const hours = alertState.notifyCount - 1;
                const hoursStr = hours === 1 ? '1 hour' : `${hours} hours`;
                title = isOffline ? `🚨 Offline: ${hoursStr}` : `🚨 Alert Active: ${hoursStr}`;
                message = isOffline 
                  ? `⚠️ Still Offline: ${message} (Unresolved for ${hoursStr})`
                  : `⚠️ Critical! Unresolved for ${hoursStr}: ${message}. Please check it!`;
              }

              triggerLocalNotification(title, message);
            }
          });
        }
      } catch (err) {
        console.log('Error checking alerts for local notifications:', err);
      }
    };

    // Check alerts immediately on app start (don't wait 15 seconds)
    checkAlerts();
    const alertPollInterval = setInterval(checkAlerts, 15000);

    // Also check alerts when app comes back from background
    const appStateSubscription = AppState.addEventListener('change', nextAppState => {
      if (appState.current.match(/inactive|background/) && nextAppState === 'active') {
        console.log('App came to foreground — checking alerts');
        checkAlerts();
      }
      appState.current = nextAppState;
    });

    return () => {
      clearInterval(alertPollInterval);
      appStateSubscription.remove();
    };
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
