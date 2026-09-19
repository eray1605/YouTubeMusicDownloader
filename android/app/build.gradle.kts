plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.chaquo.python")
}

android {
    namespace = "de.eray.ytmusic"
    compileSdk = 35

    defaultConfig {
        applicationId = "de.eray.ytmusic"
        minSdk = 24
        targetSdk = 35
        versionCode = 144
        versionName = "1.4.4"

        ndk {
            // arm64-v8a für echte Telefone, x86_64 für den Emulator.
            // Chaquopy wertet diese Liste beim Konfigurieren aus – eine
            // Ergänzung im Debug-Block kommt zu spät und die Laufzeit fehlt.
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"),
                          "proguard-rules.pro")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    kotlinOptions { jvmTarget = "11" }
    buildFeatures {
        compose = true
        buildConfig = true      // für BuildConfig.VERSION_NAME in der Kopfzeile
    }

}

// Der Python-Kern lebt weiter im Desktop-Projekt: eine Quelle für beide
// Plattformen, kein zweiter Stand zum Pflegen. Vor jedem Build wird er in den
// Standardordner von Chaquopy gespiegelt (Sync entfernt dort auch Gelöschtes).
val kernQuelle: String = providers.gradleProperty("ytmd.core").getOrElse(
    "C:/Users/Eray/Downloads/YT-MP3-Downloader-main/YT-MP3-Downloader-main/ytmd")

val ytmdSpiegeln by tasks.registering(Sync::class) {
    description = "Kopiert das ytmd-Paket aus dem Desktop-Projekt"
    from(kernQuelle) { exclude("__pycache__/**", "**/*.pyc") }
    into(layout.projectDirectory.dir("src/main/python/ytmd"))
    doFirst {
        if (!file(kernQuelle).isDirectory) {
            throw GradleException(
                "Python-Kern nicht gefunden: $kernQuelle\n" +
                "Pfad in gradle.properties unter ytmd.core anpassen.")
        }
    }
}

tasks.named("preBuild") { dependsOn(ytmdSpiegeln) }

// Chaquopy liest denselben Ordner ein. Ohne ausdrückliche Abhängigkeit wäre die
// Reihenfolge zufällig – Gradle bricht deshalb ab.
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("PythonSources") }
    .configureEach { dependsOn(ytmdSpiegeln) }

chaquopy {
    defaultConfig {
        version = "3.11"
        pip {
            install("yt-dlp")
            install("requests")
            install("mutagen")
            // Wandelt YouTubes WebP-Vorschaubilder in einbettbares JPEG um
            install("Pillow")
        }
        // Startpunkt ist unser bestehender headless-Einstieg
        pyc { src = false }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("androidx.activity:activity-compose:1.9.1")
    implementation(platform("androidx.compose:compose-bom:2024.09.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    // Lädt die YouTube-Vorschaubilder der Suchergebnisse
    implementation("io.coil-kt:coil-compose:2.7.0")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
