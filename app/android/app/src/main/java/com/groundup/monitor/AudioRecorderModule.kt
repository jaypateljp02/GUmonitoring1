package com.groundup.monitor

import android.media.MediaRecorder
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.Promise
import java.io.File
import java.io.IOException

class AudioRecorderModule(reactContext: ReactApplicationContext) : ReactContextBaseJavaModule(reactContext) {
    private var mediaRecorder: MediaRecorder? = null
    private var outputFile: String = ""

    override fun getName(): String {
        return "AudioRecorder"
    }

    @ReactMethod
    fun startRecording(promise: Promise) {
        try {
            val cacheDir = reactApplicationContext.cacheDir
            val file = File.createTempFile("voice_record_", ".m4a", cacheDir)
            outputFile = file.absolutePath

            mediaRecorder = MediaRecorder().apply {
                setAudioSource(MediaRecorder.AudioSource.MIC)
                setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                setOutputFile(outputFile)
                prepare()
                start()
            }
            promise.resolve(outputFile)
        } catch (e: Exception) {
            promise.reject("RECORD_ERROR", e.message, e)
        }
    }

    @ReactMethod
    fun stopRecording(promise: Promise) {
        try {
            mediaRecorder?.apply {
                stop()
                release()
            }
            mediaRecorder = null
            promise.resolve(outputFile)
        } catch (e: Exception) {
            promise.reject("STOP_ERROR", e.message, e)
        }
    }
}
