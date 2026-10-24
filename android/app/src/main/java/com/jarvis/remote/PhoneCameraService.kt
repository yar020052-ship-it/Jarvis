package com.jarvis.remote

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.Context
import android.content.pm.ServiceInfo
import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.media.Image
import android.media.ImageReader
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import org.json.JSONObject
import java.io.ByteArrayOutputStream

/** Keeps the explicitly enabled phone camera streaming while JARVIS is in the background. */
class PhoneCameraService : Service() {

    companion object {
        const val ACTION_START = "com.jarvis.remote.camera.START"
        const val ACTION_STOP = "com.jarvis.remote.camera.STOP"
        const val ACTION_EVENT = "com.jarvis.remote.camera.EVENT"
        const val EXTRA_SERVER = "server"
        const val EXTRA_TOKEN = "token"
        const val EXTRA_DEVICE_TOKEN = "device_token"
        const val EXTRA_STATUS = "status"
        const val EXTRA_DETAIL = "detail"
        const val EXTRA_FRAME = "frame"

        @Volatile var isRunning: Boolean = false
            private set
        @Volatile var currentStatus: String = "stopped"
            private set
        private const val CHANNEL_ID = "jarvis_phone_camera"
        private const val NOTIFICATION_ID = 2241
        private const val FRAME_INTERVAL_MS = 1000L
        private const val ACK_CHECK_MS = 3000L
        private const val ACK_TIMEOUT_MS = 10000L
        private const val JPEG_QUALITY = 60
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    @Volatile private var wanted = false
    @Volatile private var server = ""
    @Volatile private var token = ""
    @Volatile private var deviceToken = ""
    @Volatile private var videoWs: WsClient? = null
    private var cameraThread: HandlerThread? = null
    private var cameraHandler: Handler? = null
    private var cameraDevice: CameraDevice? = null
    private var captureSession: CameraCaptureSession? = null
    private var imageReader: ImageReader? = null
    private var lastFrameAt = 0L
    private var lastAckAt = 0L
    private var retryCount = 0
    private var lastStatus = "stopped"
    private var lastDetail = ""

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> stopStream()
            ACTION_START -> startStream(intent)
        }
        return START_NOT_STICKY
    }

    private fun startStream(intent: Intent) {
        val newServer = intent.getStringExtra(EXTRA_SERVER).orEmpty()
        val newToken = intent.getStringExtra(EXTRA_TOKEN).orEmpty()
        val newDeviceToken = intent.getStringExtra(EXTRA_DEVICE_TOKEN).orEmpty()
        if (newServer.isBlank() || newToken.isBlank()) {
            publishStatus("error", "Нет подключения к JARVIS. Переподключите приложение.")
            stopSelf()
            return
        }
        if (wanted) return

        server = newServer
        token = newToken
        deviceToken = newDeviceToken
        wanted = true
        isRunning = true
        getSharedPreferences("jarvis_remote", MODE_PRIVATE)
            .edit().putBoolean("camera_live_enabled", true).apply()
        startAsForeground()
        publishStatus("connecting", "Подключаю камеру к JARVIS…")

        cameraThread = HandlerThread("jarvis-camera-capture").also { it.start() }
        cameraHandler = Handler(cameraThread!!.looper)
        cameraHandler?.post { openCamera() }
        connectVideo()
    }

    private fun startAsForeground() {
        val notification = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
            )
        } else {
            @Suppress("DEPRECATION")
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Камера JARVIS",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Показывает, когда камера планшета передаёт видео в JARVIS"
            setShowBadge(false)
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        val stopIntent = Intent(this, PhoneCameraService::class.java).setAction(ACTION_STOP)
        val stopPending = PendingIntent.getService(
            this,
            2,
            stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val openPending = PendingIntent.getActivity(
            this,
            1,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentTitle("Камера JARVIS активна")
            .setContentText("Планшет передаёт видео. Нажмите, чтобы открыть приложение.")
            .setContentIntent(openPending)
            .setCategory(Notification.CATEGORY_SERVICE)
            .setOngoing(true)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Остановить", stopPending)
            .build()
    }

    private fun openCamera() {
        if (!wanted || cameraDevice != null || imageReader != null) return
        try {
            val manager = getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val cameraId = manager.cameraIdList.firstOrNull { id ->
                manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) ==
                    CameraCharacteristics.LENS_FACING_FRONT
            } ?: manager.cameraIdList.firstOrNull()
                ?: throw IllegalStateException("На планшете не найдена камера")
            val characteristics = manager.getCameraCharacteristics(cameraId)
            val sizes = characteristics
                .get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                ?.getOutputSizes(ImageFormat.YUV_420_888)
                ?: throw IllegalStateException("Камера не поддерживает видеокадры")
            val size = sizes
                .filter { maxOf(it.width, it.height) <= 640 }
                .maxByOrNull { it.width * it.height }
                ?: sizes.minBy { it.width * it.height }

            val reader = ImageReader.newInstance(size.width, size.height, ImageFormat.YUV_420_888, 2)
            imageReader = reader
            val handler = cameraHandler ?: throw IllegalStateException("Поток камеры остановлен")
            reader.setOnImageAvailableListener({ source -> processImage(source) }, handler)
            manager.openCamera(cameraId, object : CameraDevice.StateCallback() {
                override fun onOpened(device: CameraDevice) {
                    if (!wanted) {
                        device.close()
                        return
                    }
                    cameraDevice = device
                    try {
                        val request = device.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW)
                        request.addTarget(reader.surface)
                        val capture = request.build()
                        device.createCaptureSession(listOf(reader.surface), object : CameraCaptureSession.StateCallback() {
                            override fun onConfigured(session: CameraCaptureSession) {
                                if (!wanted) {
                                    session.close()
                                    return
                                }
                                captureSession = session
                                try {
                                    session.setRepeatingRequest(capture, null, handler)
                                    publishStatus("waiting_frame", "Камера работает; проверяю приход кадров в JARVIS…")
                                } catch (e: Exception) {
                                    cameraFailure(e)
                                }
                            }

                            override fun onConfigureFailed(session: CameraCaptureSession) {
                                session.close()
                                cameraFailure(IllegalStateException("Не удалось настроить видеопоток камеры"))
                            }
                        }, handler)
                    } catch (e: Exception) {
                        cameraFailure(e)
                    }
                }

                override fun onDisconnected(device: CameraDevice) {
                    device.close()
                    if (cameraDevice === device) cameraDevice = null
                    if (wanted) cameraFailure(IllegalStateException("Камера отключилась"))
                }

                override fun onError(device: CameraDevice, error: Int) {
                    device.close()
                    if (cameraDevice === device) cameraDevice = null
                    if (wanted) cameraFailure(IllegalStateException("Ошибка камеры ($error)"))
                }
            }, handler)
        } catch (e: Exception) {
            cameraFailure(e)
        }
    }

    private fun processImage(source: ImageReader) {
        val image = try { source.acquireLatestImage() } catch (_: Exception) { null } ?: return
        image.use {
            if (!wanted || SystemClock.elapsedRealtime() - lastFrameAt < FRAME_INTERVAL_MS) return
            lastFrameAt = SystemClock.elapsedRealtime()
            try {
                val nv21 = imageToNv21(image)
                val output = ByteArrayOutputStream()
                val yuv = YuvImage(nv21, ImageFormat.NV21, image.width, image.height, null)
                if (!yuv.compressToJpeg(Rect(0, 0, image.width, image.height), JPEG_QUALITY, output)) return
                val frame = output.toByteArray()
                publishFrame(frame)
                videoWs?.sendBinary(frame)
            } catch (_: Exception) {
                // A bad frame should not stop the stream or the chat.
            }
        }
    }

    private fun imageToNv21(image: Image): ByteArray {
        val width = image.width
        val height = image.height
        val result = ByteArray(width * height * 3 / 2)
        val planes = image.planes
        val y = planes[0]
        val u = planes[1]
        val v = planes[2]
        val yBuffer = y.buffer
        val uBuffer = u.buffer
        val vBuffer = v.buffer
        val yBase = yBuffer.position()
        val uBase = uBuffer.position()
        val vBase = vBuffer.position()
        for (row in 0 until height) {
            val rowOffset = yBase + row * y.rowStride
            for (col in 0 until width) {
                result[row * width + col] = yBuffer.get(rowOffset + col * y.pixelStride)
            }
        }
        val chromaOffset = width * height
        for (row in 0 until height / 2) {
            for (col in 0 until width / 2) {
                val vIndex = vBase + row * v.rowStride + col * v.pixelStride
                val uIndex = uBase + row * u.rowStride + col * u.pixelStride
                result[chromaOffset + row * width + col * 2] = vBuffer.get(vIndex)
                result[chromaOffset + row * width + col * 2 + 1] = uBuffer.get(uIndex)
            }
        }
        return result
    }

    private fun cameraFailure(error: Exception) {
        if (!wanted) return
        val reason = error.message ?: "проверьте разрешение и доступность камеры"
        publishStatus("error", "Не удалось запустить камеру: $reason")
        releaseCamera()
        mainHandler.postDelayed({ if (wanted) stopStream() }, 4000L)
    }

    private fun connectVideo() {
        if (!wanted) return
        val url = Net.wsBase(server) + "/ws/phone-video?token=" + Net.token(token)
        val client = WsClient(url, object : WsClient.Listener {
            override fun onOpen() {
                mainHandler.post {
                    if (!wanted) return@post
                    retryCount = 0
                    lastAckAt = 0L
                    publishStatus("waiting_frame", "Видеоканал подключён; жду подтверждение первого кадра…")
                    scheduleAckWatchdog()
                }
            }

            override fun onMessage(text: String) {
                if (text.trim() == "frame_received") {
                    mainHandler.post {
                        if (!wanted) return@post
                        lastAckAt = SystemClock.elapsedRealtime()
                        publishStatus("frame_received", "JARVIS получил кадр с камеры планшета.")
                    }
                }
            }

            override fun onBinary(data: ByteArray) = Unit

            override fun onClosed(reason: String?) {
                mainHandler.post {
                    if (!wanted) return@post
                    videoWs = null
                    val detail = reason?.takeIf { it.isNotBlank() }?.let { "Связь с камерой прервана: $it" }
                        ?: "Связь с камерой прервана; переподключаюсь…"
                    publishStatus("reconnecting", detail)
                    scheduleReconnect()
                }
            }
        })
        videoWs = client
        client.connect()
    }

    private fun scheduleReconnect() {
        if (!wanted) return
        val delay = minOf(20_000L, 1000L shl minOf(retryCount, 4))
        retryCount++
        mainHandler.removeCallbacks(reconnectRunnable)
        mainHandler.postDelayed(reconnectRunnable, delay)
    }

    private val reconnectRunnable = Runnable {
        if (!wanted) return@Runnable
        if (deviceToken.isBlank()) {
            connectVideo()
            return@Runnable
        }
        publishStatus("reconnecting", "Обновляю подключение к JARVIS…")
        Thread {
            val response = runCatching {
                JarvisNet.postJson(
                    "$server/api/device-login",
                    JSONObject().put("device_token", deviceToken).toString()
                )
            }.getOrNull()
            val refreshed = if (response != null && response.code in 200..299) {
                runCatching { JSONObject(response.body).optString("token") }.getOrDefault("")
            } else ""
            mainHandler.post {
                if (!wanted) return@post
                if (refreshed.isNotBlank()) token = refreshed
                connectVideo()
            }
        }.start()
    }

    private val ackWatchdog = object : Runnable {
        override fun run() {
            if (!wanted) return
            val last = lastAckAt
            if (last > 0L && SystemClock.elapsedRealtime() - last > ACK_TIMEOUT_MS) {
                publishStatus("waiting_frame", "Связь открыта, но JARVIS давно не подтверждал новые кадры.")
                lastAckAt = 0L
            }
            mainHandler.postDelayed(this, ACK_CHECK_MS)
        }
    }

    private fun scheduleAckWatchdog() {
        mainHandler.removeCallbacks(ackWatchdog)
        mainHandler.postDelayed(ackWatchdog, ACK_CHECK_MS)
    }

    private fun publishFrame(frame: ByteArray) {
        sendBroadcast(
            Intent(ACTION_EVENT)
                .setPackage(packageName)
                .putExtra(EXTRA_STATUS, "frame")
                .putExtra(EXTRA_FRAME, frame)
        )
    }

    private fun publishStatus(status: String, detail: String = "") {
        currentStatus = status
        if (status == lastStatus && detail == lastDetail) return
        lastStatus = status
        lastDetail = detail
        sendBroadcast(
            Intent(ACTION_EVENT)
                .setPackage(packageName)
                .putExtra(EXTRA_STATUS, status)
                .putExtra(EXTRA_DETAIL, detail)
        )
    }

    private fun stopStream() {
        if (!wanted && !isRunning) {
            stopSelf()
            return
        }
        wanted = false
        isRunning = false
        getSharedPreferences("jarvis_remote", MODE_PRIVATE)
            .edit().putBoolean("camera_live_enabled", false).apply()
        mainHandler.removeCallbacks(reconnectRunnable)
        mainHandler.removeCallbacks(ackWatchdog)
        videoWs?.close()
        videoWs = null
        cameraHandler?.post { releaseCamera() }
        cameraThread?.quitSafely()
        cameraHandler = null
        cameraThread = null
        publishStatus("stopped", "Передача камеры остановлена.")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) stopForeground(STOP_FOREGROUND_REMOVE)
        else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
        stopSelf()
    }

    private fun releaseCamera() {
        val session = captureSession
        captureSession = null
        try { session?.stopRepeating() } catch (_: Exception) {}
        try { session?.close() } catch (_: Exception) {}
        val device = cameraDevice
        cameraDevice = null
        try { device?.close() } catch (_: Exception) {}
        val reader = imageReader
        imageReader = null
        try { reader?.setOnImageAvailableListener(null, null) } catch (_: Exception) {}
        try { reader?.close() } catch (_: Exception) {}
    }

    override fun onDestroy() {
        wanted = false
        isRunning = false
        mainHandler.removeCallbacksAndMessages(null)
        videoWs?.close()
        videoWs = null
        cameraHandler?.post { releaseCamera() }
        cameraThread?.quitSafely()
        cameraHandler = null
        cameraThread = null
        currentStatus = "stopped"
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

}
