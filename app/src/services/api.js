import axios from 'axios';

import { Platform } from 'react-native';

// For Android Emulator localhost is 10.0.2.2, for web/iOS it's localhost
const HOST = Platform.OS === 'android' ? '10.0.2.2' : 'localhost';
export const API_URL = `http://${HOST}:8000`;

export const api = axios.create({
  baseURL: API_URL,
});

api.interceptors.request.use(async (config) => {
  try {
    // We are not using SecureStore fully in this quick POC, 
    // but here's where token logic goes.
    const token = await getAuthToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  } catch (e) { }
  return config;
});

// For demonstration, a simple memory token store 
let memoryToken = null;
export const setAuthToken = (token) => { memoryToken = token; };
export const getAuthToken = async () => { return memoryToken; };
