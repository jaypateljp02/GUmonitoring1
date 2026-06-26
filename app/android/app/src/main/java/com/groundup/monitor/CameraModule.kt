package com.groundup.monitor

import android.app.Activity
import android.content.Intent
import android.provider.MediaStore
import android.net.Uri
import com.facebook.react.bridge.*
import java.io.File
import java.text.SimpleDateFormat
import java.util.*

class CameraModule(reactContext: ReactApplicationContext) : ReactContextBaseJavaModule(reactContext), ActivityEventListener {
    private var pickerPromise: Promise? = null

    init {
        reactContext.addActivityEventListener(this)
    }

    override fun getName(): String {
        return "CameraModule"
    }

    @ReactMethod
    fun capturePhoto(promise: Promise) {
        val currentActivity = currentActivity
        if (currentActivity == null) {
            promise.reject("ACTIVITY_NULL", "Activity doesn't exist")
            return
        }

        pickerPromise = promise

        try {
            val cameraIntent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            currentActivity.startActivityForResult(cameraIntent, 101)
        } catch (e: Exception) {
            promise.reject("ERROR", "Failed to start camera: " + e.message)
        }
    }

    @ReactMethod
    fun selectPhoto(promise: Promise) {
        val currentActivity = currentActivity
        if (currentActivity == null) {
            promise.reject("ACTIVITY_NULL", "Activity doesn't exist")
            return
        }

        pickerPromise = promise

        try {
            val galleryIntent = Intent(Intent.ACTION_PICK, MediaStore.Images.Media.EXTERNAL_CONTENT_URI)
            currentActivity.startActivityForResult(galleryIntent, 102)
        } catch (e: Exception) {
            promise.reject("ERROR", "Failed to start gallery: " + e.message)
        }
    }

    override fun onActivityResult(activity: Activity?, requestCode: Int, resultCode: Int, data: Intent?) {
        val promise = pickerPromise ?: return

        if (resultCode != Activity.RESULT_OK) {
            promise.reject("CANCELLED", "Action cancelled by user")
            pickerPromise = null
            return
        }

        try {
            if (requestCode == 101) {
                // Handle camera thumbnail capture
                val extras = data?.extras
                val imageBitmap = extras?.get("data") as? android.graphics.Bitmap
                if (imageBitmap != null) {
                    val timeStamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(Date())
                    val tempFile = File.createTempFile("JPEG_${timeStamp}_", ".jpg", activity?.cacheDir)
                    val out = java.io.FileOutputStream(tempFile)
                    imageBitmap.compress(android.graphics.Bitmap.CompressFormat.JPEG, 90, out)
                    out.flush()
                    out.close()
                    promise.resolve(tempFile.absolutePath)
                } else {
                    promise.reject("BITMAP_NULL", "Failed to capture image data")
                }
            } else if (requestCode == 102) {
                // Handle gallery Uri selection
                val selectedImageUri = data?.data
                if (selectedImageUri != null) {
                    // Resolve content URI to a temp file in cache to allow easy form-data uploads
                    val contentResolver = activity?.contentResolver
                    val inputStream = contentResolver?.openInputStream(selectedImageUri)
                    if (inputStream != null) {
                        val timeStamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(Date())
                        val tempFile = File.createTempFile("JPEG_${timeStamp}_", ".jpg", activity.cacheDir)
                        val outputStream = java.io.FileOutputStream(tempFile)
                        inputStream.copyTo(outputStream)
                        outputStream.close()
                        inputStream.close()
                        promise.resolve(tempFile.absolutePath)
                    } else {
                        promise.reject("STREAM_NULL", "Failed to read gallery image stream")
                    }
                } else {
                    promise.reject("URI_NULL", "No image URI returned from gallery")
                }
            }
        } catch (e: Exception) {
            promise.reject("ERROR", "Failed to process photo result: " + e.message)
        } finally {
            pickerPromise = null
        }
    }

    override fun onNewIntent(intent: Intent?) {}
}
