import notifee, { AndroidImportance } from '@notifee/react-native';
import { Platform } from 'react-native';

export const requestNotificationPermissions = async () => {
  if (Platform.OS === 'web') return false;

  try {
    const settings = await notifee.requestPermission();
    if (settings.authorizationStatus) {
      console.log('Notification permission granted');
      return true;
    } else {
      console.log('Notification permission denied');
      return false;
    }
  } catch (e) {
    console.log('Failed to request notification permission:', e);
    return false;
  }
};

// Send a local notification instantly
export const triggerLocalNotification = async (title, body, data = {}) => {
  try {
    // Request permission if not already granted
    await notifee.requestPermission();

    // Create a channel (required for Android)
    const channelId = await notifee.createChannel({
      id: 'default',
      name: 'Default Channel',
      importance: AndroidImportance.HIGH,
    });

    // Display the notification
    await notifee.displayNotification({
      title,
      body,
      data,
      android: {
        channelId,
        importance: AndroidImportance.HIGH,
        pressAction: {
          id: 'default',
        },
      },
    });
  } catch (e) {
    console.log('Failed to trigger local notification:', e);
  }
};
