package de.eray.ytmusic

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.OpenableColumns
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

data class Song(val titel: String, val interpret: String, val dauer: String)
data class Format(val name: String, val label: String, val verfuegbar: Boolean)
data class Treffer(val titel: String, val kanal: String, val dauer: String,
                   val url: String, val bild: String)

/**
 * Oberfläche im Stil der Desktop-Fassung: oben Suche mit Ergebniskarten,
 * daneben der Playlist-Bereich – dieselben Farben, dieselben Statusangaben.
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            var dunkel by remember { mutableStateOf(true) }
            YtmdTheme(dunkel) {
                Surface(color = MaterialTheme.colorScheme.background) {
                    Oberflaeche(dunkel) { dunkel = !dunkel }
                }
            }
        }
    }

    /**
     * Die gewählte Datei in den App-Ordner kopieren – mit passender Endung.
     *
     * Der Dateiwähler liefert je nach Quelle eine URI wie ".../document/msf:42".
     * Daraus wurde ein Name ohne ".csv", und der Kern las die Liste dann als
     * JSON, scheiterte und schwieg: auf dem Bildschirm passierte nichts. Also
     * den Anzeigenamen erfragen und notfalls am Inhalt erkennen, was es ist.
     */
    private fun kopieren(uri: Uri): String {
        val anzeige = contentResolver
            .query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
            ?.use { if (it.moveToFirst()) it.getString(0) else null }
            ?: uri.lastPathSegment?.substringAfterLast('/')

        val roh = File(filesDir, "playlist_import.tmp")
        contentResolver.openInputStream(uri).use { ein ->
            roh.outputStream().use { aus -> ein?.copyTo(aus) }
        }
        if (roh.length() == 0L) throw java.io.IOException("Datei ist leer")

        var endung = anzeige?.substringAfterLast('.', "")?.lowercase() ?: ""
        if (endung != "csv" && endung != "json") {
            // Am ersten sichtbaren Zeichen erkennbar: "{" oder "[" heißt JSON.
            val erstes = roh.bufferedReader().use { leser ->
                generateSequence { leser.read().takeIf { z -> z >= 0 } }
                    .firstOrNull { z -> !Character.isWhitespace(z) }?.toChar()
            }
            endung = if (erstes == '{' || erstes == '[') "json" else "csv"
        }

        // Der Dateiname ist zugleich der angezeigte Playlistname.
        val basis = (anzeige?.substringBeforeLast('.') ?: "")
            .replace(Regex("[^\\p{L}\\p{N} _-]"), "_").trim().ifBlank { "Playlist" }
        val ziel = File(filesDir, "$basis.$endung")
        ziel.delete()
        if (!roh.renameTo(ziel)) {
            roh.copyTo(ziel, overwrite = true)
            roh.delete()
        }
        return ziel.absolutePath
    }

    /** Darf die App überall schreiben? Erst dann ist der Musikordner erreichbar. */
    fun vollzugriff(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.R || Environment.isExternalStorageManager()

    /**
     * Zielordner. Mit Vollzugriff der öffentliche Musikordner – nur dort finden
     * Dateimanager und Musik-Apps die Songs. Ohne Vollzugriff bleibt nur der
     * App-Ordner unter Android/data, den seit Android 11 niemand mehr öffnen kann.
     */
    fun zielOrdner(): File = (
        if (vollzugriff())
            File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MUSIC),
                 "YT Music Downloader")
        else
            File(getExternalFilesDir(null), "Musik")
        ).apply { mkdirs() }

    fun zugriffAnfragen() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            startActivity(Intent(
                Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                Uri.parse("package:$packageName")))
        }
    }

    private fun starten(block: Intent.() -> Unit) {
        startForegroundService(Intent(this, DownloadService::class.java).apply {
            putExtra("ziel", zielOrdner().absolutePath)
            block()
        })
    }

    private suspend fun python() = withContext(Dispatchers.IO) {
        if (!Python.isStarted()) Python.start(AndroidPlatform(this@MainActivity))
        Python.getInstance().getModule("ytmd.headless")
    }

    @Composable
    private fun Oberflaeche(dunkel: Boolean, umschalten: () -> Unit) {
        val bereich = remember { mutableStateOf(0) }   // 0 = Suche, 1 = Playlist
        var format by remember { mutableStateOf("original") }
        var formate by remember { mutableStateOf(listOf<Format>()) }
        var workers by remember { mutableStateOf(2) }
        var kernStatus by remember { mutableStateOf("Kern wird geprüft …") }
        var laeuft by remember { mutableStateOf(false) }
        var ergebnis by remember { mutableStateOf<String?>(null) }

        LaunchedEffect(Unit) {
            kernStatus = try {
                python().callAttr("selbsttest").toString()
            } catch (e: Throwable) { "Kern nicht startbar: ${e.message}" }
            // Ohne FFmpeg lässt sich nichts umwandeln – dann bietet der Kern nur
            // "Original" an, und die Oberfläche zeigt die anderen ausgegraut.
            try {
                val j = JSONArray(python().callAttr("formate").toString())
                formate = (0 until j.length()).map {
                    val o = j.getJSONObject(it)
                    val name = o.getString("name")
                    Format(name, o.getString("label"),
                           // WAV geht auch ohne FFmpeg: Android bringt eigene
                           // Dekoder mit, umgewandelt wird nach dem Download.
                           o.getBoolean("available") || name == "wav")
                }
                if (formate.none { it.name == format && it.verfuegbar }) {
                    format = formate.firstOrNull { it.verfuegbar }?.name ?: "original"
                }
            } catch (_: Throwable) { }
        }
        LaunchedEffect(Unit) {
            while (true) {
                laeuft = DownloadService.Fortschritt.laeuft
                ergebnis = DownloadService.Fortschritt.ergebnis
                delay(400)
            }
        }

        Column(Modifier.fillMaxSize()) {
            Kopfzeile(dunkel, umschalten, kernStatus)
            Speicherort()

            TabRow(selectedTabIndex = bereich.value,
                   containerColor = MaterialTheme.colorScheme.background,
                   contentColor = Farben.Akzent) {
                Tab(selected = bereich.value == 0, onClick = { bereich.value = 0 },
                    text = { Text("Suche") })
                Tab(selected = bereich.value == 1, onClick = { bereich.value = 1 },
                    text = { Text("Playlist") })
            }

            Einstellungen(formate, format, { format = it }, workers, { workers = it },
                          laeuft, zeigeWorkers = bereich.value == 1)

            ergebnis?.let {
                Text(it, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                     modifier = Modifier.padding(horizontal = 16.dp))
            }

            if (bereich.value == 0) SucheBereich(format, laeuft)
            else PlaylistBereich(format, workers, laeuft)
        }
    }

    @Composable
    private fun Kopfzeile(dunkel: Boolean, umschalten: () -> Unit, status: String) {
        Surface(color = MaterialTheme.colorScheme.surface, tonalElevation = 2.dp) {
            Column(Modifier.fillMaxWidth()
                       .windowInsetsPadding(WindowInsets.statusBars)
                       .padding(horizontal = 16.dp, vertical = 12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("YouTube Music Downloader", color = Farben.Akzent,
                             fontSize = 20.sp, fontWeight = FontWeight.Bold)
                        // Version sichtbar: Sonst lässt sich nicht erkennen, ob
                        // eine Korrektur überhaupt auf dem Gerät angekommen ist.
                        Text("Fassung ${BuildConfig.VERSION_NAME}  ·  Songs suchen " +
                             "oder ganze Playlists sichern",
                             color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
                    }
                    TextButton(onClick = umschalten) {
                        Text(if (dunkel) "☀" else "☾", fontSize = 20.sp)
                    }
                }
                Text(status, fontSize = 10.sp,
                     color = MaterialTheme.colorScheme.onSurfaceVariant,
                     maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
    }

    /**
     * Zeigt den Zielordner an – und bietet den Vollzugriff an, solange die Songs
     * in Android/data landen würden, wo kein Dateimanager hinkommt.
     */
    @Composable
    private fun Speicherort() {
        var erlaubt by remember { mutableStateOf(vollzugriff()) }
        // Nach der Rückkehr aus den Einstellungen neu prüfen
        LaunchedEffect(Unit) {
            while (true) { erlaubt = vollzugriff(); delay(1000) }
        }

        Column(Modifier.padding(horizontal = 16.dp, vertical = 6.dp),
               verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Speicherort: " + zielOrdner().absolutePath
                     .removePrefix("/storage/emulated/0/"),
                 fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (!erlaubt) {
                Text("Ohne Dateizugriff landen die Songs in einem Ordner, den " +
                     "Dateimanager seit Android 11 nicht mehr öffnen können.",
                     fontSize = 11.sp, color = Farben.Warnung)
                Button(onClick = { zugriffAnfragen() },
                       shape = RoundedCornerShape(10.dp),
                       colors = ButtonDefaults.buttonColors(containerColor = Farben.Warnung)) {
                    Text("Dateizugriff erlauben", fontWeight = FontWeight.Bold)
                }
            }
        }
    }

    @Composable
    private fun Einstellungen(formate: List<Format>, format: String,
                              setFormat: (String) -> Unit,
                              workers: Int, setWorkers: (Int) -> Unit,
                              laeuft: Boolean, zeigeWorkers: Boolean) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
               verticalArrangement = Arrangement.spacedBy(6.dp)) {
            // Waagerecht scrollbar: Seit "WAV-CD" und "MP3-192" passen die
            // Knöpfe nicht mehr nebeneinander, und "Original" lag außerhalb
            // des Bildschirms – also unerreichbar.
            Row(Modifier.horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                formate.distinctBy { it.name }.forEach { f ->
                    FilterChip(selected = format == f.name, onClick = { setFormat(f.name) },
                               // Ohne FFmpeg bleiben WAV/FLAC/MP3 ausgegraut,
                               // statt still das Falsche zu tun.
                               enabled = !laeuft && f.verfuegbar,
                               shape = RoundedCornerShape(10.dp),
                               label = { Text(if (f.name == "original") "Original"
                                              else f.name.uppercase()) })
                }
            }
            // Für Songs, die schon ohne Umwandlung auf dem Gerät liegen
            if (format == "wav") {
                var stand by remember { mutableStateOf<String?>(null) }
                val bereich = rememberCoroutineScope()
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    OutlinedButton(
                        onClick = {
                            bereich.launch {
                                stand = withContext(Dispatchers.IO) {
                                    val offen = zielOrdner().listFiles()
                                        ?.filter { it.isFile && WavKonverter.kandidat(it) }
                                        ?: emptyList()
                                    if (offen.isEmpty()) "Nichts umzuwandeln"
                                    else {
                                        var ok = 0
                                        offen.forEach {
                                            if (WavKonverter.nachWavMitCover(it) != null) ok++
                                        }
                                        "Umgewandelt: $ok von ${offen.size}"
                                    }
                                }
                            }
                        },
                        enabled = !laeuft, shape = RoundedCornerShape(10.dp)) {
                        Text("Vorhandene nach WAV wandeln", fontSize = 12.sp)
                    }
                    stand?.let {
                        Text(it, fontSize = 11.sp,
                             color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }

            if (formate.any { !it.verfuegbar }) {
                Text(if (format == "wav")
                         "WAV wird nach dem Download auf dem Gerät erzeugt – das " +
                         "dauert etwas länger und braucht rund zehnmal mehr Platz."
                     else
                         "FLAC und MP3 brauchen FFmpeg, das es auf Android nicht " +
                         "gibt. Möglich sind \"Original\" (.m4a) und WAV.",
                     fontSize = 11.sp, color = Farben.Warnung)
            }
            if (zeigeWorkers) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Text("Gleichzeitig", fontSize = 12.sp,
                         color = MaterialTheme.colorScheme.onSurfaceVariant)
                    listOf(1, 2, 3, 4).forEach { n ->
                        FilterChip(selected = workers == n, onClick = { setWorkers(n) },
                                   enabled = !laeuft, shape = RoundedCornerShape(10.dp),
                                   label = { Text("$n") })
                    }
                }
            }
        }
    }

    // === Suche ===
    @Composable
    private fun SucheBereich(format: String, laeuft: Boolean) {
        var frage by remember { mutableStateOf("") }
        var treffer by remember { mutableStateOf(listOf<Treffer>()) }
        var sucht by remember { mutableStateOf(false) }
        val bereich = rememberCoroutineScope()

        fun suchen() {
            if (frage.isBlank()) return
            sucht = true
            bereich.launch {
                treffer = try {
                    val roh = withContext(Dispatchers.IO) {
                        python().callAttr("suche", frage).toString()
                    }
                    val j = JSONArray(roh)
                    (0 until j.length()).map {
                        val o = j.getJSONObject(it)
                        Treffer(o.getString("title"), o.getString("channel"),
                                o.getString("duration"), o.getString("url"),
                                o.getString("thumb"))
                    }
                } catch (e: Throwable) { listOf() }
                sucht = false
            }
        }

        Column(Modifier.padding(horizontal = 16.dp),
               verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = frage, onValueChange = { frage = it },
                    placeholder = { Text("Songname eingeben …") },
                    singleLine = true, shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.weight(1f))
                Button(onClick = { suchen() }, enabled = !sucht,
                       shape = RoundedCornerShape(12.dp),
                       colors = ButtonDefaults.buttonColors(containerColor = Farben.Akzent)) {
                    Text("Suchen", fontWeight = FontWeight.Bold)
                }
            }
            if (sucht) Text("Suche läuft …", fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
        }

        LazyColumn(Modifier.fillMaxSize().padding(16.dp),
                   verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(treffer) { t ->
                Surface(color = MaterialTheme.colorScheme.surface,
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth()) {
                    Row(Modifier.padding(10.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        AsyncImage(model = t.bild, contentDescription = null,
                                   contentScale = ContentScale.Crop,
                                   modifier = Modifier.size(96.dp, 54.dp)
                                       .clip(RoundedCornerShape(8.dp)))
                        Column(Modifier.weight(1f).padding(horizontal = 10.dp)) {
                            Text(t.titel, fontSize = 13.sp, fontWeight = FontWeight.Bold,
                                 maxLines = 2, overflow = TextOverflow.Ellipsis)
                            Text(listOfNotNull(t.kanal.ifBlank { null },
                                               t.dauer.ifBlank { null })
                                     .joinToString("  ·  "),
                                 fontSize = 11.sp,
                                 color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Button(onClick = { starten { putExtra("url", t.url)
                                                     putExtra("format", format) } },
                               enabled = !laeuft, shape = RoundedCornerShape(10.dp),
                               contentPadding = PaddingValues(horizontal = 12.dp),
                               colors = ButtonDefaults.buttonColors(
                                   containerColor = Farben.Download)) {
                            Text("Laden", fontSize = 12.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }
        }
    }

    // === Playlist ===
    @Composable
    private fun PlaylistBereich(format: String, workers: Int, laeuft: Boolean) {
        var playlist by remember { mutableStateOf<String?>(null) }
        var listenName by remember { mutableStateOf("") }
        var anzahl by remember { mutableStateOf(0) }
        var groesse by remember { mutableStateOf("") }
        var songs by remember { mutableStateOf(listOf<Song>()) }
        var zustaende by remember { mutableStateOf(mapOf<Int, String>()) }
        var fertig by remember { mutableStateOf(0) }
        var gesamt by remember { mutableStateOf(0) }
        // Fehler sichtbar machen: Vorher wurde jeder Fehlschlag verschluckt,
        // und der Knopf sah aus, als hätte er nichts getan.
        var fehler by remember { mutableStateOf<String?>(null) }

        val waehlen = rememberLauncherForActivityResult(
            ActivityResultContracts.OpenDocument()) { uri ->
            uri?.let {
                fehler = null
                try { playlist = kopieren(it) }
                catch (e: Throwable) { fehler = "Datei nicht lesbar: ${e.message}" }
            }
        }

        LaunchedEffect(playlist, format) {
            val pfad = playlist ?: return@LaunchedEffect
            try {
                val roh = withContext(Dispatchers.IO) {
                    python().callAttr("playlist_info", pfad, format).toString()
                }
                val j = JSONObject(roh)
                listenName = j.getString("name")
                anzahl = j.getInt("count")
                groesse = j.getString("size")
                val liste = j.getJSONArray("tracks")
                songs = (0 until liste.length()).map {
                    val o = liste.getJSONObject(it)
                    Song(o.getString("title"), o.getString("artist"), o.getString("duration"))
                }
                fehler = if (anzahl == 0)
                    "Keine Songs erkannt. Erwartet wird ein Export mit einer " +
                    "Spalte für den Titel (z. B. \"Track Name\" aus Exportify)."
                else null
            } catch (e: Throwable) {
                anzahl = 0
                songs = listOf()
                fehler = "Playlist nicht lesbar: ${e.message}"
            }
        }
        LaunchedEffect(Unit) {
            while (true) {
                fertig = DownloadService.Fortschritt.fertig
                gesamt = DownloadService.Fortschritt.gesamt
                zustaende = DownloadService.Fortschritt.zustaende.toMap()
                delay(400)
            }
        }

        Column(Modifier.padding(horizontal = 16.dp),
               verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { waehlen.launch(arrayOf("*/*")) }, enabled = !laeuft,
                   shape = RoundedCornerShape(12.dp),
                   colors = ButtonDefaults.buttonColors(containerColor = Farben.Playlist),
                   modifier = Modifier.fillMaxWidth()) {
                Text("Playlist laden (CSV oder JSON)", fontWeight = FontWeight.Bold)
            }
            fehler?.let {
                Text(it, fontSize = 11.sp, color = Farben.Warnung)
            }
            if (anzahl > 0) {
                Surface(color = MaterialTheme.colorScheme.surface,
                        shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp),
                           verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("$listenName  ·  $anzahl Songs  ·  ca. $groesse",
                             fontWeight = FontWeight.Bold, fontSize = 14.sp)
                        if (gesamt > 0) {
                            LinearProgressIndicator(
                                progress = { fertig.toFloat() / gesamt },
                                color = Farben.Playlist,
                                modifier = Modifier.fillMaxWidth().height(6.dp))
                            Text("$fertig von $gesamt", fontSize = 11.sp,
                                 color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
                Button(onClick = { starten { putExtra("playlist", playlist)
                                             putExtra("format", format)
                                             putExtra("workers", workers) } },
                       enabled = !laeuft, shape = RoundedCornerShape(12.dp),
                       colors = ButtonDefaults.buttonColors(containerColor = Farben.Download),
                       modifier = Modifier.fillMaxWidth().height(46.dp)) {
                    Text(if (laeuft) "läuft …" else "Alle herunterladen",
                         fontWeight = FontWeight.Bold)
                }
            }
        }

        LazyColumn(Modifier.fillMaxSize().padding(16.dp),
                   verticalArrangement = Arrangement.spacedBy(6.dp)) {
            itemsIndexed(songs) { i, s ->
                val (text, farbe) = statusText(zustaende[i] ?: "")
                Surface(color = MaterialTheme.colorScheme.surface,
                        shape = RoundedCornerShape(10.dp), modifier = Modifier.fillMaxWidth()) {
                    Row(Modifier.padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text("%04d".format(i + 1), fontSize = 11.sp,
                             fontWeight = FontWeight.Bold,
                             color = MaterialTheme.colorScheme.onSurfaceVariant,
                             modifier = Modifier.width(42.dp))
                        Column(Modifier.weight(1f)) {
                            Text(s.titel, fontSize = 13.sp, fontWeight = FontWeight.Bold,
                                 maxLines = 1, overflow = TextOverflow.Ellipsis)
                            val unten = listOfNotNull(s.interpret.ifBlank { null },
                                                      s.dauer.ifBlank { null })
                                .joinToString("  ·  ")
                            if (unten.isNotEmpty()) {
                                Text(unten, fontSize = 11.sp,
                                     color = MaterialTheme.colorScheme.onSurfaceVariant,
                                     maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                        }
                        Text(text, fontSize = 11.sp, color = farbe)
                    }
                }
            }
        }
    }
}
