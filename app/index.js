import { AppRegistry } from 'react-native';
import App from './App';
import notifee from '@notifee/react-native';
import { api, getAuthToken, getApiUrl } from './src/services/api';
import { triggerLocalNotification } from './src/services/notificationService';

// Map of alertId -> lastNotifiedTime/state for background notifications
let lastNotifiedAlerts = {};

async function checkAlertsBackground() {
  try {
    const token = await getAuthToken();
    if (!token) return;
    
    // Ensure baseURL is updated in case it changed in AsyncStorage
    const url = await getApiUrl();
    api.defaults.baseURL = url;

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
          
          // If it's a recurring reminder, make the text dynamic
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
    console.log('Error checking alerts in background:', err);
  }
}

// Register foreground service task
notifee.registerForegroundService((notification) => {
  return new Promise(() => {
    // Run immediate check
    checkAlertsBackground();
    // Poll every 15 seconds
    const interval = setInterval(checkAlertsBackground, 15000);
  });
});

AppRegistry.registerComponent('main', () => App);

