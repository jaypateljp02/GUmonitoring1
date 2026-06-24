package com.groundup.monitor

import android.media.MediaPlayer
import android.media.AudioAttributes
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.Promise
import java.io.IOException

class AudioPlayerModule(reactContext: ReactApplicationContext) : ReactContextBaseJavaModule(reactContext) {
    private var mediaPlayer: MediaPlayer? = null

    override fun getName(): String {
        return "AudioPlayer"
    }

    @ReactMethod
    fun play(url: String, promise: Promise) {
        try {
            mediaPlayer?.stop()
            mediaPlayer?.release()
            
            mediaPlayer = MediaPlayer().apply {
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .build()
                )
                setDataSource(url)
                prepareAsync()
                setOnPreparedListener {
                    start()
                    promise.resolve(true)
                }
                setOnErrorListener { _, what, extra ->
                    promise.reject("PLAY_ERROR", "MediaPlayer error: what=$what, extra=$extra")
                    true
                }
                setOnCompletionListener {
                    release()
                    mediaPlayer = null
                }
            }
        } catch (e: Exception) {
            promise.reject("PLAY_ERROR", e.message, e)
        }
    }

    @ReactMethod
    fun stop(promise: Promise) {
        try {
            mediaPlayer?.stop()
            mediaPlayer?.release()
            mediaPlayer = null
            promise.resolve(true)
        } catch (e: Exception) {
            promise.reject("STOP_ERROR", e.message, e)
        }
    }
}
