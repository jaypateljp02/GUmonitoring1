import axios from 'axios';

// Public cloud backend - works from anywhere in the world
export const API_URL = 'https://gumonitoring.onrender.com';

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
