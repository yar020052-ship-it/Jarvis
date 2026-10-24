package com.jarvis.remote

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.os.Handler
import android.os.Looper
import android.view.View
import kotlin.math.abs
import kotlin.math.min
import kotlin.math.sin

/**
 * Holographic JARVIS face — a native re-creation of the holographic head that
 * sits in the centre of the desktop app. States mirror the desktop HUD:
 * IDLE (cyan), LISTENING (green), SPEAKING (orange), THINKING (amber),
 * MUTED (pink).
 */
class FaceView(context: Context) : View(context) {

    var state: String = "IDLE"
        set(value) {
            if (field != value) {
                field = value
                invalidate()
            }
        }

    /** Audio level 0..1 used to animate the "voice" bars. */
    var level: Float = 0f

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val handler = Handler(Looper.getMainLooper())
    private var t0 = System.currentTimeMillis()
    private var running = false

    private val tick = object : Runnable {
        override fun run() {
            if (!running) return
            invalidate()
            handler.postDelayed(this, 33)
        }
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (!running) {
            running = true
            t0 = System.currentTimeMillis()
            handler.post(tick)
        }
    }

    override fun onDetachedFromWindow() {
        running = false
        handler.removeCallbacks(tick)
        super.onDetachedFromWindow()
    }

    private fun accent(): Int = when (state) {
        "SPEAKING" -> Pal.ACC
        "THINKING", "PROCESSING" -> Pal.ACC2
        "LISTENING" -> Pal.GREEN
        "MUTED" -> Pal.MUTED
        else -> Pal.PRI
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0f || h <= 0f) return
        canvas.drawColor(Pal.BG)
        val cx = w / 2f
        val cy = h / 2f
        val r = min(w, h) * 0.30f
        val t = (System.currentTimeMillis() - t0) / 1000f
        val acc = accent()

        // ── grid background ─────────────────────────────────────────────
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 1f
        paint.color = Pal.BORDER
        paint.alpha = 80
        val step = r * 0.5f
        var x = cx - ((cx / step).toInt()) * step
        while (x <= w) {
            canvas.drawLine(x, 0f, x, h, paint)
            x += step
        }
        var y = cy - ((cy / step).toInt()) * step
        while (y <= h) {
            canvas.drawLine(0f, y, w, y, paint)
            y += step
        }
        paint.alpha = 255

        // ── glow ────────────────────────────────────────────────────────
        paint.style = Paint.Style.FILL
        paint.shader = RadialGradient(
            cx, cy, r * 2.2f,
            intArrayOf(alpha(acc, 95), alpha(acc, 28), 0x00000000),
            floatArrayOf(0f, 0.5f, 1f),
            Shader.TileMode.CLAMP
        )
        canvas.drawCircle(cx, cy, r * 2.2f, paint)
        paint.shader = null

        // ── outer HUD ring ──────────────────────────────────────────────
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 1.5f
        paint.color = Pal.BORDER_B
        canvas.drawCircle(cx, cy, r * 1.30f, paint)

        // ticks
        paint.strokeWidth = 2f
        paint.color = alpha(acc, 120)
        val n = 48
        for (i in 0 until n) {
            val a = (i.toFloat() / n) * (Math.PI * 2).toFloat()
            val rr1 = r * 1.32f
            val rr2 = if (i % 4 == 0) r * 1.40f else r * 1.36f
            canvas.drawLine(
                cx + (Math.cos(a.toDouble()).toFloat()) * rr1, cy + (Math.sin(a.toDouble()).toFloat()) * rr1,
                cx + (Math.cos(a.toDouble()).toFloat()) * rr2, cy + (Math.sin(a.toDouble()).toFloat()) * rr2,
                paint
            )
        }

        // rotating arc
        paint.strokeWidth = 2.5f
        paint.color = alpha(acc, 200)
        val start = (t * 90f) % 360f
        canvas.drawArc(
            RectF(cx - r * 1.44f, cy - r * 1.44f, cx + r * 1.44f, cy + r * 1.44f),
            start, 70f, false, paint
        )
        canvas.drawArc(
            RectF(cx - r * 1.44f, cy - r * 1.44f, cx + r * 1.44f, cy + r * 1.44f),
            start + 180f, 70f, false, paint
        )

        // ── head outline ────────────────────────────────────────────────
        paint.strokeWidth = 2f
        paint.color = alpha(acc, 230)
        canvas.drawCircle(cx, cy, r, paint)

        paint.strokeWidth = 1.4f
        paint.color = Pal.BORDER_B
        canvas.drawOval(RectF(cx - r * 0.62f, cy - r * 0.82f, cx + r * 0.62f, cy + r * 0.82f), paint)

        // ── eyes ────────────────────────────────────────────────────────
        val blink = if ((t % 3.4f) < 0.12f) 0.12f else 1f
        paint.style = Paint.Style.FILL
        paint.color = alpha(acc, 235)
        val eyeW = r * 0.30f
        val eyeH = r * 0.20f * blink
        val eyeY = cy - r * 0.18f
        canvas.drawOval(
            RectF(cx - r * 0.36f - eyeW / 2, eyeY - eyeH / 2, cx - r * 0.36f + eyeW / 2, eyeY + eyeH / 2),
            paint
        )
        canvas.drawOval(
            RectF(cx + r * 0.36f - eyeW / 2, eyeY - eyeH / 2, cx + r * 0.36f + eyeW / 2, eyeY + eyeH / 2),
            paint
        )

        // ── voice bars (mouth) ──────────────────────────────────────────
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 3f
        paint.strokeCap = Paint.Cap.ROUND
        paint.color = alpha(acc, 215)
        val bars = 17
        val span = r * 1.1f
        val base = cy + r * 0.42f
        var amp = r * (0.05f + 0.32f * level)
        if (state == "SPEAKING") amp += r * 0.18f
        for (i in 0 until bars) {
            val fx = (i.toFloat() / (bars - 1) - 0.5f) * span
            val env = (1f - abs(fx) / (span * 0.62f)).coerceAtLeast(0f)
            val s = abs(sin(t * 9f + i * 0.9f))
            val hh = amp * (0.22f + 0.78f * s) * (0.4f + 0.6f * env)
            val xx = cx + fx
            canvas.drawLine(xx, base - hh, xx, base + hh, paint)
        }

        // ── scan line ───────────────────────────────────────────────────
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 1.5f
        paint.color = alpha(acc, 70)
        val sy = cy + sin(t * 1.6f) * r * 0.9f
        canvas.drawLine(cx - r * 1.2f, sy, cx + r * 1.2f, sy, paint)
    }

    private fun alpha(c: Int, a: Int): Int =
        Color.argb(a, Color.red(c), Color.green(c), Color.blue(c))
}
