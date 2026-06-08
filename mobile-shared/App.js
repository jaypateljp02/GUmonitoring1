import { useEffect } from 'react';
import { Alert, Platform } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { NavigationContainer } from '@react-navigation/native';
import AppNavigator from './src/navigation/AppNavigator';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import * as Updates from 'expo-updates';
import * as Location from 'expo-location';

async function checkForOTAUpdate() {
  // OTA updates only work in production builds, not in dev/Expo Go
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
    // Silently ignore update errors — don't disrupt the user
    console.log('OTA update check failed:', e.message);
  }
}

export default function App() {
  useEffect(() => {
    checkForOTAUpdate();
    
    // Request GPS permissions on app launch
    (async () => {
      let { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        console.log('Permission to access location was denied');
      }
    })();
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
