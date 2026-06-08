import React, { useState, useEffect } from 'react';
import { ActivityIndicator, View } from 'react-native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import LoginScreen from '../screens/LoginScreen';
import SensorListScreen from '../screens/SensorListScreen';
import DashboardScreen from '../screens/DashboardScreen';
import AnalyticsScreen from '../screens/AnalyticsScreen';
import { getAuthToken } from '../services/api';

const Stack = createNativeStackNavigator();

export default function AppNavigator() {
  const [isLoading, setIsLoading] = useState(true);
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  useEffect(() => {
    // Check if user has a saved session
    (async () => {
      const token = await getAuthToken();
      setIsLoggedIn(!!token);
      setIsLoading(false);
    })();
  }, []);

  if (isLoading) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#111827' }}>
        <ActivityIndicator size="large" color="#3B82F6" />
      </View>
    );
  }

  return (
    <Stack.Navigator 
      initialRouteName={isLoggedIn ? 'Main' : 'Login'}
      screenOptions={{
        headerStyle: { backgroundColor: '#1F2937' },
        headerTintColor: '#fff',
        headerTitleStyle: { fontWeight: 'bold' },
        contentStyle: { backgroundColor: '#111827' }
      }}
    >
      <Stack.Screen 
        name="Login" 
        component={LoginScreen} 
        options={{ headerShown: false }} 
      />
      <Stack.Screen 
        name="Main" 
        component={SensorListScreen} 
        options={{ title: 'Ground Up Monitor', headerBackVisible: false }} 
      />
      <Stack.Screen 
        name="DeviceDetail" 
        component={DashboardScreen} 
        options={{ title: 'Sensor Details' }} 
      />
      <Stack.Screen 
        name="Analytics" 
        component={AnalyticsScreen} 
        options={{ title: 'Analytics' }} 
      />
    </Stack.Navigator>
  );
}
