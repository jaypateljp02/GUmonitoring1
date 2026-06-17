package com.groundup.monitor;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import androidx.core.app.NotificationCompat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

public class BackgroundPollingService extends Service {
    private static final String TAG = "BGPollingService";
    private static final String CHANNEL_ID = "monitoring-service-native";
    private static final String ALERT_CHANNEL_ID = "monitoring-alerts-native";
    private static final int FOREGROUND_NOTIFICATION_ID = 9999;
    
    private Thread pollingThread;
    private boolean isRunning = false;
    
    // Tracks alertId -> lastNotifiedTime (ms) for escalating notifications
    private final Map<String, Long> lastNotifiedAlerts = new HashMap<>();
    private final Map<String, Integer> notifyCounts = new HashMap<>();

    @Override
    public void onCreate() {
        super.onCreate();
        Log.i(TAG, "Service onCreate");
        createNotificationChannels();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Log.i(TAG, "Service onStartCommand");
        
        // Start foreground service with persistent status bar notification
        Notification notification = createForegroundNotification();
        startForeground(FOREGROUND_NOTIFICATION_ID, notification);
        
        isRunning = true;
        if (pollingThread == null || !pollingThread.isAlive()) {
            pollingThread = new Thread(new Runnable() {
                @Override
                public void run() {
                    pollingLoop();
                }
            });
            pollingThread.start();
        }
        
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        Log.i(TAG, "Service onDestroy");
        isRunning = false;
        if (pollingThread != null) {
            pollingThread.interrupt();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void createNotificationChannels() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) {
                // 1. Silent persistent channel for foreground service
                NotificationChannel serviceChannel = new NotificationChannel(
                        CHANNEL_ID,
                        "Monitoring Background Service",
                        NotificationManager.IMPORTANCE_LOW
                );
                serviceChannel.setDescription("Keeps monitoring polling running in the background");
                manager.createNotificationChannel(serviceChannel);
                
                // 2. High-importance channel for alerts (makes sound/heads-up)
                NotificationChannel alertChannel = new NotificationChannel(
                        ALERT_CHANNEL_ID,
                        "Sensor Alerts",
                        NotificationManager.IMPORTANCE_HIGH
                );
                alertChannel.setDescription("Critical temperature and offline alerts");
                alertChannel.enableLights(true);
                alertChannel.enableVibration(true);
                manager.createNotificationChannel(alertChannel);
            }
        }
    }

    private Notification createForegroundNotification() {
        Intent notificationIntent = new Intent(this, MainActivity.class);
        
        int pendingIntentFlags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            pendingIntentFlags |= PendingIntent.FLAG_IMMUTABLE;
        }
        
        PendingIntent pendingIntent = PendingIntent.getActivity(
                this, 0, notificationIntent, pendingIntentFlags
        );

        return new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle("Ground Up Monitoring")
                .setContentText("Polling sensors in the background...")
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentIntent(pendingIntent)
                .setOngoing(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .build();
    }

    private void pollingLoop() {
        while (isRunning) {
            try {
                checkAlerts();
            } catch (Exception e) {
                Log.e(TAG, "Error in checkAlerts loop", e);
            }
            
            try {
                Thread.sleep(15000); // Poll every 15 seconds
            } catch (InterruptedException e) {
                Log.i(TAG, "Polling thread interrupted");
                break;
            }
        }
    }

    private void checkAlerts() {
        File filesDir = getFilesDir();
        File tokenFile = new File(filesDir, "auth_token.txt");
        File urlFile = new File(filesDir, "api_url.txt");
        
        if (!tokenFile.exists() || !urlFile.exists()) {
            Log.d(TAG, "Config files do not exist yet. Waiting for login.");
            return;
        }
        
        String token = readFileContent(tokenFile).trim();
        String baseUrl = readFileContent(urlFile).trim();
        
        if (token.isEmpty() || baseUrl.isEmpty()) {
            Log.d(TAG, "Auth token or base URL is empty.");
            return;
        }

        HttpURLConnection conn = null;
        try {
            URL url = new URL(baseUrl + "/alerts?resolved=false");
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setRequestProperty("Authorization", "Bearer " + token);
            conn.setRequestProperty("Accept", "application/json");
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(10000);
            
            int status = conn.getResponseCode();
            if (status == 200) {
                BufferedReader in = new BufferedReader(new InputStreamReader(conn.getInputStream()));
                StringBuilder content = new StringBuilder();
                String line;
                while ((line = in.readLine()) != null) {
                    content.append(line);
                }
                in.close();
                
                JSONArray activeAlerts = new JSONArray(content.toString());
                processActiveAlerts(activeAlerts);
            } else {
                Log.e(TAG, "Failed checkAlerts. HTTP Status: " + status);
            }
        } catch (Exception e) {
            Log.e(TAG, "HTTP Request error during alert check", e);
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    private void processActiveAlerts(JSONArray activeAlerts) {
        try {
            long now = System.currentTimeMillis();
            long ONE_HOUR_MS = 60 * 60 * 1000;
            
            Set<String> activeIds = new HashSet<>();
            for (int i = 0; i < activeAlerts.length(); i++) {
                JSONObject alertItem = activeAlerts.getJSONObject(i);
                String alertId = String.valueOf(alertItem.get("id"));
                activeIds.add(alertId);
                
                String message = alertItem.optString("message", "A sensor has crossed critical limits.");
                boolean isOffline = message.toLowerCase().contains("offline");
                boolean isHum = message.toLowerCase().contains("humidity");
                
                // Track state
                if (!lastNotifiedAlerts.containsKey(alertId)) {
                    lastNotifiedAlerts.put(alertId, 0L);
                    notifyCounts.put(alertId, 0);
                }
                
                long lastNotified = lastNotifiedAlerts.get(alertId);
                int count = notifyCounts.get(alertId);
                
                // Notify if first time OR if 1 hour has passed
                if (count == 0 || (now - lastNotified >= ONE_HOUR_MS)) {
                    lastNotifiedAlerts.put(alertId, now);
                    int nextCount = count + 1;
                    notifyCounts.put(alertId, nextCount);
                    
                    String title;
                    if (isOffline) {
                        title = "🚨 Sensor Offline!";
                    } else if (isHum) {
                        title = "🚨 Humidity Alert!";
                    } else {
                        title = "🚨 Temperature Alert!";
                    }
                    String body = message;
                    
                    if (nextCount > 1) {
                        int hours = nextCount - 1;
                        String hoursStr = hours == 1 ? "1 hour" : hours + " hours";
                        if (isOffline) {
                            title = "🚨 Offline: " + hoursStr;
                            body = "⚠️ Still Offline: " + message + " (Unresolved for " + hoursStr + ")";
                        } else if (isHum) {
                            title = "🚨 Humidity Alert Active: " + hoursStr;
                            body = "⚠️ Critical! Unresolved for " + hoursStr + ": " + message + ". Please check it!";
                        } else {
                            title = "🚨 Alert Active: " + hoursStr;
                            body = "⚠️ Critical! Unresolved for " + hoursStr + ": " + message + ". Please check it!";
                        }
                    }
                    
                    triggerAlertNotification(alertId.hashCode(), title, body);
                }
            }
            
            // Dismiss notifications for resolved alerts
            NotificationManager manager = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (manager != null) {
                for (String alertId : lastNotifiedAlerts.keySet()) {
                    if (!activeIds.contains(alertId)) {
                        manager.cancel(alertId.hashCode());
                        Log.i(TAG, "Dismissing resolved alert notification: " + alertId);
                    }
                }
            }
            
            // Clean up resolved alerts
            lastNotifiedAlerts.keySet().retainAll(activeIds);
            notifyCounts.keySet().retainAll(activeIds);
            
        } catch (Exception e) {
            Log.e(TAG, "Error processing active alerts", e);
        }
    }

    private void triggerAlertNotification(int notificationId, String title, String body) {
        Log.i(TAG, "Triggering native notification: " + title);
        
        Intent notificationIntent = new Intent(this, MainActivity.class);
        int pendingIntentFlags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            pendingIntentFlags |= PendingIntent.FLAG_IMMUTABLE;
        }
        PendingIntent pendingIntent = PendingIntent.getActivity(
                this, notificationId, notificationIntent, pendingIntentFlags
        );

        Notification notification = new NotificationCompat.Builder(this, ALERT_CHANNEL_ID)
                .setContentTitle(title)
                .setContentText(body)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentIntent(pendingIntent)
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setDefaults(Notification.DEFAULT_ALL)
                .build();

        NotificationManager manager = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) {
            manager.notify(notificationId, notification);
        }
    }

    private String readFileContent(File file) {
        StringBuilder text = new StringBuilder();
        try {
            BufferedReader br = new BufferedReader(new FileReader(file));
            String line;
            while ((line = br.readLine()) != null) {
                text.append(line);
            }
            br.close();
        } catch (Exception e) {
            Log.e(TAG, "Error reading file: " + file.getName(), e);
        }
        return text.toString();
    }
}
