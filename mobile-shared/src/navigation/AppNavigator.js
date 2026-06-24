import React, { useState, useEffect } from 'react';
import { ActivityIndicator, View, Text } from 'react-native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';

// Import Screens
import LoginScreen from '../screens/LoginScreen';
import SensorListScreen from '../screens/SensorListScreen'; 
import FloorPlanScreen from '../screens/FloorPlanScreen';
import DashboardScreen from '../screens/DashboardScreen'; 
import AnalyticsScreen from '../screens/AnalyticsScreen';
import TapoPlugsScreen from '../screens/TapoPlugsScreen';
import ChatScreen from '../screens/ChatScreen';
import QualityCheckScreen from '../screens/QualityCheckScreen';

import { api, getAuthToken, clearAuthToken } from '../services/api';

const Stack = createNativeStackNavigator();
const Tab = createBottomTabNavigator();

function MainTabNavigator() {
  return (
    <Tab.Navigator
      screenOptions={{
        tabBarStyle: { backgroundColor: '#0F172A', borderTopColor: '#1E293B', height: 60, paddingBottom: 8, paddingTop: 8 },
        tabBarActiveTintColor: '#3B82F6',
        tabBarInactiveTintColor: '#94A3B8',
        headerShown: false,
      }}
    >
      <Tab.Screen 
        name="Map" 
        component={FloorPlanScreen} 
        options={{
          tabBarLabel: 'Facility Map',
          tabBarIcon: ({ color }) => <Text style={{ color, fontSize: 20 }}>🗺️</Text>,
        }}
      />
      <Tab.Screen 
        name="List" 
        component={SensorListScreen} 
        options={{
          tabBarLabel: 'Sensors List',
          tabBarIcon: ({ color }) => <Text style={{ color, fontSize: 20 }}>📋</Text>,
        }}
      />
      <Tab.Screen 
        name="Chats" 
        component={ChatScreen} 
        options={{
          tabBarLabel: 'Chats',
          tabBarIcon: ({ color }) => <Text style={{ color, fontSize: 20 }}>💬</Text>,
        }}
      />
      <Tab.Screen 
        name="Quality" 
        component={QualityCheckScreen} 
        options={{
          tabBarLabel: 'Quality Checks',
          tabBarIcon: ({ color }) => <Text style={{ color, fontSize: 20 }}>📦</Text>,
        }}
      />
      <Tab.Screen 
        name="Plugs" 
        component={TapoPlugsScreen} 
        options={{
          tabBarLabel: 'Tapo Plugs',
          tabBarIcon: ({ color }) => <Text style={{ color, fontSize: 20 }}>🔌</Text>,
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
      if (token) {
        try {
          await api.get('/alerts');
          setIsLoggedIn(true);
        } catch (err) {
          if (err.response && err.response.status === 401) {
            console.log('Stored token is expired/invalid. Clearing session.');
            await clearAuthToken();
            setIsLoggedIn(false);
          } else {
            setIsLoggedIn(true);
          }
        }
      } else {
        setIsLoggedIn(false);
      }
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
        contentStyle: { backgroundColor: '#F3F4F6' }
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
