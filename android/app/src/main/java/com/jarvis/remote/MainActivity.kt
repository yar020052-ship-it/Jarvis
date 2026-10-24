package com.jarvis.remote

import android.Manifest
import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.net.Uri
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.YuvImage
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.media.MediaRecorder
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import kotlin.math.sqrt

class MainActivity : Activity() {

    private val prefs by lazy { getSharedPreferences("jarvis_remote", MODE_PRIVATE) }
    private val h = Handler(Looper.getMainLooper())

    private lateinit var root: FrameLayout
    private lateinit var connectView: View
    private lateinit var mainView: LinearLayout
    private lateinit var addr: EditText
    private lateinit var code: EditText
    private lateinit var connectStatus: TextView
    private lateinit var scroller: ScrollView
    private lateinit var feed: LinearLayout
    private lateinit var face: FaceView
    private lateinit var statusLabel: TextView
    private lateinit var pill: TextView
    private lateinit var input: EditText
    private lateinit var micBtn: TextView
    private lateinit var cameraLiveButton: TextView
    private lateinit var phonePreview: ImageView
    private lateinit var liveBadge: TextView
    private var cameraUri: Uri? = null
    private val shotFile by lazy { java.io.File(cacheDir, "jarvis_shot.jpg") }

    private var server = ""
    private var token = ""
    private var sessionKey = ""
    private var ws: WsClient? = null
    private var audioWs: WsClient? = null
    private var record: AudioRecord? = null
    @Volatile private var cameraRequested = false
    private var cameraReceiverRegistered = false
    private var lastCameraUiStatus = ""
    private var phoneSpeaker: AudioTrack? = null
    private var echoCanceler: AcousticEchoCanceler? = null
    private var noiseSuppressor: NoiseSuppressor? = null
    @Volatile private var micOn = false
    private var connected = false
    private var reconnectScheduled = false
    private var reauthInProgress = false
    private var faceTimer: Runnable? = null

