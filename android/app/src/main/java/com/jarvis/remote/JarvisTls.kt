package com.jarvis.remote

import android.content.Context
import java.io.InputStream
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.X509TrustManager

/** Trust only this JARVIS installation's self-signed server certificate. */
object JarvisTls {
    @Volatile private var pinnedSocketFactory: SSLSocketFactory? = null

    @Synchronized
    fun initialize(context: Context) {
        if (pinnedSocketFactory != null) return

        val certificate = context.applicationContext.resources
            .openRawResource(R.raw.jarvis_server_certificate).use { input: InputStream ->
                CertificateFactory.getInstance("X.509")
                    .generateCertificate(input) as X509Certificate
            }
        certificate.checkValidity()

        val trustManager = object : X509TrustManager {
            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf(certificate)

            override fun checkClientTrusted(chain: Array<out X509Certificate>, authType: String) {
                throw CertificateException("Client certificates are not accepted")
            }

            override fun checkServerTrusted(chain: Array<out X509Certificate>, authType: String) {
                if (chain.isEmpty() || !certificate.sameCertificate(chain[0])) {
                    throw CertificateException("JARVIS server certificate does not match the pinned certificate")
                }
                chain[0].checkValidity()
            }
        }

        pinnedSocketFactory = SSLContext.getInstance("TLS").apply {
            init(null, arrayOf(trustManager), SecureRandom())
        }.socketFactory
    }

    fun socketFactory(): SSLSocketFactory =
        checkNotNull(pinnedSocketFactory) { "JARVIS TLS has not been initialized" }

    private fun X509Certificate.sameCertificate(other: X509Certificate): Boolean =
        encoded.contentEquals(other.encoded)
}
