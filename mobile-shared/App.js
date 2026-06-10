import { useEffect } from 'react';
import { Alert, Platform } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { NavigationContainer } from '@react-navigation/native';
import AppNavigator from './src/navigation/AppNavigator';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import * as Updates from 'expo-updates';
import * as Location from 'expo-location';

import { requestNotificationPermissions, triggerLocalNotification } from './src/services/notificationService';
import { api } from './src/services/api';

async function checkForOTAUpdate() {
  if (__DEV__) return;

  try {
    const update = await Updates.checkForUpdateAsync();
    if (update.isAvailable) {
      await Updates.fetchUpdateAsync();
      Alert.alert(
        'Update Available',
        'A new version has been downloaded. The app will restart now.',
        [{ text: 'OK', onPress: () => Updates.reloadAsync() }],
      );
    }
  } catch (e) {
    console.log('OTA update check failed:', e.message);
  }
}

export default function App() {
  useEffect(() => {
    checkForOTAUpdate();
    
    // Request GPS and Notification permissions on app launch
    (async () => {
      let { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        console.log('Permission to access location was denied');
      }

      await requestNotificationPermissions();
    })();

    // Alert polling for local notifications (runs every 15 seconds)
    let notifiedAlertIds = new Set();
    const checkAlerts = async () => {
      try {
        const res = await api.get('/alerts?resolved=false');
        if (res.data && res.data.length > 0) {
          res.data.forEach(alertItem => {
            if (!notifiedAlertIds.has(alertItem.id)) {
              notifiedAlertIds.add(alertItem.id);
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
        <StatusBar style="light" />
        <AppNavigator />
      </NavigationContainer>
    </SafeAreaProvider>
  );
}
