"""Zentrale Einstellungen – hier lassen sich Aussehen und Suchverhalten anpassen."""

from dataclasses import dataclass, field
from typing import Optional, Tuple

# Fassung der Desktop-App, sichtbar im Kopfbereich. Ohne sie lässt sich nicht
# erkennen, welcher Stand gerade läuft – und ein altes Fertigprogramm sieht
# dann aus wie eine kaputte Korrektur. Bei einem Release mitziehen (die
# Android-App führt dieselbe Nummer in android/app/build.gradle.kts).
APP_VERSION = "1.4.3"

FONT_FAMILY = "Satoshi Medium"

# --- Suche ---
NUM_RESULTS = 10          # Treffer, die bei einer normalen Suche angezeigt werden
PLAYLIST_CANDIDATES = 8   # Treffer, aus denen pro Playlist-Song ausgewählt wird

# Mindestpunktzahl, ab der ein Treffer als derselbe Song gilt. Erreicht kein
# Treffer sie, wird der Song lieber gar nicht geladen als falsch.
MIN_MATCH_SCORE = 15

# Schafft kein Treffer die Mindestpunktzahl, gibt es einen zweiten Durchgang, in
# dem die Länge nicht mehr zählt. Der greift nur, wenn alles andere eindeutig ist:
# Titel exakt enthalten, Interpret im Kanalnamen, keine Hinweise auf eine andere
# Fassung. So kommt "Porco e Bella" (66 s bei Spotify, 294 s beim offiziellen
# Kanal) doch noch an, ohne Live-Mitschnitte oder Remixe durchzulassen.
RELAXED_FALLBACK = True
# Obergrenze dafür – gegen ganze Alben und Stundenloops
RELAXED_MAX_SECONDS = 900

# Wie viel des Songtitels sich im Videotitel wiederfinden muss (0.0-1.0).
# Nicht 1.0, weil Schreibweisen abweichen: "C R E E D" vs. "CREED",
# "Fuck Tha Police" vs. "Fuk Da Police".
MIN_TITLE_SIMILARITY = 0.75

# Wörter, die auf eine andere Fassung hindeuten (Live, Cover, Remix ...).
# Sie zählen nur, wenn sie NICHT schon im gesuchten Songtitel vorkommen –
# ein Song, der wirklich "Live" heißt, wird also nicht bestraft.
WRONG_VERSION_WORDS = (
    "live", "cover", "remix", "reaction", "karaoke", "instrumental", "playback",
    "nightcore", "slowed", "reverb", "8d", "sped", "mashup", "medley", "megamix",
    "concert", "konzert", "tour", "festival", "unplugged", "acoustic", "akustik",
    "session", "rehearsal", "soundcheck", "snippet", "teaser", "trailer",
    "tutorial", "review", "interview", "behind", "making", "parody", "parodie",
    "loop", "hour", "stunde", "compilation", "megamix", "dj set", "full album",
)

# --- Audio ---

@dataclass(frozen=True)
class AudioFormat:
    """Ein wählbares Zielformat inkl. Größenabschätzung.

    bytes_per_second dient nur der Vorschau ("wie viel Platz brauche ich?").
    """

    label: str
    codec: str
    bytes_per_second: int
    quality: Optional[str] = None          # nur für verlustbehaftete Codecs
    args: Tuple[str, ...] = field(default_factory=tuple)
    # Auswahlname, wenn mehrere Einträge dieselbe Dateiendung benutzen
    name: Optional[str] = None

    @property
    def kennung(self):
        """Eindeutiger Name für Auswahl und Oberfläche."""
        return self.name or self.codec or "original"

    @property
    def per_minute(self):
        return self.bytes_per_second * 60


# Dateiendungen, unter denen eine Tonspur liegen kann. Ohne Umwandlung
# bestimmt YouTube die Endung, nicht wir.
AUDIO_EXTENSIONS = (".m4a", ".webm", ".opus", ".mp3", ".wav", ".flac", ".ogg", ".aac")

