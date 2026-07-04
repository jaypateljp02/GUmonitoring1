package com.groundup.monitor;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;

public class BootReceiver extends BroadcastReceiver {
    private static final String TAG = "BootReceiver";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent != null && intent.getAction() != null) {
            String action = intent.getAction();
            Log.i(TAG, "Received broadcast: " + action);
            if (Intent.ACTION_BOOT_COMPLETED.equals(action) || 
                "android.intent.action.QUICKBOOT_POWERON".equals(action) || 
                Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
                
                Log.i(TAG, "Starting BackgroundPollingService from BootReceiver...");
                Intent serviceIntent = new Intent(context, BackgroundPollingService.class);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    context.startForegroundService(serviceIntent);
                } else {
                    context.startService(serviceIntent);
                }
            }
        }
    }
}
