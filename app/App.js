import { useEffect } from 'react';
import { Platform, StatusBar, PermissionsAndroid } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import AppNavigator from './src/navigation/AppNavigator';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import RNFS from 'react-native-fs';

import { requestNotificationPermissions } from './src/services/notificationService';
import { getAuthToken, getApiUrl } from './src/services/api';

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

import { announcePendingTasks } from './src/services/audioService';

export default function App() {
  useEffect(() => {
    // Request GPS and Notification permissions, then sync config to native files
    (async () => {
      await requestLocationPermission();
      await requestNotificationPermissions();

      try {
        const token = await getAuthToken();
        if (token) {
          await RNFS.writeFile(RNFS.DocumentDirectoryPath + '/auth_token.txt', token, 'utf8');
        } else {
          const tokenPath = RNFS.DocumentDirectoryPath + '/auth_token.txt';
          if (await RNFS.exists(tokenPath)) {
            await RNFS.unlink(tokenPath);
          }
        }
        
        const url = await getApiUrl();
        await RNFS.writeFile(RNFS.DocumentDirectoryPath + '/api_url.txt', url, 'utf8');
      } catch (e) {
        console.log('Error syncing config to files for native service:', e);
      }
    })();
  }, []);

  // Set up hourly task announcer
  useEffect(() => {
    // Perform an initial check on start after 5 seconds delay, then every hour
    const timer = setTimeout(async () => {
      const token = await getAuthToken();
      if (token) {
        announcePendingTasks();
      }
    }, 5000);

    const interval = setInterval(async () => {
      const token = await getAuthToken();
      if (token) {
        announcePendingTasks();
      }
    }, 60 * 60 * 1000);

    return () => {
      clearTimeout(timer);
      clearInterval(interval);
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
