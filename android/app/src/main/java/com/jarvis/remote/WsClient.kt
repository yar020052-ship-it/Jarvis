package com.jarvis.remote

import android.util.Base64
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URI
import java.security.MessageDigest
import java.security.SecureRandom
import javax.net.ssl.SSLSocket
import kotlin.concurrent.thread

/**
 * Minimal RFC-6455 WebSocket client built on plain sockets (no third-party
 * dependencies). Supports text + binary frames, ping/pong and clean close.
 * TLS (wss://) uses Android's standard trust store and hostname verification.
 */
class WsClient(private val url: String, private val listener: Listener) {

    interface Listener {
        fun onOpen()
        fun onMessage(text: String)
        fun onBinary(data: ByteArray)
        fun onClosed(reason: String?)
    }

    @Volatile private var socket: Socket? = null
    @Volatile private var out: OutputStream? = null
    @Volatile private var userClosed = false
    @Volatile private var started = false

    @Synchronized
    fun connect() {
        if (started) return
        started = true
        thread(name = "jarvis-ws") { runLoop() }
    }

    private fun runLoop() {
        var reason: String? = null
        try {
            val uri = URI(url)
            val secure = uri.scheme.equals("wss", true) || uri.scheme.equals("https", true)
            val host = uri.host ?: throw IllegalArgumentException("bad host")
            val port = if (uri.port != -1) uri.port else if (secure) 443 else 80
            val path = (uri.rawPath?.ifEmpty { "/" } ?: "/") +
                (uri.rawQuery?.let { "?$it" } ?: "")

            val s = if (secure) {
                val raw = JarvisTls.socketFactory().createSocket() as SSLSocket
                raw.connect(InetSocketAddress(host, port), 9000)
                raw.sslParameters = raw.sslParameters.apply {
                    endpointIdentificationAlgorithm = "HTTPS"
                }
                raw.startHandshake()
                raw
            } else {
                val raw = Socket()
                raw.connect(InetSocketAddress(host, port), 9000)
                raw
            }
            socket = s

            val os = BufferedOutputStream(s.getOutputStream())
            val ins = BufferedInputStream(s.getInputStream())

            val keyBytes = ByteArray(16).also { SecureRandom().nextBytes(it) }
            val wsKey = Base64.encodeToString(keyBytes, Base64.NO_WRAP)
            val origin = (if (secure) "https" else "http") + "://" + host + ":" + port
            val req = buildString {
                append("GET ").append(path).append(" HTTP/1.1\r\n")
                append("Host: ").append(host).append(":").append(port).append("\r\n")
                append("Upgrade: websocket\r\n")
                append("Connection: Upgrade\r\n")
                append("Sec-WebSocket-Key: ").append(wsKey).append("\r\n")
                append("Sec-WebSocket-Version: 13\r\n")
                append("Origin: ").append(origin).append("\r\n")
                append("\r\n")
            }
            os.write(req.toByteArray(Charsets.UTF_8))
            os.flush()

            val statusLine = readLine(ins) ?: throw IllegalStateException("no handshake")
            if (!Regex("^HTTP/1\\.[01] 101(?:\\s|$)").containsMatchIn(statusLine)) {
                reason = "HTTP " + statusLine.substringAfter(' ').trim()
                throw IllegalStateException(reason)
            }
            var accept: String? = null
            while (true) {
                val line = readLine(ins) ?: break
                if (line.isEmpty()) break
                if (line.startsWith("Sec-WebSocket-Accept:", ignoreCase = true)) {
                    accept = line.substringAfter(':').trim()
                }
            }
            val expectedAccept = Base64.encodeToString(
                MessageDigest.getInstance("SHA-1")
                    .digest((wsKey + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").toByteArray(Charsets.US_ASCII)),
                Base64.NO_WRAP
            )
            if (accept != expectedAccept) throw IllegalStateException("invalid websocket handshake")

            out = os
            listener.onOpen()

            while (true) {
                val frame = readFrame(ins) ?: break
                when (frame.opcode) {
                    0x1 -> listener.onMessage(String(frame.payload, Charsets.UTF_8))
                    0x2 -> listener.onBinary(frame.payload)
                    0x8 -> break
                    0x9 -> sendFrame(0xA, frame.payload)
                    0xA -> {}
                    else -> {}
                }
            }
        } catch (e: Exception) {
            if (!userClosed) reason = e.message
        } finally {
            closeQuietly()
            if (!userClosed) listener.onClosed(reason)
        }
    }

    @Synchronized
    fun sendText(text: String) {
        sendFrame(0x1, text.toByteArray(Charsets.UTF_8))
    }

    fun sendBinary(data: ByteArray) {
        sendFrame(0x2, data)
    }

    @Synchronized
    fun close() {
        userClosed = true
        try {
            sendFrame(0x8, ByteArray(0))
        } catch (_: Exception) {
        }
        closeQuietly()
    }

    private fun closeQuietly() {
        try {
            out?.close()
        } catch (_: Exception) {
        }
        try {
            socket?.close()
        } catch (_: Exception) {
        }
        out = null
        socket = null
    }

    @Synchronized
    private fun sendFrame(opcode: Int, payload: ByteArray) {
        val o = out ?: return
        val header = ByteArrayOutputStream()
        header.write(0x80 or (opcode and 0x0F))
        val len = payload.size
        when {
            len < 126 -> header.write(0x80 or len)
            len < 65536 -> {
                header.write(0x80 or 126)
                header.write((len ushr 8) and 0xFF)
                header.write(len and 0xFF)
            }
            else -> {
                header.write(0x80 or 127)
                for (i in 7 downTo 0) header.write(((len.toLong() ushr (8 * i)).toInt()) and 0xFF)
            }
        }
        val mask = ByteArray(4).also { SecureRandom().nextBytes(it) }
        header.write(mask)
        val masked = ByteArray(len)
        for (i in 0 until len) {
            masked[i] = (payload[i].toInt() xor mask[i and 3].toInt()).toByte()
        }
        try {
            o.write(header.toByteArray())
            o.write(masked)
            o.flush()
        } catch (_: Exception) {
        }
    }

    private fun readFrame(ins: InputStream): Frame? {
        val b0 = ins.read()
        if (b0 < 0) return null
        val b1 = ins.read()
        if (b1 < 0) return null
        val opcode = b0 and 0x0F
        val masked = (b1 and 0x80) != 0
        var len: Long = (b1 and 0x7F).toLong()
        if (len == 126L) {
            len = ((readByte(ins) shl 8) or readByte(ins)).toLong()
        } else if (len == 127L) {
            var v = 0L
            for (i in 0 until 8) v = (v shl 8) or readByte(ins).toLong()
            len = v
        }
        val mask = if (masked) ByteArray(4).also { readFully(ins, it) } else null
        if (len > 16L * 1024L * 1024L) throw IllegalStateException("websocket frame too large")
        val payload = ByteArray(len.toInt())
        readFully(ins, payload)
        if (mask != null) {
            for (i in payload.indices) {
                payload[i] = (payload[i].toInt() xor mask[i and 3].toInt()).toByte()
            }
        }
        return Frame(opcode, payload)
    }

    private fun readByte(ins: InputStream): Int {
        val b = ins.read()
        if (b < 0) throw IllegalStateException("eof")
        return b
    }

    private fun readFully(ins: InputStream, buf: ByteArray) {
        var off = 0
        while (off < buf.size) {
            val n = ins.read(buf, off, buf.size - off)
            if (n < 0) throw IllegalStateException("eof")
            off += n
        }
    }

    private fun readLine(ins: InputStream): String? {
        val sb = StringBuilder()
        while (true) {
            val b = ins.read()
            if (b < 0) return if (sb.isEmpty()) null else sb.toString()
            if (b == '\n'.code) return sb.toString().trimEnd('\r')
            sb.append(b.toChar())
        }
    }

    private data class Frame(val opcode: Int, val payload: ByteArray)

}
