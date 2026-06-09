import React, { useState, useEffect } from 'react';
import { ActivityIndicator, View, Text } from 'react-native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';

// Import Screens
import LoginScreen from '../screens/LoginScreen';
import SensorListScreen from '../screens/SensorListScreen'; 
import DashboardScreen from '../screens/DashboardScreen'; 
import AnalyticsScreen from '../screens/AnalyticsScreen';
import FloorPlanScreen from '../screens/FloorPlanScreen';
import AlertsScreen from '../screens/AlertsScreen'; 

import { getAuthToken } from '../services/api';

const Stack = createNativeStackNavigator();
const Tab = createBottomTabNavigator();

function MainTabNavigator() {
  return (
    <Tab.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: '#0F172A', borderBottomWidth: 1, borderBottomColor: '#1E293B' },
        headerTintColor: '#FFFFFF',
        headerTitleStyle: { fontWeight: '800', fontSize: 18, letterSpacing: 0.5 },
        tabBarStyle: { backgroundColor: '#0F172A', borderTopColor: '#1E293B', paddingBottom: 6, height: 60 },
        tabBarActiveTintColor: '#3B82F6',
        tabBarInactiveTintColor: '#64748B',
      }}
    >
      <Tab.Screen 
        name="Map" 
        component={FloorPlanScreen} 
        options={{ 
          title: 'Facility Map', 
          headerShown: false,
          tabBarIcon: ({ color }) => (
            <Text style={{ fontSize: 20, color }}>🗺️</Text>
          ) 
        }} 
      />
      <Tab.Screen 
        name="Sensors" 
        component={SensorListScreen} 
        options={{ 
          title: 'All Sensors', 
          headerShown: false,
          tabBarIcon: ({ color }) => (
            <Text style={{ fontSize: 20, color }}>🌡️</Text>
          ) 
        }} 
      />
      <Tab.Screen 
        name="Alerts" 
        component={AlertsScreen} 
        options={{ 
          title: 'System Alerts', 
          headerShown: false,
          tabBarIcon: ({ color }) => (
            <Text style={{ fontSize: 20, color }}>⚠️</Text>
          ) 
        }} 
      />
    </Tab.Navigator>
  );
}

export default function AppNavigator() {
  const [isLoading, setIsLoading] = useState(true);
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  useEffect(() => {
    (async () => {
      const token = await getAuthToken();
      setIsLoggedIn(!!token);
      setIsLoading(false);
    })();
  }, []);

  if (isLoading) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#0F172A' }}>
        <ActivityIndicator size="large" color="#3B82F6" />
      </View>
    );
  }

  return (
    <Stack.Navigator 
      initialRouteName={isLoggedIn ? 'MainTabs' : 'Login'}
      screenOptions={{
        headerStyle: { backgroundColor: '#0F172A', borderBottomWidth: 1, borderBottomColor: '#1E293B' },
        headerTintColor: '#FFFFFF',
        headerTitleStyle: { fontWeight: '800', letterSpacing: 0.5 },
        contentStyle: { backgroundColor: '#0F172A' }
      }}
    >
      <Stack.Screen 
        name="Login" 
        component={LoginScreen} 
        options={{ headerShown: false }} 
      />
      <Stack.Screen 
        name="MainTabs" 
        component={MainTabNavigator} 
        options={{ headerShown: false }} 
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
