package com.jarvis.remote

import org.json.JSONObject
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.IvParameterSpec
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec

/** Small HTTP helper for the desktop dashboard REST API. */
object JarvisNet {

    data class Resp(val code: Int, val body: String)

    private fun open(url: String, method: String, token: String?): HttpURLConnection {
        val c = URL(url).openConnection() as HttpURLConnection
        // Keep the platform trust store and hostname checks. Custom trust for a
        // self-signed desktop certificate must be explicit and pinned, never TrustAll.
        c.requestMethod = method
        if (c is javax.net.ssl.HttpsURLConnection) {
            c.sslSocketFactory = JarvisTls.socketFactory()
        }
        c.connectTimeout = 9000
        c.readTimeout = 20000
        c.doInput = true
        if (!token.isNullOrEmpty()) c.setRequestProperty("Authorization", "Bearer $token")
        return c
    }

    private fun read(c: HttpURLConnection): Resp {
        val code = c.responseCode
        val stream = if (code in 200..299) c.inputStream else (c.errorStream ?: c.inputStream)
        val body = try {
            stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
        } catch (_: Exception) {
            ""
        }
        c.disconnect()
        return Resp(code, body)
    }

    fun postJson(url: String, json: String, token: String? = null): Resp {
        val c = open(url, "POST", token)
        c.doOutput = true
        c.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        c.outputStream.use { it.write(json.toByteArray(Charsets.UTF_8)) }
        return read(c)
    }

    /** Upload a file as multipart/form-data (field name "file"). */
    fun postFile(url: String, token: String, fileName: String, mime: String, data: ByteArray): Resp {
        val boundary = "----jarvis" + System.currentTimeMillis()
        val c = open(url, "POST", token)
        c.doOutput = true
        c.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
        val out: OutputStream = c.outputStream
        val head = "--$boundary\r\n" +
            "Content-Disposition: form-data; name=\"file\"; filename=\"" + fileName + "\"\r\n" +
            "Content-Type: " + mime + "\r\n\r\n"
        out.write(head.toByteArray(Charsets.UTF_8))
        out.write(data)
        out.write("\r\n--$boundary--\r\n".toByteArray(Charsets.UTF_8))
        out.flush()
        out.close()
        return read(c)
    }

}

/** AES-256-CBC command encryption — identical to the web dashboard. */
object Crypto {
    private val SALT = "JARVIS-DASHBOARD-v1".toByteArray(Charsets.UTF_8)
    private const val ITERATIONS = 200_000

    private fun key(sessionKey: String): ByteArray {
        val spec = PBEKeySpec(sessionKey.toCharArray(), SALT, ITERATIONS, 256)
        return try {
            SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
                .generateSecret(spec).encoded
        } finally {
            spec.clearPassword()
        }
    }

    fun encrypt(sessionKey: String, text: String): String {
        val k = key(sessionKey)
        val iv = ByteArray(16).also { SecureRandom().nextBytes(it) }
        val cipher = Cipher.getInstance("AES/CBC/PKCS5Padding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(k, "AES"), IvParameterSpec(iv))
        val ct = cipher.doFinal(text.toByteArray(Charsets.UTF_8))
        val raw = ByteArray(iv.size + ct.size)
        System.arraycopy(iv, 0, raw, 0, iv.size)
        System.arraycopy(ct, 0, raw, iv.size, ct.size)
        return android.util.Base64.encodeToString(raw, android.util.Base64.NO_WRAP)
    }
}

/** URL utilities shared by the activities. */
object Net {
    fun normalizeServer(raw: String): String {
        val entered = raw.trim().trimEnd('/')
        require(entered.isNotEmpty()) { "Введите адрес ПК" }
        val withScheme = if (entered.contains("://")) entered else "https://$entered"
        val url = URL(withScheme)
        require(url.protocol == "http" || url.protocol == "https") { "Нужен адрес http(s)://" }
        require(!url.host.isNullOrBlank()) { "Нужен адрес вида IP:порт" }
        val port = if (url.port != -1) url.port else if (url.protocol == "https") 443 else 80
        return url.protocol + "://" + url.host + ":" + port
    }

    fun wsBase(server: String): String =
        if (server.startsWith("https")) "wss://" + server.removePrefix("https://")
        else "ws://" + server.removePrefix("http://")

    fun token(token: String): String = URLEncoder.encode(token, "UTF-8")

    fun parse(body: String): JSONObject = JSONObject(body)
}

/** Convenience wrapper for the dashboard endpoints. */
class JarvisApi(private val server: String) {

    fun login(pin: String): JarvisNet.Resp {
        val body = JSONObject().put("pin", pin).toString()
        return JarvisNet.postJson("$server/login", body)
    }

    fun deviceLogin(deviceToken: String): JarvisNet.Resp {
        val body = JSONObject().put("device_token", deviceToken).toString()
        return JarvisNet.postJson("$server/api/device-login", body)
    }

    fun wake(token: String): JarvisNet.Resp =
        JarvisNet.postJson("$server/api/wake", "{}", token)

    fun upload(token: String, name: String, mime: String, data: ByteArray): JarvisNet.Resp =
        JarvisNet.postFile("$server/api/upload", token, name, mime, data)
}
