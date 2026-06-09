import React, { useState, useEffect } from 'react';
import { ActivityIndicator, View } from 'react-native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';

// Import Screens
import LoginScreen from '../screens/LoginScreen';
import SensorListScreen from '../screens/SensorListScreen'; // We can use this as 'Lists' tab
import DashboardScreen from '../screens/DashboardScreen'; // Detail View
import AnalyticsScreen from '../screens/AnalyticsScreen';
import FloorPlanScreen from '../screens/FloorPlanScreen';
// import AlertsScreen from '../screens/AlertsScreen'; // TODO: build later

import { getAuthToken } from '../services/api';

const Stack = createNativeStackNavigator();
const Tab = createBottomTabNavigator();

function MainTabNavigator() {
  return (
    <Tab.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: '#1F2937' },
        headerTintColor: '#fff',
        headerTitleStyle: { fontWeight: 'bold' },
        tabBarStyle: { backgroundColor: '#1F2937', borderTopColor: '#374151' },
        tabBarActiveTintColor: '#3B82F6',
        tabBarInactiveTintColor: '#9CA3AF',
      }}
    >
      <Tab.Screen 
        name="Map" 
        component={FloorPlanScreen} 
        options={{ title: 'Facility Map', tabBarIcon: () => <View style={{width: 20, height: 20, backgroundColor: '#3B82F6', borderRadius: 4}} /> }} 
      />
      <Tab.Screen 
        name="Sensors" 
        component={SensorListScreen} 
        options={{ title: 'All Sensors', tabBarIcon: () => <View style={{width: 20, height: 20, backgroundColor: '#10B981', borderRadius: 4}} /> }} 
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
      <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#111827' }}>
        <ActivityIndicator size="large" color="#3B82F6" />
      </View>
    );
  }

  return (
    <Stack.Navigator 
      initialRouteName={isLoggedIn ? 'MainTabs' : 'Login'}
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
