import { useEffect, useRef } from 'react';
import { Alert, Platform, StatusBar, PermissionsAndroid, AppState } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import AppNavigator from './src/navigation/AppNavigator';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { requestNotificationPermissions, triggerLocalNotification } from './src/services/notificationService';
import { api, getAuthToken } from './src/services/api';

import notifee, { AndroidImportance } from '@notifee/react-native';

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

const startForegroundService = async () => {
  if (Platform.OS === 'web') return;
  try {
    const channelId = await notifee.createChannel({
      id: 'monitoring-service',
      name: 'Monitoring Service',
      importance: AndroidImportance.LOW,
    });

    await notifee.displayNotification({
      id: 'monitoring-bg-notification',
      title: 'Ground Up Monitoring',
      body: 'Polling sensors in the background...',
      android: {
        channelId,
        asForegroundService: true,
        ongoing: true,
        pressAction: {
          id: 'default',
          launchActivity: 'default',
        },
      },
    });
    console.log('Foreground service started successfully');
  } catch (err) {
    console.log('Failed to start foreground service:', err);
  }
};

export default function App() {
  useEffect(() => {
    // Request GPS and Notification permissions, then start foreground service on app launch
    (async () => {
      await requestLocationPermission();
      const notifyGranted = await requestNotificationPermissions();
      if (notifyGranted) {
        await startForegroundService();
      }
    })();
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
