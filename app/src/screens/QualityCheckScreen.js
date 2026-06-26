import React, { useState, useEffect } from 'react';
import { 
  View, 
  Text, 
  StyleSheet, 
  TouchableOpacity, 
  TextInput, 
  ScrollView, 
  Alert, 
  ActivityIndicator, 
  NativeModules, 
  Platform, 
  PermissionsAndroid,
  Image
} from 'react-native';
import { productionApi, tasksApi } from '../services/api';
import { theme } from '../theme';

export default function QualityCheckScreen({ route, navigation }) {
  const [jarNumber, setJarNumber] = useState('');
  const [loading, setLoading] = useState(false);
  const [jarData, setJarData] = useState(null);

  // Form states
  const [taste, setTaste] = useState(3);
  const [umami, setUmami] = useState(3);
  const [sweetness, setSweetness] = useState(3);
  const [aroma, setAroma] = useState(3);
  const [color, setColor] = useState(3);
  const [smell, setSmell] = useState(3);
  const [notes, setNotes] = useState('');
  
  // Geolocation and Photo upload states
  const [gpsPermission, setGpsPermission] = useState(false);
  const [latitude, setLatitude] = useState(null);
  const [longitude, setLongitude] = useState(null);
  const [photoPath, setPhotoPath] = useState(null);
  const [photoUrl, setPhotoUrl] = useState(null);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Check GPS permission on mount
  useEffect(() => {
    checkGpsPermission();
  }, []);

  // Pre-fill jar number from route params if navigate from home scan
  const routeParamsJar = route?.params?.jarNumber;
  useEffect(() => {
    if (routeParamsJar) {
      setJarNumber(String(routeParamsJar));
      fetchJarDetails(parseInt(routeParamsJar));
    }
  }, [routeParamsJar]);

  const checkGpsPermission = async () => {
    if (Platform.OS === 'web') {
      setGpsPermission(true);
      return;
    }
    if (Platform.OS === 'android') {
      try {
        const granted = await PermissionsAndroid.check(
          PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION
        );
        if (granted) {
          setGpsPermission(true);
          fetchCoordinates();
        } else {
          const request = await PermissionsAndroid.request(
            PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION
          );
          const isGranted = request === PermissionsAndroid.RESULTS.GRANTED;
          setGpsPermission(isGranted);
          if (isGranted) fetchCoordinates();
        }
      } catch (err) {
        console.warn('GPS check error:', err);
        setGpsPermission(false);
      }
    }
  };

  const fetchCoordinates = async () => {
    try {
      if (NativeModules.LocationModule) {
        const pos = await NativeModules.LocationModule.getCurrentPosition();
        setLatitude(pos.latitude);
        setLongitude(pos.longitude);
      }
    } catch (e) {
      console.log('Error fetching coordinates:', e);
    }
  };

  const handleScanQR = async () => {
    try {
      const qrScanner = NativeModules.QRScanner;
      if (!qrScanner) {
        Alert.alert("Scanner Error", "Native QR Scanner module is not available.");
        return;
      }
      
      const scanResult = await qrScanner.scanQR();
      console.log("[QRScan] Raw Scanned Code in QualityCheck:", scanResult);
      
      // Parse jar number from result. e.g. "http://localhost:8004/jar.html?jar=42" or just "42"
      let jarNum = "";
      if (scanResult.includes("jar=")) {
        const parts = scanResult.split("jar=");
        jarNum = parts[parts.length - 1];
      } else if (!isNaN(scanResult.trim())) {
        jarNum = scanResult.trim();
      } else {
        const match = scanResult.match(/\d+$/);
        jarNum = match ? match[0] : scanResult;
      }
      
      if (jarNum) {
        setJarNumber(jarNum);
        fetchJarDetails(parseInt(jarNum));
      } else {
        Alert.alert("Scan Result", `Value: ${scanResult}`);
      }
    } catch (e) {
      console.log('Scan error:', e);
      Alert.alert("Scan Cancelled", e.message || "Failed to scan QR code");
    }
  };

  const fetchJarDetails = async (num) => {
    const jNum = num || parseInt(jarNumber);
    if (isNaN(jNum)) {
      Alert.alert('Invalid Input', 'Please enter a valid jar serial number.');
      return;
    }
    try {
      setLoading(true);
      // Calls production service QR resolver
      const res = await productionApi.get(`/jars/qr/${jNum}`);
      setJarData(res.data);
    } catch (e) {
      console.log('Error fetching jar metadata:', e);
      Alert.alert('Not Found', `Jar number ${jNum} was not found in the production repository.`);
      setJarData(null);
    } finally {
      setLoading(false);
    }
  };

  const handleCapturePhoto = async (fromGallery = false) => {
    try {
      if (!NativeModules.CameraModule) {
        Alert.alert('Unavailable', 'Camera module is not available.');
        return;
      }
      
      const path = fromGallery 
        ? await NativeModules.CameraModule.selectPhoto()
        : await NativeModules.CameraModule.capturePhoto();
        
      setPhotoPath(path);
      uploadPhoto(path);
    } catch (err) {
      console.log('Photo select error:', err);
    }
  };

  const uploadPhoto = async (localPath) => {
    try {
      setUploadingPhoto(true);
      setPhotoUrl(null);

      // Refresh coordinates right before uploading to attach correct geo metadata
      await fetchCoordinates();

      const formData = new FormData();
      formData.append('file', {
        uri: `file://${localPath}`,
        name: 'quality_check.jpg',
        type: 'image/jpeg'
      });
      // Append coordinates to the upload payload
      if (latitude !== null && longitude !== null) {
        formData.append('latitude', String(latitude));
        formData.append('longitude', String(longitude));
      }

      console.log('[Media] Uploading quality check photo with coordinates...');
      const res = await tasksApi.post('/media/upload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        }
      });

      setPhotoUrl(res.data.url);
      Alert.alert('Upload Success', 'Quality check photo uploaded and geotagged!');
    } catch (err) {
      console.error('[Media] Upload error:', err);
      Alert.alert('Upload Failed', 'Failed to upload and save image to DigitalOcean Spaces.');
    } finally {
      setUploadingPhoto(false);
    }
  };

  const handleSubmitQualityCheck = async () => {
    if (!gpsPermission) {
      Alert.alert('Submission Blocked', 'Quality check submissions must be geotagged. Please grant GPS location permission in settings.');
      return;
    }

    setSubmitting(true);
    try {
      // Ensure we have final precise coordinates
      await fetchCoordinates();

      const details = {
        taste,
        umami,
        sweetness,
        aroma,
        color,
        smell,
        photo_url: photoUrl,
        latitude,
        longitude,
        notes: notes.trim() || 'Passed parameters'
      };

      const body = {
        batch_id: null,
        action: 'quality_check',
        details
      };

      console.log('[Jar] Submitting timeline event:', body);
      await productionApi.post(`/${jarData.jar.jar_number}/timeline`, body);
      
      Alert.alert('Success', `Quality check recorded for Jar ${jarData.jar.jar_number}`);
      
      // Reset form
      setNotes('');
      setPhotoPath(null);
      setPhotoUrl(null);
      
      // Reload jar history
      fetchJarDetails(jarData.jar.jar_number);
    } catch (err) {
      console.error('[Jar] Submit error:', err);
      Alert.alert('Error', 'Failed to submit quality check timeline logs.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 18 }}>
      <Text style={styles.header}>Quality Check & Jar Lifecycle</Text>

      {/* GPS Warning Banner */}
      {!gpsPermission && (
        <View style={styles.warningBanner}>
          <Text style={styles.warningText}>
            ⚠️ GPS Permission Denied: Submission is blocked. Please enable location services in your device settings.
          </Text>
        </View>
      )}

      {/* Lookup section */}
      <View style={styles.card}>
        <Text style={styles.sectionTitle}>Scan or Find Jar sticker</Text>
        <View style={{ flexDirection: 'row', gap: 10 }}>
          <TextInput
            style={styles.textInput}
            value={jarNumber}
            onChangeText={setJarNumber}
            placeholder="e.g. 1 (for jar 001)"
            placeholderTextColor="#9CA3AF"
            keyboardType="numeric"
          />
          <TouchableOpacity style={styles.scanBtn} onPress={handleScanQR}>
            <Text style={{ fontSize: 16 }}>📸 Scan</Text>
          </TouchableOpacity>
        </View>
        <TouchableOpacity 
          style={styles.findBtn} 
          onPress={() => fetchJarDetails()}
          disabled={loading}
        >
          {loading ? (
            <ActivityIndicator color="#FFFFFF" size="small" />
          ) : (
            <Text style={{ color: '#FFFFFF', fontWeight: 'bold' }}>Find Lifecycle History</Text>
          )}
        </TouchableOpacity>
      </View>

      {jarData && (
        <View>
          {/* Jar Details and Gemini AI Summary */}
          <View style={styles.aiCard}>
            <Text style={styles.aiTitle}>✨ Jar {String(jarData.jar.jar_number).padStart(3, '0')} Gemini Overview</Text>
            <Text style={styles.aiText}>
              {jarData.summary || 'No lifecycle summary generated yet.'}
            </Text>
          </View>

          {/* Timeline events */}
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Timeline logs</Text>
            {jarData.timeline && jarData.timeline.length > 0 ? (
              jarData.timeline.map((log, index) => (
                <View key={log.id || index} style={styles.timelineRow}>
                  <View style={styles.timelineDot} />
                  <View style={{ flex: 1, marginLeft: 10 }}>
                    <Text style={{ color: '#111827', fontWeight: 'bold', fontSize: 13 }}>
                      {log.action.toUpperCase().replace('_', ' ')}
                    </Text>
                    <Text style={{ color: '#6B7280', fontSize: 11, marginTop: 2 }}>
                      ⏰ {new Date(log.created_at).toLocaleString()}
                    </Text>
                    {log.details && (
                      <View style={styles.detailsBox}>
                        <Text style={styles.detailsText}>
                          Taste: {log.details.taste || 'N/A'} • Umami: {log.details.umami || 'N/A'} • Aroma: {log.details.aroma || 'N/A'}
                        </Text>
                        {log.details.notes && (
                          <Text style={[styles.detailsText, { fontStyle: 'italic', marginTop: 4, color: '#0284C7' }]}>
                            "{log.details.notes}"
                          </Text>
                        )}
                        {log.details.latitude && (
                          <Text style={{ color: '#6B7280', fontSize: 9, marginTop: 4 }}>
                            📍 Geotagged: {log.details.latitude.toFixed(4)}, {log.details.longitude.toFixed(4)}
                          </Text>
                        )}
                        {log.details.photo_url && (
                          <Image 
                             source={{ uri: log.details.photo_url }} 
                             style={styles.timelineImage} 
                          />
                        )}
                      </View>
                    )}
                  </View>
                </View>
              ))
            ) : (
              <Text style={{ color: '#6B7280', fontSize: 13, fontStyle: 'italic' }}>
                No events recorded. Perform initial checks below.
              </Text>
            )}
          </View>

          {/* New Quality Check Form */}
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Log Quality Check</Text>
            
            {/* Score selection grid */}
            <View style={styles.grid}>
              <View style={styles.gridCol}>
                <Text style={styles.label}>Taste (1-5)</Text>
                <TextInput
                  style={styles.smallInput}
                  keyboardType="numeric"
                  value={String(taste)}
                  onChangeText={(v) => setTaste(Math.min(5, Math.max(1, parseInt(v) || 3)))}
                />
              </View>
              <View style={styles.gridCol}>
                <Text style={styles.label}>Umami (1-5)</Text>
                <TextInput
                  style={styles.smallInput}
                  keyboardType="numeric"
                  value={String(umami)}
                  onChangeText={(v) => setUmami(Math.min(5, Math.max(1, parseInt(v) || 3)))}
                />
              </View>
            </View>

            <View style={styles.grid}>
              <View style={styles.gridCol}>
                <Text style={styles.label}>Sweetness (1-5)</Text>
                <TextInput
                  style={styles.smallInput}
                  keyboardType="numeric"
                  value={String(sweetness)}
                  onChangeText={(v) => setSweetness(Math.min(5, Math.max(1, parseInt(v) || 3)))}
                />
              </View>
              <View style={styles.gridCol}>
                <Text style={styles.label}>Aroma (1-5)</Text>
                <TextInput
                  style={styles.smallInput}
                  keyboardType="numeric"
                  value={String(aroma)}
                  onChangeText={(v) => setAroma(Math.min(5, Math.max(1, parseInt(v) || 3)))}
                />
              </View>
            </View>

            <View style={styles.grid}>
              <View style={styles.gridCol}>
                <Text style={styles.label}>Color (1-5)</Text>
                <TextInput
                  style={styles.smallInput}
                  keyboardType="numeric"
                  value={String(color)}
                  onChangeText={(v) => setColor(Math.min(5, Math.max(1, parseInt(v) || 3)))}
                />
              </View>
              <View style={styles.gridCol}>
                <Text style={styles.label}>Smell (1-5)</Text>
                <TextInput
                  style={styles.smallInput}
                  keyboardType="numeric"
                  value={String(smell)}
                  onChangeText={(v) => setSmell(Math.min(5, Math.max(1, parseInt(v) || 3)))}
                />
              </View>
            </View>

            <Text style={styles.label}>Quality Notes</Text>
            <TextInput
              style={[styles.textInput, { height: 60, width: '100%', marginBottom: 12 }]}
              value={notes}
              onChangeText={setNotes}
              placeholder="Flavor notes, fermentation progress details..."
              placeholderTextColor="#64748B"
              multiline
            />

            {/* Photo Attachment */}
            <Text style={styles.label}>Photo Attachment</Text>
            <View style={{ flexDirection: 'row', gap: 10, marginBottom: 16 }}>
              <TouchableOpacity 
                style={styles.photoBtn}
                onPress={() => handleCapturePhoto(false)}
                disabled={uploadingPhoto}
              >
                <Text style={{ fontSize: 13, fontWeight: '700', color: theme.colors.text }}>📸 Camera</Text>
              </TouchableOpacity>
              <TouchableOpacity 
                style={[styles.photoBtn, { backgroundColor: '#475569' }]}
                onPress={() => handleCapturePhoto(true)}
                disabled={uploadingPhoto}
              >
                <Text style={{ fontSize: 13, fontWeight: '700', color: '#FFFFFF' }}>🖼️ Gallery</Text>
              </TouchableOpacity>
            </View>

            {uploadingPhoto && (
              <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 12 }}>
                <ActivityIndicator size="small" color="#3B82F6" />
                <Text style={{ color: '#94A3B8', fontSize: 12, marginLeft: 8 }}>Uploading geotagged image...</Text>
              </View>
            )}

            {photoUrl && (
              <View style={styles.photoAttachedBadge}>
                <Text style={{ color: '#10B981', fontSize: 11, fontWeight: 'bold' }}>
                  ✓ Geotagged Photo Attached ({latitude ? `${latitude.toFixed(3)}, ${longitude.toFixed(3)}` : 'Resolving GPS...'})
                </Text>
              </View>
            )}

            {/* Submit */}
            <TouchableOpacity 
              style={[
                styles.submitBtn,
                (!gpsPermission || submitting || uploadingPhoto) && styles.submitBtnDisabled
              ]}
              onPress={handleSubmitQualityCheck}
              disabled={!gpsPermission || submitting || uploadingPhoto}
            >
              {submitting ? (
                <ActivityIndicator color="#FFFFFF" size="small" />
              ) : (
                <Text style={{ color: '#FFFFFF', fontWeight: 'bold', fontSize: 15 }}>
                  Submit Quality Check Checksheet
                </Text>
              )}
            </TouchableOpacity>
          </View>
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: theme.colors.background },
  header: { fontSize: 22, fontWeight: '800', color: theme.colors.text, marginBottom: 16 },
  warningBanner: {
    backgroundColor: '#FEF2F2',
    borderWidth: 1,
    borderColor: '#FCA5A5',
    padding: 12,
    borderRadius: 14,
    marginBottom: 16
  },
  warningText: { color: '#991B1B', fontWeight: 'bold', fontSize: 12 },
  card: {
    backgroundColor: theme.colors.surface,
    borderRadius: 20,
    padding: 16,
    borderWidth: 1,
    borderColor: theme.colors.border,
    marginBottom: 16,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.05,
    shadowRadius: 8,
    elevation: 2,
  },
  sectionTitle: { color: theme.colors.text, fontSize: 14, fontWeight: '800', marginBottom: 12, textTransform: 'uppercase', letterSpacing: 0.5 },
  label: { color: theme.colors.textSecondary, fontSize: 11, fontWeight: '800', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.5 },
  textInput: {
    flex: 1,
    backgroundColor: '#F9FAFB',
    borderRadius: 12,
    paddingHorizontal: 12,
    color: theme.colors.text,
    fontSize: 14,
    borderWidth: 1,
    borderColor: theme.colors.border,
    height: 44
  },
  scanBtn: {
    backgroundColor: '#E5E7EB',
    paddingHorizontal: 16,
    borderRadius: 12,
    justifyContent: 'center',
    alignItems: 'center',
    height: 44
  },
  scanBtnText: {
    color: theme.colors.text,
    fontWeight: 'bold',
  },
  findBtn: {
    backgroundColor: theme.colors.primary,
    borderRadius: 12,
    paddingVertical: 12,
    alignItems: 'center',
    marginTop: 12
  },
  findBtnText: {
    color: '#FFFFFF',
    fontWeight: 'bold',
  },
  aiCard: {
    backgroundColor: 'rgba(59, 130, 246, 0.05)',
    borderRadius: 20,
    padding: 16,
    borderWidth: 1,
    borderColor: 'rgba(59, 130, 246, 0.15)',
    marginBottom: 16
  },
  aiTitle: { color: '#0284C7', fontSize: 13, fontWeight: '800', marginBottom: 8 },
  aiText: { color: theme.colors.text, fontSize: 13, lineHeight: 18 },
  timelineRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 14
  },
  timelineDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: theme.colors.primary,
    marginTop: 4
  },
  detailsBox: {
    backgroundColor: '#F9FAFB',
    borderRadius: 10,
    padding: 8,
    marginTop: 6,
    borderWidth: 1,
    borderColor: theme.colors.border
  },
  timelineImage: {
    width: '100%',
    height: 160,
    borderRadius: 10,
    marginTop: 8,
    resizeMode: 'cover',
    borderWidth: 1,
    borderColor: theme.colors.border
  },
  detailsText: { color: theme.colors.textSecondary, fontSize: 11 },
  grid: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 10
  },
  gridCol: { flex: 1 },
  smallInput: {
    backgroundColor: '#F9FAFB',
    borderRadius: 10,
    color: theme.colors.text,
    fontSize: 14,
    borderWidth: 1,
    borderColor: theme.colors.border,
    paddingHorizontal: 8,
    height: 38,
    textAlign: 'center'
  },
  photoBtn: {
    flex: 1,
    backgroundColor: '#E5E7EB',
    borderRadius: 10,
    paddingVertical: 10,
    alignItems: 'center'
  },
  photoAttachedBadge: {
    backgroundColor: 'rgba(16, 185, 129, 0.05)',
    borderWidth: 1,
    borderColor: '#A7F3D0',
    padding: 10,
    borderRadius: 10,
    marginBottom: 16
  },
  submitBtn: {
    backgroundColor: '#10B981',
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center'
  },
  submitBtnDisabled: {
    backgroundColor: '#9CA3AF',
    opacity: 0.6
  }
});
