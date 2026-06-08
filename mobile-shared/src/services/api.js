import axios from 'axios';
import AsyncStorage from '@react-native-async-storage/async-storage';

const AUTH_KEY = '@gu_auth_token';

// Public cloud backend - works from anywhere in the world
export const API_URL = 'https://gumonitoring.onrender.com';

export const api = axios.create({
  baseURL: API_URL,
});

api.interceptors.request.use(async (config) => {
  try {
    const token = await getAuthToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  } catch (e) { }
  return config;
});

// Persistent token storage using AsyncStorage
export const setAuthToken = async (token) => {
  try {
    await AsyncStorage.setItem(AUTH_KEY, token);
  } catch (e) {
    console.log('Failed to save auth token:', e);
  }
};

export const getAuthToken = async () => {
  try {
    return await AsyncStorage.getItem(AUTH_KEY);
  } catch (e) {
    return null;
  }
};

export const clearAuthToken = async () => {
  try {
    await AsyncStorage.removeItem(AUTH_KEY);
  } catch (e) {
    console.log('Failed to clear auth token:', e);
  }
};
