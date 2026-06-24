import { NativeModules, Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { authApi, tasksApi } from './api';

const MUTE_STATE_KEY = '@gu_audio_mute';

export const isMuted = async () => {
  try {
    const val = await AsyncStorage.getItem(MUTE_STATE_KEY);
    return val === 'true';
  } catch (e) {
    return false;
  }
};

export const setMuted = async (muted) => {
  try {
    await AsyncStorage.setItem(MUTE_STATE_KEY, String(muted));
  } catch (e) {}
};

export const playAudio = async (url) => {
  try {
    const muted = await isMuted();
    if (muted) {
      console.log('[AudioService] Mute active, ignoring play for:', url);
      return;
    }

    console.log('[AudioService] Playing audio:', url);
    if (Platform.OS === 'web') {
      const audio = new Audio(url);
      await audio.play();
    } else {
      if (NativeModules.AudioPlayer) {
        await NativeModules.AudioPlayer.play(url);
      } else {
        console.warn('[AudioService] Native AudioPlayer module is not available');
      }
    }
  } catch (err) {
    console.error('[AudioService] Error playing audio:', err);
  }
};

export const stopAudio = async () => {
  try {
    if (Platform.OS === 'web') {
      // Optional web stop
    } else {
      if (NativeModules.AudioPlayer) {
        await NativeModules.AudioPlayer.stop();
      }
    }
  } catch (err) {
    console.error('[AudioService] Error stopping audio:', err);
  }
};

export const announcePendingTasks = async () => {
  try {
    const profileRes = await authApi.get('/users/profile');
    const locale = profileRes.data.preferred_locale || 'en';

    const tasksRes = await tasksApi.get('/tasks?status=pending');
    const tasks = tasksRes.data;
    if (tasks.length === 0) {
      return;
    }

    const taskTitles = tasks.map(t => t.title).join(', ');
    let message = '';
    if (locale === 'mr') {
      message = `तुमच्याकडे ${tasks.length} प्रलंबित कामे आहेत: ${taskTitles}`;
    } else if (locale === 'hi') {
      message = `आपके पास ${tasks.length} लंबित कार्य हैं: ${taskTitles}`;
    } else if (locale === 'bn') {
      message = `আপনার কাছে ${tasks.length}টি মুলতুবি কাজ আছে: ${taskTitles}`;
    } else {
      message = `You have ${tasks.length} pending tasks: ${taskTitles}`;
    }

    const ttsUrl = `${tasksApi.defaults.baseURL}/ai/tts?text=${encodeURIComponent(message)}&lang=${locale}`;
    await playAudio(ttsUrl);
  } catch (err) {
    console.error('[AudioService] Failed to announce pending tasks:', err);
  }
};
