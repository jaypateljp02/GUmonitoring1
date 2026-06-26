import { NativeModules, Platform, PermissionsAndroid } from 'react-native';

let webRecorder = null;
let webChunks = [];

export const requestRecordPermission = async () => {
  if (Platform.OS === 'web') {
    try {
      await navigator.mediaDevices.getUserMedia({ audio: true });
      return true;
    } catch (e) {
      return false;
    }
  }

  if (Platform.OS === 'android') {
    try {
      const granted = await PermissionsAndroid.request(
        PermissionsAndroid.PERMISSIONS.RECORD_AUDIO,
        {
          title: 'Microphone Permission',
          message: 'Ground Up needs access to your microphone to record voice commands and messages.',
          buttonNeutral: 'Ask Me Later',
          buttonNegative: 'Cancel',
          buttonPositive: 'OK',
        }
      );
      return granted === PermissionsAndroid.RESULTS.GRANTED;
    } catch (err) {
      console.warn(err);
      return false;
    }
  }
  return true;
};

export const startRecording = async () => {
  const hasPermission = await requestRecordPermission();
  if (!hasPermission) {
    throw new Error('Microphone permission denied');
  }

  if (Platform.OS === 'web') {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    webChunks = [];
    webRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    webRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) {
        webChunks.push(e.data);
      }
    };
    webRecorder.start();
    return 'web-blob';
  } else {
    if (NativeModules.AudioRecorder) {
      return await NativeModules.AudioRecorder.startRecording();
    } else {
      throw new Error('Native AudioRecorder module is not available');
    }
  }
};

export const stopRecording = async () => {
  if (Platform.OS === 'web') {
    return new Promise((resolve, reject) => {
      if (!webRecorder) {
        reject(new Error('Recorder not started'));
        return;
      }
      webRecorder.onstop = () => {
        const blob = new Blob(webChunks, { type: 'audio/webm' });
        resolve({ blob, uri: URL.createObjectURL(blob) });
      };
      webRecorder.stop();
      webRecorder.stream.getTracks().forEach(track => track.stop());
    });
  } else {
    if (NativeModules.AudioRecorder) {
      const filePath = await NativeModules.AudioRecorder.stopRecording();
      return { uri: `file://${filePath}` };
    } else {
      throw new Error('Native AudioRecorder module is not available');
    }
  }
};
