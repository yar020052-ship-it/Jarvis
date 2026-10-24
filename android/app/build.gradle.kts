plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.jarvis.remote"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.jarvis.remote"
        minSdk = 26
        targetSdk = 35
        versionCode = 3
        versionName = "1.2"
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_1_8)
    }
}
