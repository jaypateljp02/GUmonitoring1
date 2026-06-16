import axios from 'axios';
import AsyncStorage from '@react-native-async-storage/async-storage';
import RNFS from 'react-native-fs';

const AUTH_KEY = '@gu_auth_token';
const API_URL_KEY = '@gu_api_url';

export const DEFAULT_API_URL = 'https://gumonitoring.onrender.com';

export const getApiUrl = async () => {
  try {
    const saved = await AsyncStorage.getItem(API_URL_KEY);
    return saved || DEFAULT_API_URL;
  } catch (e) {
    return DEFAULT_API_URL;
  }
};

export const setApiUrl = async (url) => {
  try {
    await AsyncStorage.setItem(API_URL_KEY, url);
    api.defaults.baseURL = url;
    await RNFS.writeFile(RNFS.DocumentDirectoryPath + '/api_url.txt', url, 'utf8');
  } catch (e) {}
};

export const api = axios.create({
  baseURL: DEFAULT_API_URL,
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
    await RNFS.writeFile(RNFS.DocumentDirectoryPath + '/auth_token.txt', token, 'utf8');
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
    const tokenPath = RNFS.DocumentDirectoryPath + '/auth_token.txt';
    const exists = await RNFS.exists(tokenPath);
    if (exists) {
      await RNFS.unlink(tokenPath);
    }
  } catch (e) {
    console.log('Failed to clear auth token:', e);
  }
};

// Initialize baseURL from AsyncStorage on startup
getApiUrl().then(url => {
  api.defaults.baseURL = url;
}).catch(() => {});
