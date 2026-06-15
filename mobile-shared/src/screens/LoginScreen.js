import React, { useState, useEffect } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, Alert } from 'react-native';
import { api, setAuthToken, getApiUrl, setApiUrl } from '../services/api';

export default function LoginScreen({ navigation }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [apiUrl, setApiUrlState] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [logoPressCount, setLogoPressCount] = useState(0);
  const [showDevMode, setShowDevMode] = useState(false);

  useEffect(() => {
    const loadSavedUrl = async () => {
      const saved = await getApiUrl();
      setApiUrlState(saved);
    };
    loadSavedUrl();
  }, []);

  const handleLogoPress = () => {
    const nextCount = logoPressCount + 1;
    setLogoPressCount(nextCount);
    if (nextCount >= 5) {
      setShowDevMode(!showDevMode);
      setLogoPressCount(0);
      Alert.alert(
        "Developer Mode",
        !showDevMode 
          ? "Developer settings revealed! You can now configure the backend URL." 
          : "Developer settings hidden."
      );
    }
  };

  const handleLogin = async () => {
    try {
      setLoading(true);
      setError('');
      
      // Persist the backend URL if entered
      if (apiUrl.trim()) {
        await setApiUrl(apiUrl.trim());
      }
      
      const response = await api.post('/auth/login', { email, password });
      const { access_token } = response.data;
      await setAuthToken(access_token);
      navigation.replace('MainTabs');
    } catch (err) {
      console.log('Login error details:', err);
      if (err.response && err.response.data && err.response.data.detail) {
        setError(err.response.data.detail);
      } else {
        setError('Login failed. Please check your credentials.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.card}>
        <TouchableOpacity onPress={handleLogoPress} activeOpacity={1}>
          <Text style={styles.logo}>🏭</Text>
        </TouchableOpacity>
        <Text style={styles.title}>Ground Up</Text>
        <Text style={styles.subtitle}>Cold Room Monitoring Platform</Text>
        
        {error ? <Text style={styles.error}>{error}</Text> : null}

        <View style={styles.inputContainer}>
          <Text style={styles.inputLabel}>EMAIL ADDRESS</Text>
          <TextInput
            style={styles.input}
            placeholder="e.g. employee@groundup.in"
            placeholderTextColor="#4B5563"
            value={email}
            onChangeText={setEmail}
            autoCapitalize="none"
            keyboardType="email-address"
          />
        </View>
        
        <View style={styles.inputContainer}>
          <Text style={styles.inputLabel}>PASSWORD</Text>
          <TextInput
            style={styles.input}
            placeholder="••••••••"
            placeholderTextColor="#4B5563"
            value={password}
            onChangeText={setPassword}
            secureTextEntry
          />
        </View>

        {showDevMode && (
          <View style={styles.inputContainer}>
            <Text style={styles.inputLabel}>BACKEND API URL (DEV ONLY)</Text>
            <TextInput
              style={styles.input}
              placeholder="e.g. https://gumonitoring.onrender.com"
              placeholderTextColor="#9CA3AF"
              value={apiUrl}
              onChangeText={setApiUrlState}
              autoCapitalize="none"
              keyboardType="url"
            />
          </View>
        )}

        <TouchableOpacity style={styles.button} onPress={handleLogin} disabled={loading} activeOpacity={0.8}>
          {loading ? (
            <ActivityIndicator color="#FFFFFF" />
          ) : (
            <Text style={styles.buttonText}>Sign In</Text>
          )}
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: 24,
    justifyContent: 'center',
    backgroundColor: '#F3F4F6',
  },
  card: {
    backgroundColor: '#FFFFFF',
    borderRadius: 24,
    padding: 32,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 10 },
    shadowOpacity: 0.05,
    shadowRadius: 20,
    elevation: 4,
  },
  logo: {
    fontSize: 48,
    textAlign: 'center',
    marginBottom: 16,
  },
  title: {
    fontSize: 32,
    fontWeight: '800',
    color: '#111827',
    marginBottom: 6,
    textAlign: 'center',
    letterSpacing: 0.5,
  },
  subtitle: {
    fontSize: 14,
    color: '#6B7280',
    marginBottom: 32,
    textAlign: 'center',
    fontWeight: '500',
  },
  inputContainer: {
    marginBottom: 20,
  },
  inputLabel: {
    color: '#6B7280',
    fontSize: 11,
    fontWeight: '700',
    marginBottom: 8,
    letterSpacing: 1,
  },
  input: {
    backgroundColor: '#F9FAFB',
    borderRadius: 12,
    padding: 16,
    color: '#111827',
    fontSize: 15,
    borderWidth: 1,
    borderColor: '#E5E7EB',
  },
  button: {
    backgroundColor: '#3B82F6',
    borderRadius: 12,
    padding: 16,
    alignItems: 'center',
    marginTop: 12,
    shadowColor: '#3B82F6',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.1,
    shadowRadius: 8,
    elevation: 2,
  },
  buttonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: 'bold',
  },
  error: {
    color: '#EF4444',
    marginBottom: 20,
    textAlign: 'center',
    fontWeight: '600',
    fontSize: 14,
  },
});
