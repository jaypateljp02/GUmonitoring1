package com.groundup.monitor

import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.content.Context
import android.os.Bundle
import com.facebook.react.bridge.*

class LocationModule(reactContext: ReactApplicationContext) : ReactContextBaseJavaModule(reactContext) {
    private val locationManager = reactContext.getSystemService(Context.LOCATION_SERVICE) as LocationManager

    override fun getName(): String {
        return "LocationModule"
    }

    @ReactMethod
    fun getCurrentPosition(promise: Promise) {
        try {
            val hasGps = locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)
            val hasNetwork = locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)

            if (!hasGps && !hasNetwork) {
                promise.reject("LOCATION_DISABLED", "Location services are disabled on this device.")
                return
            }

            val provider = if (hasGps) LocationManager.GPS_PROVIDER else LocationManager.NETWORK_PROVIDER
            
            // Check permissions dynamically (handled by Android security context)
            val location = locationManager.getLastKnownLocation(provider)
            if (location != null) {
                val map = Arguments.createMap()
                map.putDouble("latitude", location.latitude)
                map.putDouble("longitude", location.longitude)
                promise.resolve(map)
                return
            }

            // Fallback: request a single update
            locationManager.requestSingleUpdate(provider, object : LocationListener {
                override fun onLocationChanged(loc: Location) {
                    val map = Arguments.createMap()
                    map.putDouble("latitude", loc.latitude)
                    map.putDouble("longitude", loc.longitude)
                    promise.resolve(map)
                }
                override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
                override fun onProviderEnabled(provider: String) {}
                override fun onProviderDisabled(provider: String) {}
            }, null)

        } catch (e: SecurityException) {
            promise.reject("PERMISSION_DENIED", "GPS location permission has been denied: " + e.message)
        } catch (e: Exception) {
            promise.reject("ERROR", "Failed to retrieve location: " + e.message)
        }
    }
}
