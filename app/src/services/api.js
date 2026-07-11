import axios from 'axios';
import AsyncStorage from '@react-native-async-storage/async-storage';
import RNFS from 'react-native-fs';

const AUTH_KEY = '@gu_auth_token';
const API_URL_KEY = '@gu_api_url';

export const AUTH_URL = 'https://gu-production.initiativesewafoundation.com';
export const PRODUCTION_URL = 'https://gu-production.initiativesewafoundation.com';
export const TASKS_URL = 'https://gu-task.initiativesewafoundation.com';
export const MONITORING_URL = 'https://gu-monitoring.initiativesewafoundation.com';
export const ADMIN_URL = 'https://gu-factory.initiativesewafoundation.com';
export const DEFAULT_API_URL = 'https://gu-monitoring.initiativesewafoundation.com';

export const getApiUrl = async () => {
  try {
    const saved = await AsyncStorage.getItem(API_URL_KEY);
    return saved || DEFAULT_API_URL;
  } catch (e) {
    return DEFAULT_API_URL;
  }
};

export const getServiceUrls = (currentUrl) => {
  let authUrl = currentUrl;
  let productionUrl = currentUrl;
  let tasksUrl = currentUrl;
  let monitoringUrl = currentUrl;
  let adminUrl = currentUrl;

  if (currentUrl.includes('monitoring-dot-')) {
    authUrl = currentUrl.replace('monitoring-dot-', '');
    productionUrl = currentUrl.replace('monitoring-dot-', 'production-dot-');
    tasksUrl = currentUrl.replace('monitoring-dot-', 'tasks-dot-');
    monitoringUrl = currentUrl;
    adminUrl = currentUrl.replace('monitoring-dot-', 'admin-dot-');
  } else if (currentUrl.match(/:\d+/)) {
    authUrl = currentUrl.replace(/:\d+/, ':8000');
    productionUrl = currentUrl.replace(/:\d+/, ':8001');
    tasksUrl = currentUrl.replace(/:\d+/, ':8002');
    monitoringUrl = currentUrl.replace(/:\d+/, ':8003');
    adminUrl = currentUrl.replace(/:\d+/, ':8004');
  } else {
    authUrl = AUTH_URL;
    productionUrl = PRODUCTION_URL;
    tasksUrl = TASKS_URL;
    monitoringUrl = MONITORING_URL;
    adminUrl = ADMIN_URL;
  }

  return { auth: authUrl, production: productionUrl, tasks: tasksUrl, monitoring: monitoringUrl, admin: adminUrl };
};

export const api = axios.create({ baseURL: DEFAULT_API_URL });
export const authApi = axios.create({ baseURL: AUTH_URL });
export const productionApi = axios.create({ baseURL: PRODUCTION_URL });
export const tasksApi = axios.create({ baseURL: TASKS_URL });

const addAuthInterceptor = (instance) => {
  instance.interceptors.request.use(async (config) => {
    try {
      console.log('[API Request]', config.method ? config.method.toUpperCase() : 'GET', config.baseURL + config.url);
      const token = await getAuthToken();
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
    } catch (e) {}
    return config;
  });
};

addAuthInterceptor(api);
addAuthInterceptor(authApi);
addAuthInterceptor(productionApi);
addAuthInterceptor(tasksApi);

export const setApiUrl = async (url) => {
  try {
    await AsyncStorage.setItem(API_URL_KEY, url);
    const urls = getServiceUrls(url);
    api.defaults.baseURL = urls.monitoring;
    authApi.defaults.baseURL = urls.auth;
    productionApi.defaults.baseURL = urls.production;
    tasksApi.defaults.baseURL = urls.tasks;
    await RNFS.writeFile(RNFS.DocumentDirectoryPath + '/api_url.txt', url, 'utf8');
  } catch (e) {}
};

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

// Initialize baseURLs from AsyncStorage on startup
getApiUrl().then(url => {
  const urls = getServiceUrls(url);
  api.defaults.baseURL = urls.monitoring;
  authApi.defaults.baseURL = urls.auth;
  productionApi.defaults.baseURL = urls.production;
  tasksApi.defaults.baseURL = urls.tasks;
}).catch(() => {});

