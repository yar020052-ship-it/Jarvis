package com.jarvis.remote

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView

/** Shared Midnight Glass palette (mirrors ui.py class C and dashboard CSS). */
object Pal {
    val BG = 0xFF070B12.toInt()
    val PANEL = 0xFF101827.toInt()
    val PANEL2 = 0xFF141E30.toInt()
    val BORDER = 0xFF27364B.toInt()
    val BORDER_B = 0xFF3D5777.toInt()
    val BORDER_A = 0xFF334867.toInt()
    val PRI = 0xFF52D4F2.toInt()
    val PRI_DIM = 0xFF328DA8.toInt()
    val PRI_GHO = 0xFF152D3A.toInt()
    val ACC = 0xFFFFAE63.toInt()
    val ACC2 = 0xFFF2C66D.toInt()
    val GREEN = 0xFF61D6AC.toInt()
    val GREEN_D = 0xFF3EAA86.toInt()
    val RED = 0xFFFF7185.toInt()
    val MUTED = 0xFFC18AA5.toInt()
    val TEXT = 0xFFD1DFEF.toInt()
    val TEXT_DIM = 0xFF9AAAC0.toInt()
    val TEXT_MED = 0xFFB2C0D4.toInt()
    val WHITE = 0xFFF3F7FD.toInt()
    val DARK = 0xFF0B111C.toInt()
    val BAR_BG = 0xFF182338.toInt()
}

fun Context.dp(v: Int): Int = (v * resources.displayMetrics.density + 0.5f).toInt()
fun Context.dpf(v: Float): Float = v * resources.displayMetrics.density

fun Context.round(
    color: Int,
    radiusDp: Float,
    strokeColor: Int? = null,
    strokeDp: Int = 1
): GradientDrawable {
    val d = GradientDrawable()
    d.shape = GradientDrawable.RECTANGLE
    d.cornerRadius = dpf(radiusDp)
    d.setColor(color)
    if (strokeColor != null) d.setStroke(dp(strokeDp).coerceAtLeast(1), strokeColor)
    return d
}

fun Context.tv(
    text: String,
    sizeSp: Float,
    color: Int,
    bold: Boolean = false,
    letterSpacing: Float = 0f
): TextView = TextView(this).apply {
    this.text = text
    // Keep captions readable across the remote app, including on high-density
    // tablets where 8–9sp labels otherwise look like decorative noise.
    setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp.coerceAtLeast(10f))
    setTextColor(color)
    if (bold) setTypeface(typeface, android.graphics.Typeface.BOLD)
    if (letterSpacing != 0f) this.letterSpacing = letterSpacing
    includeFontPadding = false
}

fun Context.button(
    text: String,
    color: Int,
    bg: Int,
    border: Int
): TextView = tv(text, 12f, color, bold = true, letterSpacing = 0.04f).apply {
    gravity = Gravity.CENTER
    setPadding(dp(14), dp(11), dp(14), dp(11))
    background = RippleDrawable(
        ColorStateList.valueOf(0x3352D4F2),
        round(bg, 12f, border, 1),
        round(0xFF52D4F2.toInt(), 12f)
    )
}

fun horizontal(context: Context, spacingDp: Int = 0): LinearLayout =
    LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        if (spacingDp > 0) {
            // spacing applied by callers via margins for reliability
        }
    }

fun View.margin(left: Int, top: Int, right: Int, bottom: Int) {
    val lp = layoutParams as? LinearLayout.LayoutParams
    if (lp != null) {
        lp.setMargins(left, top, right, bottom)
        layoutParams = lp
    }
}