AUDIO_FORMATS = (
    # 16 Bit, Abtastrate der Quelle. YouTubes beste Spur ist Opus mit 48 kHz –
    # sie auf 44,1 kHz zu rechnen kostet Qualität und bringt nichts.
    # 48 kHz, 16 Bit, Stereo = 192.000 Byte/s
    AudioFormat("WAV · unkomprimiert", "wav", 192_000,
                args=("-sample_fmt", "s16")),
    # Audio-CDs sind auf 44,1 kHz festgelegt. Wer brennen will, braucht diese
    # Fassung – sonst muss das Brennprogramm selbst umrechnen.
    AudioFormat("WAV · CD-tauglich (44,1 kHz)", "wav", 176_400,
                args=("-ar", "44100", "-ac", "2", "-sample_fmt", "s16"),
                name="wav-cd"),
    AudioFormat("FLAC · verlustfrei", "flac", 100_000),
    AudioFormat("MP3 · 320 kbps", "mp3", 40_000, quality="320"),
    AudioFormat("MP3 · 192 kbps", "mp3", 24_000, quality="192", name="mp3-192"),
    # Ohne Umwandlung – die einzige Wahl, wenn kein FFmpeg vorhanden ist.
    # YouTube liefert meist eine .m4a mit rund 130 kbit/s.
    AudioFormat("Original · ohne Umwandlung", None, 16_000),
)
DEFAULT_FORMAT = AUDIO_FORMATS[0]

# Puffer, der auf dem Ziellaufwerk frei bleiben soll
DISK_RESERVE_BYTES = 500 * 1024 * 1024
# Annahme für Songs, deren Länge im Export fehlt
AVERAGE_TRACK_SECONDS = 210

# Sichtbare Playlist-Zeilen. Jede Zeile kostet Aufbauzeit (400 Zeilen ≈ 19 s),
# geladen werden aber immer alle Songs.
MAX_VISIBLE_ROWS = 150

# Gleichzeitige Downloads. Mehr ist schneller, erhöht aber das Risiko, dass
# YouTube die Anfragen ausbremst.
WORKER_CHOICES = (1, 2, 3, 4, 6)
DEFAULT_WORKERS = 3

# --- Fehlerbehandlung ---
# Wie viele Suchtreffer pro Song durchprobiert werden, wenn einer nicht klappt
# (gesperrte, gelöschte oder altersbeschränkte Videos).
MAX_CANDIDATES = 3
# Wartezeiten vor erneutem Versuch bei 403/429 – YouTube drosselt kurzzeitig.
RETRY_DELAYS = (2, 6)

# Scheitern so viele Songs hintereinander, stimmt etwas Grundsätzliches nicht
# (IP gesperrt, Internet weg). Dann wird abgebrochen, statt den Rest der
# Playlist sinnlos durchzuprobieren.
MAX_CONSECUTIVE_FAILURES = 20

# Zufällige Pause (Sekunden) vor jedem Song einer Playlist. Ohne Pause fällt ein
# Lauf über tausende Songs YouTube als automatisiert auf und die IP wird gesperrt
# ("Sign in to confirm you're not a bot"). Auf (0, 0) setzen, um sie abzuschalten.
SLEEP_BETWEEN = (2, 6)

# Echtes Albumcover über iTunes/Deezer suchen, wenn der Export keine Bild-URL
# enthält. Ohne das bleibt nur das YouTube-Vorschaubild – bei Lyric-Videos also
# eine Textkarte statt eines Covers. Gesendet werden nur Interpret und Album.
ALBUM_ART_LOOKUP = True

# Auch für bereits vorhandene Songs ein Cover besorgen. Liefert der Export keine
# Bild-URL (manche Exportify-Einstellungen lassen "Album Image URL" weg), kostet
# das eine zusätzliche Suchanfrage je Song ohne Cover.
COVER_SEARCH_FOR_EXISTING = True

# Browser, aus denen Cookies gelesen werden können. Gegen die Altersprüfung ist
# das der einzige Weg – die alternativen Player-Clients (web_embedded, tv, ...)
# funktionieren dafür seit 2026 nicht mehr (nachgemessen).
# yt-dlp liest sie lokal selbst aus; die App sieht die Werte nicht.
COOKIE_BROWSERS = ("keine", "chrome", "firefox", "edge", "brave", "opera", "vivaldi")

# --- Farben ---
COLOR_ACCENT = "#e94560"
COLOR_ACCENT_HOVER = "#ff6b81"
COLOR_DOWNLOAD = "#2ecc71"
COLOR_DOWNLOAD_HOVER = "#27ae60"
COLOR_PLAYLIST = "#1DB954"
COLOR_PLAYLIST_HOVER = "#1ed760"

COLOR_SUCCESS = "#2ecc71"
COLOR_INFO = "#3498db"
COLOR_WARNING = "#f39c12"
COLOR_ERROR = "#e74c3c"
COLOR_MUTED = ("gray50", "gray60")  # (heller Modus, dunkler Modus)