    private val cameraReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.action != PhoneCameraService.ACTION_EVENT) return
            val status = intent.getStringExtra(PhoneCameraService.EXTRA_STATUS).orEmpty()
            if (status == "frame") {
                val data = intent.getByteArrayExtra(PhoneCameraService.EXTRA_FRAME) ?: return
                val bitmap = BitmapFactory.decodeByteArray(data, 0, data.size) ?: return
                if (::phonePreview.isInitialized && mainView.visibility == View.VISIBLE) {
                    phonePreview.setImageBitmap(bitmap)
                }
            } else {
                applyPhoneCameraStatus(
                    status,
                    intent.getStringExtra(PhoneCameraService.EXTRA_DETAIL).orEmpty()
                )
            }
        }
    }

    private val quick = listOf(
        "ЭКРАН" to "Take a look at my screen and tell me what you see.",
        "ИТОГ" to "Summarise what is on my screen.",
        "КОД" to "Explain the code on my screen step by step.",
        "ПЕРЕВОД" to "Translate the text on my screen to Russian.",
        "НОВОСТИ" to "Give me a short briefing of today's top news.",
        "ПЛАН" to "Help me plan my day."
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        JarvisTls.initialize(this)
        @Suppress("DEPRECATION")
        run {
            window.statusBarColor = Pal.BG
            window.navigationBarColor = Pal.BG
        }

        root = FrameLayout(this).apply { setBackgroundColor(Pal.BG) }
        connectView = buildConnect()
        mainView = buildMain()
        mainView.visibility = View.GONE
        root.addView(connectView, match())
        root.addView(mainView, match())
        setContentView(root)

        if (Build.VERSION.SDK_INT >= 30) {
            window.setDecorFitsSystemWindows(false)
            root.setOnApplyWindowInsetsListener { v, insets ->
                val bars = insets.getInsets(WindowInsets.Type.systemBars())
                val ime = insets.getInsets(WindowInsets.Type.ime())
                // With edge-to-edge enabled, adjustResize alone does not move content
                // above the keyboard. Include the IME inset in the root's bottom padding.
                v.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, ime.bottom))
                insets
            }
        }

        tryAutoLogin()
    }

    // ── UI construction ──────────────────────────────────────────────────

    private fun buildConnect(): ScrollView {
        val scroll = ScrollView(this).apply {
            isFillViewport = true
            clipToPadding = false
            setBackgroundColor(Pal.BG)
            isVerticalScrollBarEnabled = false
        }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setBackgroundColor(Pal.BG)
            setPadding(dp(24), dp(36), dp(24), dp(28))
        }
        col.addView(tv("J", 28f, Pal.PRI, bold = true).apply {
            gravity = Gravity.CENTER
            background = round(Pal.PANEL2, 20f, Pal.BORDER_B, 1)
            elevation = dpf(10f)
        }, LinearLayout.LayoutParams(dp(68), dp(68)))
        col.addView(tv("JARVIS", 24f, Pal.WHITE, bold = true, letterSpacing = 0.28f).apply {
            gravity = Gravity.CENTER
            setPadding(0, dp(18), 0, dp(2))
        })
        col.addView(tv("УДАЛЁННОЕ УПРАВЛЕНИЕ", 9f, Pal.TEXT_DIM, letterSpacing = 0.16f).apply {
            gravity = Gravity.CENTER
        })
        col.addView(tv("Адрес компьютера в Wi-Fi", 12f, Pal.TEXT_MED).apply {
            setPadding(dp(2), dp(28), 0, dp(8))
        })
        addr = EditText(this).apply {
            isSingleLine = true
            textSize = 14f
            hint = "https://192.168.1.10:8000"
            setTextColor(Pal.WHITE)
            setHintTextColor(Pal.TEXT_DIM)
            background = round(Pal.PANEL, 12f, Pal.BORDER, 1)
            setPadding(dp(14), dp(12), dp(14), dp(12))
            minHeight = dp(52)
            onFocusChangeListener = View.OnFocusChangeListener { view, focused ->
                view.background = round(Pal.PANEL, 12f, if (focused) Pal.PRI else Pal.BORDER, 1)
            }
            setText(prefs.getString("server", "") ?: "")
        }
        col.addView(addr, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        col.addView(tv("Код сопряжения с экрана JARVIS", 12f, Pal.TEXT_MED).apply {
            setPadding(dp(2), dp(18), 0, dp(8))
        })
        code = EditText(this).apply {
            isSingleLine = true
            textSize = 16f
            hint = "напр. 7KQ2WX"
            setTextColor(Pal.PRI)
            setHintTextColor(Pal.TEXT_DIM)
            background = round(Pal.PANEL, 12f, Pal.BORDER, 1)
            setPadding(dp(14), dp(12), dp(14), dp(12))
            letterSpacing = 0.18f
            minHeight = dp(52)
            onFocusChangeListener = View.OnFocusChangeListener { view, focused ->
                view.background = round(Pal.PANEL, 12f, if (focused) Pal.PRI else Pal.BORDER, 1)
            }
        }
        col.addView(code, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        val cbtn = button("ПОДКЛЮЧИТЬСЯ", Pal.BG, Pal.PRI, Pal.PRI).apply {
            gravity = Gravity.CENTER
            setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, 14f)
            setOnClickListener { doLogin() }
        }
        col.addView(cbtn, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(54)).apply {
            topMargin = dp(20)
        })
        connectStatus = tv("Телефон и ПК должны быть в одной сети. Откройте на ПК раздел удалённого управления, чтобы получить код.", 12f, Pal.TEXT_DIM).apply {
            gravity = Gravity.CENTER
            setLineSpacing(dpf(3f), 1f)
            setPadding(dp(8), dp(16), dp(8), 0)
        }
        col.addView(connectStatus)
        scroll.addView(col, FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ))
        return scroll
    }

    private fun buildMain(): LinearLayout {
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Pal.BG)
        }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(14), dp(12), dp(12), dp(10))
        }
        header.addView(tv("J", 16f, Pal.PRI, bold = true).apply {
            gravity = Gravity.CENTER
            background = round(Pal.PANEL2, 14f, Pal.BORDER_B, 1)
        }, LinearLayout.LayoutParams(dp(42), dp(42)))
        val titles = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        titles.addView(tv("JARVIS", 16f, Pal.WHITE, bold = true, letterSpacing = 0.22f))
        titles.addView(tv("Удалённое управление", 10f, Pal.TEXT_DIM))
        header.addView(titles, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { leftMargin = dp(10) })
        pill = tv("…", 10f, Pal.TEXT_MED, bold = true, letterSpacing = 0.06f).apply {
            gravity = Gravity.CENTER
            setPadding(dp(10), dp(6), dp(10), dp(6))
            background = round(Pal.PANEL2, 20f, Pal.BORDER, 1)
        }
        header.addView(pill)
        col.addView(header)

        statusLabel = tv("ГОТОВ", 11f, Pal.TEXT_MED, bold = true, letterSpacing = 0.12f).apply {
            gravity = Gravity.CENTER
            setPadding(0, dp(4), 0, dp(8))
        }
        col.addView(statusLabel)

        val quickScroll = HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
            setPadding(dp(12), 0, dp(6), dp(6))
        }
        val qrow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        for ((label, msg) in quick) {
            val b = button(label, Pal.TEXT_MED, Pal.PANEL2, Pal.BORDER)
            b.setOnClickListener { send(msg) }
            qrow.addView(b, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { rightMargin = dp(8) })
        }
        quickScroll.addView(qrow)
        col.addView(quickScroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        val visionStage = FrameLayout(this)
        face = FaceView(this)
        visionStage.addView(face, FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT
        ))
        phonePreview = ImageView(this).apply {
            visibility = View.GONE
            scaleType = ImageView.ScaleType.CENTER_CROP
            contentDescription = "Предварительный просмотр live-камеры планшета"
        }
        visionStage.addView(phonePreview, FrameLayout.LayoutParams(dp(132), dp(176), Gravity.TOP or Gravity.END).apply {
            topMargin = dp(12)
            rightMargin = dp(12)
        })
        liveBadge = tv("● LIVE", 10f, Pal.GREEN, bold = true).apply {
            gravity = Gravity.CENTER
            background = round(Pal.BG, 12f, Pal.GREEN, 1)
            setPadding(dp(8), dp(5), dp(8), dp(5))
            visibility = View.GONE
        }
        visionStage.addView(liveBadge, FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
            Gravity.TOP or Gravity.END
        ).apply { topMargin = dp(18); rightMargin = dp(20) })
        col.addView(visionStage, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1.2f
        ))

        scroller = ScrollView(this).apply {
            setPadding(dp(10), dp(4), dp(10), dp(4))
            isVerticalScrollBarEnabled = true
        }
        feed = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        scroller.addView(feed)
        col.addView(scroller, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))

        val controls = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(10), dp(4), dp(10), dp(2))
        }
        val controls2 = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(dp(10), dp(0), dp(10), dp(2))
        }
        micBtn = button("МИКРОФОН", Pal.TEXT_MED, Pal.PANEL2, Pal.BORDER).apply { setOnClickListener { toggleMic() } }
        val stopBtn = button("СТОП РЕЧИ", Pal.ACC2, Pal.PANEL2, Pal.BORDER_A).apply { setOnClickListener { doInterrupt() } }
        val wakeBtn = button("РАЗБУДИТЬ", Pal.GREEN, Pal.PANEL2, Pal.BORDER_A).apply { setOnClickListener { doWake() } }
        cameraLiveButton = button("ВИДЕТЬ LIVE", Pal.PRI, Pal.PANEL2, Pal.BORDER).apply {
            contentDescription = "Включить или остановить live-видео; удерживайте для одного снимка"
            setOnClickListener { togglePhoneCamera() }
            setOnLongClickListener { if (cameraRequested) stopPhoneCamera(); takePhoto(); true }
        }
        val fileBtn = button("ФАЙЛ", Pal.TEXT_MED, Pal.PANEL2, Pal.BORDER).apply { setOnClickListener { pickFile() } }
        val setBtn = button("НАСТР.", Pal.TEXT_MED, Pal.PANEL2, Pal.BORDER).apply { setOnClickListener { showConnect() } }
        controls.addView(micBtn, w1().apply { rightMargin = dp(6) })
        controls.addView(stopBtn, w1().apply { rightMargin = dp(6) })
        controls.addView(wakeBtn, w1())
        controls2.addView(cameraLiveButton, w1().apply { rightMargin = dp(6) })
        controls2.addView(fileBtn, w1().apply { rightMargin = dp(6) })
        controls2.addView(setBtn, w1())
        col.addView(controls, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        col.addView(controls2, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        val inputRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.BOTTOM
            setPadding(dp(10), dp(6), dp(10), dp(10))
        }
        input = EditText(this).apply {
            hint = "Сообщение JARVIS…"
            textSize = 14f
            setTextColor(Pal.WHITE)
            setHintTextColor(Pal.TEXT_DIM)
            background = round(Pal.PANEL2, 12f, Pal.BORDER, 1)
            setPadding(dp(14), dp(10), dp(14), dp(10))
            maxLines = 4
            onFocusChangeListener = View.OnFocusChangeListener { view, focused ->
                view.background = round(Pal.PANEL2, 12f, if (focused) Pal.PRI else Pal.BORDER, 1)
            }
            inputType = android.text.InputType.TYPE_CLASS_TEXT or
                android.text.InputType.TYPE_TEXT_FLAG_CAP_SENTENCES or
                android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE
        }
        val sendBtn = button("➤", Pal.BG, Pal.PRI, Pal.PRI).apply {
            setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, 16f)
            setOnClickListener {
                val t = input.text.toString().trim()
                if (t.isNotEmpty()) {
                    input.setText("")
                    send(t)
                }
            }
        }
        inputRow.addView(input, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = dp(8) })
        inputRow.addView(sendBtn, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        col.addView(inputRow, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        return col
    }

    private fun w1(): LinearLayout.LayoutParams =
        LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)

    private fun match(): FrameLayout.LayoutParams = FrameLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT
    )

    // ── auth ─────────────────────────────────────────────────────────────

    private fun tryAutoLogin() {
        val savedServer = prefs.getString("server", null)
        val savedDev = prefs.getString("device_token", null)
        if (savedServer.isNullOrBlank() || savedDev.isNullOrBlank()) return
        server = savedServer
        sessionKey = prefs.getString("session_key", "") ?: ""
        addr.setText(savedServer)
        connectStatus.text = "Подключение к сохранённому ПК…"
        Thread {
            try {
                val r = JarvisNet.postJson(
                    "$server/api/device-login",
                    JSONObject().put("device_token", savedDev).toString()
                )
                runOnUiThread {
                    if (r.code in 200..299) {
                        val j = JSONObject(r.body)
                        token = j.optString("token")
                        sessionKey = j.optString("key", sessionKey)
                        prefs.edit().putString("session_key", sessionKey).apply()
                        enterMain()
                    } else {
                        connectStatus.text = "Сохранённое устройство не найдено — введите код с экрана ПК."
                    }
                }
            } catch (e: Exception) {
                runOnUiThread { connectStatus.text = "ПК недоступен: " + (e.message ?: "проверьте Wi-Fi и адрес") }
            }
        }.start()
    }

    private fun doLogin() {
        val pin = code.text.toString().trim().uppercase()
        if (pin.isEmpty()) {
            connectStatus.text = "Введите код сопряжения с экрана ПК"
            return
        }
        val s = try {
            Net.normalizeServer(addr.text.toString())
        } catch (e: Exception) {
            connectStatus.text = e.message ?: "Неверный адрес"
            return
        }
        server = s
        addr.setText(s)
        connectStatus.text = "Подключение к $s…"
        Thread {
            try {
                val r = JarvisNet.postJson("$s/login", JSONObject().put("pin", pin).toString())
                runOnUiThread {
                    if (r.code in 200..299) {
                        val j = JSONObject(r.body)
                        token = j.optString("token")
                        sessionKey = j.optString("key", pin)
                        val dev = j.optString("device_token")
                        prefs.edit()
                            .putString("server", s)
                            .putString("device_token", dev)
                            .putString("session_key", sessionKey)
                            .apply()
                        connectStatus.text = "Готово"
                        enterMain()
                    } else {
                        connectStatus.text = "Не удалось: неверный или просроченный код"
                    }
                }
            } catch (e: Exception) {
                runOnUiThread { connectStatus.text = "Нет связи с ПК: " + (e.message ?: "") }
            }
        }.start()
    }

    private fun enterMain() {
        connectView.visibility = View.GONE
        mainView.visibility = View.VISIBLE
        feed.removeAllViews()
        addSystem("Подключено к $server. Разрешите доступ к микрофону в системном окне: голосовой канал запустится автоматически и ответ прозвучит на планшете. Камера LIVE включается отдельно.")
        if (PhoneCameraService.isRunning) {
            applyPhoneCameraStatus(PhoneCameraService.currentStatus, "")
        }
        connectStatus.text = ""
        openWs()
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            startMic()
        } else {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 7)
        }
    }

    private fun showConnect() {
        h.removeCallbacksAndMessages(null)
        ws?.close()
        ws = null
        stopMic()
        stopPhoneCamera()
        connected = false
        addr.setText(prefs.getString("server", "") ?: server)
        connectView.visibility = View.VISIBLE
        mainView.visibility = View.GONE
    }

    // ── websocket ────────────────────────────────────────────────────────

    private fun openWs() {
        if (mainView.visibility != View.VISIBLE || connected) return
        h.removeCallbacks(reconnect)
        reconnectScheduled = false
        ws?.close()
        connected = false
        setPill("ПОДКЛЮЧЕНИЕ…", Pal.TEXT_MED)
        val url = Net.wsBase(server) + "/ws?token=" + Net.token(token)
        ws = WsClient(url, object : WsClient.Listener {
            override fun onOpen() = runOnUiThread {
                connected = true
                setPill("НА СВЯЗИ", Pal.GREEN)
                if (!micOn) faceTo("IDLE")
                if (!micOn && checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                    startMic()
                }
            }

            override fun onMessage(text: String) = runOnUiThread { handleWs(text) }

            override fun onBinary(data: ByteArray) { /* audio downlink uses phone-audio socket */ }

            override fun onClosed(reason: String?) = runOnUiThread { onWsClosed(reason) }
        })
        ws?.connect()
    }

    private fun onWsClosed(reason: String?) {
        connected = false
        setPill("НЕТ СВЯЗИ", Pal.RED)
        if (mainView.visibility == View.VISIBLE && !reconnectScheduled) {
            reconnectScheduled = true
            h.postDelayed(reconnect, 2500)
        }
    }

    private val reconnect = object : Runnable {
        override fun run() {
            reconnectScheduled = false
            if (mainView.visibility == View.VISIBLE && !connected) refreshDeviceSession()
        }
    }

    /** A desktop JARVIS restart invalidates short-lived WebSocket tokens. Exchange the
     *  saved paired-device token for a fresh one before reconnecting all app channels. */
    private fun refreshDeviceSession() {
        if (reauthInProgress || mainView.visibility != View.VISIBLE || connected) return
        val deviceToken = prefs.getString("device_token", "").orEmpty()
        if (deviceToken.isBlank()) {
            addSystem("Сессия ПК истекла. Откройте «НАСТР.» и подключитесь кодом с экрана JARVIS.")
            showConnect()
            return
        }
        reauthInProgress = true
        Thread {
            val response = try {
                JarvisNet.postJson(
                    "$server/api/device-login",
                    JSONObject().put("device_token", deviceToken).toString()
                )
            } catch (e: Exception) {
                null
            }
            runOnUiThread {
                reauthInProgress = false
                if (mainView.visibility != View.VISIBLE || connected) return@runOnUiThread
                if (response != null && response.code in 200..299) {
                    try {
                        val json = JSONObject(response.body)
                        token = json.optString("token")
                        sessionKey = json.optString("key", sessionKey)
                        prefs.edit().putString("session_key", sessionKey).apply()
                        if (micOn) stopMic()
                        openWs()
                    } catch (_: Exception) {
                        scheduleReconnect()
                    }
                } else if (response?.code == 401) {
                    addSystem("Сохранённое сопряжение ПК истекло. Подключитесь заново кодом с экрана JARVIS.")
                    showConnect()
                } else {
                    scheduleReconnect()
                }
            }
        }.start()
    }

    private fun scheduleReconnect() {
        if (mainView.visibility == View.VISIBLE && !connected && !reconnectScheduled) {
            reconnectScheduled = true
            h.postDelayed(reconnect, 3000)
        }
    }

    private fun handleWs(text: String) {
        val j = try {
            JSONObject(text)
        } catch (e: Exception) {
            return
        }
        when (j.optString("type")) {
            "log" -> {
                val spk = j.optString("speaker")
                val body = j.optString("text")
                if (spk == "jarvis") {
                    addBubble("jarvis", body)
                    flashSpeaking((900L + body.length * 55L).coerceAtMost(12000L))
                } else {
                    addBubble("user", body)
                }
            }
            "sys" -> addSystem(j.optString("text"))
            "status" -> {
                if (j.optString("state") == "sleeping") {
                    setPill("СПИТ", Pal.ACC2)
                    if (!micOn) faceTo("IDLE")
                } else {
                    setPill("НА СВЯЗИ", Pal.GREEN)
                }
            }
            "file_received" -> addSystem("ПК получил файл: " + j.optString("name"))
        }
    }

    private fun send(text: String) {
        val w = ws
        if (w == null || !connected) {
            toast("Нет связи с ПК")
            return
        }
        addBubble("user", text)
        faceTo("THINKING")
        try {
            val enc = Crypto.encrypt(sessionKey, text)
            w.sendText(JSONObject().put("type", "command").put("enc", enc).toString())
        } catch (e: Exception) {
            addSystem("Не удалось отправить: ${e.message}")
        }
    }

    private fun doWake() {
        if (server.isEmpty() || token.isEmpty()) return
        val s = server
        val t = token
        Thread {
            runCatching { JarvisNet.postJson("$s/api/wake", "{}", token = t) }
            runOnUiThread { addSystem("Отправлен сигнал пробуждения") }
        }.start()
    }

    private fun doInterrupt() {
        if (server.isEmpty() || token.isEmpty()) return
        val s = server
        val t = token
        Thread {
            val r = runCatching { JarvisNet.postJson("$s/api/interrupt", "{}", token = t) }.getOrNull()
            runOnUiThread {
                if (r != null && r.code in 200..299) addSystem("Стоп — речь остановлена")
                else addSystem("Не удалось остановить речь")
            }
        }.start()
    }

    // ── live Android camera → authenticated JARVIS vision stream ─────────

    private fun togglePhoneCamera() {
        if (cameraRequested) {
            stopPhoneCamera()
            return
        }
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.CAMERA), CAMERA_PERMISSION)
            return
        }
        startPhoneCameraStream()
    }

    private fun startPhoneCameraStream() {
        if (cameraRequested || server.isEmpty() || token.isEmpty()) return
        cameraRequested = true
        phonePreview.visibility = View.VISIBLE
        liveBadge.visibility = View.VISIBLE
        try {
            val service = Intent(this, PhoneCameraService::class.java)
                .setAction(PhoneCameraService.ACTION_START)
                .putExtra(PhoneCameraService.EXTRA_SERVER, server)
                .putExtra(PhoneCameraService.EXTRA_TOKEN, token)
                .putExtra(
                    PhoneCameraService.EXTRA_DEVICE_TOKEN,
                    prefs.getString("device_token", "").orEmpty()
                )
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(service)
            else startService(service)
            applyPhoneCameraStatus("connecting", "Подключаю камеру к JARVIS…")
        } catch (e: Exception) {
            cameraRequested = false
            addSystem("Не удалось запустить передачу камеры: ${e.message ?: "ошибка Android"}")
        }
    }

    private fun stopPhoneCamera() {
        cameraRequested = false
        prefs.edit().putBoolean("camera_live_enabled", false).apply()
        if (PhoneCameraService.isRunning) {
            startService(Intent(this, PhoneCameraService::class.java).setAction(PhoneCameraService.ACTION_STOP))
        }
        if (::phonePreview.isInitialized) phonePreview.visibility = View.GONE
        if (::phonePreview.isInitialized) phonePreview.setImageDrawable(null)
        if (::liveBadge.isInitialized) liveBadge.visibility = View.GONE
        if (::cameraLiveButton.isInitialized) cameraLiveButton.text = "ВИДЕТЬ LIVE"
    }

    private fun applyPhoneCameraStatus(status: String, detail: String) {
        val active = status != "stopped" && status != "error"
        cameraRequested = active
        if (::phonePreview.isInitialized) {
            phonePreview.visibility = if (active) View.VISIBLE else View.GONE
            if (!active) phonePreview.setImageDrawable(null)
        }
        if (::liveBadge.isInitialized) {
            liveBadge.visibility = if (active) View.VISIBLE else View.GONE
            val confirmed = status == "frame_received"
            liveBadge.text = if (confirmed) "● LIVE" else "● ОЖИДАНИЕ"
            val color = if (confirmed) Pal.GREEN else Pal.PRI
            liveBadge.setTextColor(color)
            liveBadge.background = round(Pal.BG, 12f, color, 1)
        }
        if (::cameraLiveButton.isInitialized) {
            cameraLiveButton.text = when (status) {
                "connecting", "reconnecting" -> "ПОДКЛЮЧЕНИЕ LIVE"
                "waiting_frame" -> "ОЖИДАНИЕ КАДРА"
                "frame_received" -> "СТОП LIVE"
                else -> "ВИДЕТЬ LIVE"
            }
        }
        if (detail.isNotBlank() && status != lastCameraUiStatus) addSystem(detail)
        lastCameraUiStatus = status
    }

    // ── camera (phone → PC, /api/upload → JARVIS vision) ──────────────────

    private fun takePhoto() = launchCamera()

    private fun launchCamera() {
        try {
            if (shotFile.exists()) shotFile.delete()
            val uri = Uri.parse("content://$packageName.photos/jarvis_shot.jpg")
            cameraUri = uri
            val i = Intent(MediaStore.ACTION_IMAGE_CAPTURE).apply {
                putExtra(MediaStore.EXTRA_OUTPUT, uri)
                addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            startActivityForResult(i, CAMERA)
        } catch (e: Exception) {
            toast("Камера недоступна: ${e.message}")
        }
    }

    private fun uploadPhoto(file: java.io.File) {
        Thread {
            try {
                val bmp = decodeScaled(file)
                if (bmp == null) {
                    runOnUiThread { addSystem("Не удалось прочитать фото") }
                    return@Thread
                }
                val bos = ByteArrayOutputStream()
                bmp.compress(Bitmap.CompressFormat.JPEG, 85, bos)
                val bytes = bos.toByteArray()
                val r = JarvisNet.postFile(
                    "$server/api/upload", token,
                    "phone_${System.currentTimeMillis()}.jpg", "image/jpeg", bytes
                )
                runOnUiThread {
                    if (r.code in 200..299) {
                        addSystem("Фото отправлено — JARVIS смотрит")
                        flashSpeaking(1600)
                    } else {
                        addSystem("Не удалось отправить фото (код ${r.code})")
                    }
                }
            } catch (e: Exception) {
                runOnUiThread { addSystem("Ошибка фото: ${e.message}") }
            } finally {
                try { file.delete() } catch (_: Exception) {}
            }
        }.start()
    }

    private fun decodeScaled(file: java.io.File): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.absolutePath, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        var sample = 1
        val longer = maxOf(bounds.outWidth, bounds.outHeight)
        while (longer / sample > 1600) sample *= 2
        val opts = BitmapFactory.Options().apply { inSampleSize = sample }
        return BitmapFactory.decodeFile(file.absolutePath, opts)
    }

    // ── microphone (phone → PC, /ws/phone-audio) ─────────────────────────

    private fun toggleMic() {
        if (micOn) {
            stopMic()
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 7)
            return
        }
        startMic()
    }

    private fun startMic() {
        if (micOn || server.isEmpty() || token.isEmpty()) return
        val sr = 16000
        val minBuf = AudioRecord.getMinBufferSize(
            sr, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        if (minBuf <= 0) {
            toast("Микрофон недоступен")
            return
        }
        val rec = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION, sr,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, minBuf * 2
            )
        } catch (e: Exception) {
            null
        }
        if (rec == null || rec.state != AudioRecord.STATE_INITIALIZED) {
            toast("Микрофон недоступен")
            try { rec?.release() } catch (_: Exception) {}
            return
        }
        val outMin = AudioTrack.getMinBufferSize(
            24000, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        if (outMin <= 0) {
            try { rec.release() } catch (_: Exception) {}
            toast("Динамик телефона недоступен")
            return
        }
        val player = try {
            AudioTrack.Builder()
                .setAudioAttributes(
                    android.media.AudioAttributes.Builder()
                        .setUsage(android.media.AudioAttributes.USAGE_ASSISTANT)
                        .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setSampleRate(24000)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .build()
                )
                .setTransferMode(AudioTrack.MODE_STREAM)
                .setBufferSizeInBytes(maxOf(outMin * 4, 9600))
                .build()
        } catch (_: Exception) {
            null
        }
        if (player == null || player.state != AudioTrack.STATE_INITIALIZED) {
            try { player?.release() } catch (_: Exception) {}
            try { rec.release() } catch (_: Exception) {}
            toast("Не удалось открыть динамик телефона")
            return
        }
        val url = Net.wsBase(server) + "/ws/phone-audio?token=" + Net.token(token)
        audioWs = WsClient(url, object : WsClient.Listener {
            override fun onOpen() = runOnUiThread {
                if (micOn) addSystem("Микрофон включён: JARVIS слышит вас и отвечает голосом через телефон. Камера включается кнопкой «КАМЕРА».")
            }
            override fun onMessage(text: String) {}
            override fun onBinary(data: ByteArray) {
                if (micOn && data.isNotEmpty()) {
                    try { player.write(data, 0, data.size, AudioTrack.WRITE_BLOCKING) }
                    catch (_: Exception) { runOnUiThread { if (micOn) stopMic() } }
                }
            }
            override fun onClosed(reason: String?) = runOnUiThread { if (micOn) stopMic() }
        })
        record = rec
        phoneSpeaker = player
        try {
            if (AcousticEchoCanceler.isAvailable()) {
                echoCanceler = AcousticEchoCanceler.create(rec.audioSessionId)?.apply { enabled = true }
            }
            if (NoiseSuppressor.isAvailable()) {
                noiseSuppressor = NoiseSuppressor.create(rec.audioSessionId)?.apply { enabled = true }
            }
        } catch (_: Exception) {
            // Audio capture and playback still work when device effects are unavailable.
        }
        try {
            rec.startRecording()
            player.play()
        } catch (e: Exception) {
            record = null
            phoneSpeaker = null
            try { player.release() } catch (_: Exception) {}
            try { rec.release() } catch (_: Exception) {}
            releaseAudioEffects()
            toast("Не удалось включить микрофон: ${e.message ?: "ошибка аудиоустройства"}")
            return
        }
        micOn = true
        micBtn.text = "СТОП МИК"
        micBtn.background = round(Pal.PANEL2, 12f, Pal.MUTED, 1)
        micBtn.setTextColor(Pal.MUTED)
        faceTo("LISTENING")
        addSystem("Подключаю голосовой канал…")
        audioWs?.connect()
        Thread {
            val shorts = ShortArray(1024)
            val bytes = ByteArray(shorts.size * 2)
            while (micOn) {
                val n = try {
                    rec.read(shorts, 0, shorts.size)
                } catch (e: Exception) {
                    -1
                }
                if (n < 0) {
                    runOnUiThread {
                        if (micOn && record === rec) {
                            toast("Ошибка чтения микрофона ($n)")
                            stopMic()
                        }
                    }
                    break
                }
                if (n == 0) continue
                var sum = 0.0
                for (i in 0 until n) {
                    val v = shorts[i].toInt()
                    sum += v.toDouble() * v
                    bytes[i * 2] = (v and 0xFF).toByte()
                    bytes[i * 2 + 1] = ((v shr 8) and 0xFF).toByte()
                }
                val rms = sqrt(sum / n) / 32768.0
                face.level = (rms * 6.0).coerceIn(0.0, 1.0).toFloat()
                if (micOn) audioWs?.sendBinary(bytes.copyOf(n * 2))
            }
        }.start()
    }

    private fun stopMic() {
        micOn = false
        val activeRecord = record
        record = null
        try { activeRecord?.stop() } catch (_: Exception) {}
        try { activeRecord?.release() } catch (_: Exception) {}
        audioWs?.close()
        audioWs = null
        try { phoneSpeaker?.pause() } catch (_: Exception) {}
        try { phoneSpeaker?.flush() } catch (_: Exception) {}
        try { phoneSpeaker?.stop() } catch (_: Exception) {}
        try { phoneSpeaker?.release() } catch (_: Exception) {}
        phoneSpeaker = null
        releaseAudioEffects()
        face.level = 0f
        if (::micBtn.isInitialized) {
            micBtn.text = "МИКРОФОН"
            micBtn.background = round(Pal.PANEL2, 12f, Pal.BORDER, 1)
        }
        if (::face.isInitialized) faceTo("IDLE")
    }

    private fun releaseAudioEffects() {
        try { echoCanceler?.release() } catch (_: Exception) {}
        try { noiseSuppressor?.release() } catch (_: Exception) {}
        echoCanceler = null
        noiseSuppressor = null
    }

    // ── files ────────────────────────────────────────────────────────────

    private fun pickFile() {
        try {
            val i = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                addCategory(Intent.CATEGORY_OPENABLE)
                type = "*/*"
            }
            startActivityForResult(i, PICK)
        } catch (e: Exception) {
            toast("Не найдено приложение для выбора файла")
        }
    }

    private fun queryName(uri: android.net.Uri): String? {
        try {
            contentResolver.query(uri, null, null, null, null)?.use { c ->
                val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (idx >= 0 && c.moveToFirst()) return c.getString(idx)
            }
        } catch (_: Exception) {}
        return uri.lastPathSegment
    }

    // ── feed helpers ─────────────────────────────────────────────────────

    private fun addBubble(speaker: String, text: String) {
        if (text.isBlank()) return
        val isUser = speaker == "user"
        val card = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        card.setPadding(dp(14), dp(11), dp(14), dp(11))
        card.background = round(if (isUser) Pal.PRI_GHO else Pal.PANEL2, 16f, if (isUser) Pal.PRI_DIM else Pal.BORDER, 1)
        card.addView(tv(if (isUser) "ВЫ" else "JARVIS", 10f, if (isUser) Pal.PRI else Pal.GREEN, bold = true, letterSpacing = 0.06f))
        card.addView(tv(text, 14f, Pal.WHITE).apply {
            setLineSpacing(dpf(3f), 1f)
            setPadding(0, dp(4), 0, 0)
            maxWidth = (resources.displayMetrics.widthPixels * 0.78f).toInt()
        })
        feed.addView(card, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ).apply {
            gravity = if (isUser) Gravity.END else Gravity.START
            setMargins(dp(3), dp(4), dp(3), dp(4))
        })
        scroller.post { scroller.fullScroll(View.FOCUS_DOWN) }
    }

    private fun addSystem(text: String) {
        if (text.isBlank()) return
        val t = tv(text, 12f, Pal.TEXT_DIM).apply {
            gravity = Gravity.CENTER
            setLineSpacing(dpf(2f), 1f)
            setPadding(dp(6), dp(3), dp(6), dp(3))
        }
        feed.addView(t, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ))
        scroller.post { scroller.fullScroll(View.FOCUS_DOWN) }
    }

    private fun setPill(text: String, color: Int) {
        pill.text = text
        pill.setTextColor(color)
        pill.background = round(Pal.PANEL2, 20f, color, 1)
    }

    private fun faceTo(state: String) {
        face.state = state
        statusLabel.text = when (state) {
            "SPEAKING" -> "ГОВОРЮ"
            "THINKING", "PROCESSING" -> "ДУМАЮ"
            "LISTENING" -> "СЛУШАЮ"
            else -> "ГОТОВ"
        }
    }

    private fun flashSpeaking(ms: Long) {
        faceTimer?.let { h.removeCallbacks(it) }
        faceTo("SPEAKING")
        val r = Runnable { faceTo(if (micOn) "LISTENING" else "IDLE") }
        faceTimer = r
        h.postDelayed(r, ms)
    }

    // ── lifecycle ────────────────────────────────────────────────────────

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == CAMERA) {
            if (resultCode == RESULT_OK && shotFile.exists() && shotFile.length() > 0) {
                uploadPhoto(shotFile)
            } else {
                toast("Съёмка отменена")
                try { shotFile.delete() } catch (_: Exception) {}
            }
            cameraUri = null
            return
        }
        if (requestCode == PICK && resultCode == RESULT_OK) {
            val uri = data?.data ?: return
            Thread {
                try {
                    val name = queryName(uri) ?: "file.bin"
                    val mime = contentResolver.getType(uri) ?: "application/octet-stream"
                    val bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: return@Thread
                    runOnUiThread { addSystem("Отправляю $name (${bytes.size / 1024} КБ)…") }
                    val r = JarvisNet.postFile("$server/api/upload", token, name, mime, bytes)
                    runOnUiThread {
                        addSystem(if (r.code in 200..299) "Файл отправлен на ПК" else "Не удалось отправить файл (код ${r.code})")
                    }
                } catch (e: Exception) {
                    runOnUiThread { addSystem("Ошибка отправки: ${e.message}") }
                }
            }.start()
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 7) {
            if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) startMic()
            else toast("Разрешение на микрофон отклонено")
        } else if (requestCode == CAMERA_PERMISSION) {
            if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) startPhoneCameraStream()
            else toast("Разрешение на камеру отклонено")
        }
    }

    @Deprecated("Deprecated in Android, retained for older devices")
    override fun onBackPressed() {
        if (::mainView.isInitialized && mainView.visibility == View.VISIBLE) showConnect() else super.onBackPressed()
    }

    override fun onStart() {
        super.onStart()
        val filter = IntentFilter(PhoneCameraService.ACTION_EVENT)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(cameraReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("DEPRECATION")
            registerReceiver(cameraReceiver, filter)
        }
        cameraReceiverRegistered = true
        if (PhoneCameraService.isRunning && ::mainView.isInitialized && mainView.visibility == View.VISIBLE) {
            applyPhoneCameraStatus(PhoneCameraService.currentStatus, "")
        }
    }

    override fun onStop() {
        if (micOn) stopMic()
        if (cameraReceiverRegistered) {
            try { unregisterReceiver(cameraReceiver) } catch (_: Exception) {}
            cameraReceiverRegistered = false
        }
        super.onStop()
    }

    override fun onDestroy() {
        h.removeCallbacksAndMessages(null)
        reconnectScheduled = false
        try { stopMic() } catch (_: Exception) {}
        ws?.close()
        audioWs?.close()
        super.onDestroy()
    }

    private fun toast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
    }

    private companion object {
        const val PICK = 42
        const val CAMERA = 43
        const val CAMERA_PERMISSION = 8
    }
}
