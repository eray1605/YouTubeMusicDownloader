"""Alles, was direkt mit yt-dlp spricht: Suche, Trefferauswahl, Download."""

import difflib
import os
import re
import sys
import time

from yt_dlp import YoutubeDL

from ytmd import config, verify
from ytmd.utils import audio_datei, get_ffmpeg_path, sanitize_filename


class QuietLogger:
    """Schluckt yt-dlps Konsolenausgabe.

    Fehler gehen ohnehin als Ausnahme an die App und landen gesammelt in der
    Zusammenfassung – ohne das hier flutet yt-dlp die Konsole mit Meldungen zu
    Versuchen, die danach erfolgreich wiederholt wurden.
    """

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


QUIET = QuietLogger()


class DownloadCancelled(Exception):
    """Wird aus dem Fortschritts-Hook geworfen, wenn der Nutzer abbricht."""


# --- Fehlerarten, nach denen sich das weitere Vorgehen richtet ---
BLOCKED = "blocked"            # HTTP 403 – Zugriff verweigert, oft nur vorübergehend
RATE_LIMITED = "rate_limited"  # HTTP 429 – zu viele Anfragen
BOT_CHECK = "bot_check"        # "Sign in to confirm you're not a bot" – IP ist auffällig
AGE_RESTRICTED = "age"         # Altersbeschränkung, braucht Cookies
UNAVAILABLE = "unavailable"    # gelöscht, gesperrt, privat
COOKIE_ERROR = "cookie_error"  # Cookies konnten nicht gelesen werden
NO_FORMAT = "no_format"        # keine Tonspur geliefert – meist Folge einer Sperre
NETWORK = "network"            # keine Verbindung, Zeitüberschreitung
FFMPEG_MISSING = "ffmpeg"      # FFmpeg fehlt – betrifft jeden Song
DISK = "disk"                  # kein Platz mehr auf dem Laufwerk
NO_MATCH = "no_match"          # nichts Passendes gefunden
OTHER = "other"

ERROR_LABELS = {
    BLOCKED: "Zugriff blockiert (403)",
    RATE_LIMITED: "Zu viele Anfragen (429)",
    BOT_CHECK: "Bot-Prüfung – YouTube blockt",
    AGE_RESTRICTED: "Altersbeschränkt",
    UNAVAILABLE: "Video nicht verfügbar",
    COOKIE_ERROR: "Cookies nicht lesbar",
    NO_FORMAT: "Keine Tonspur geliefert",
    NETWORK: "Keine Verbindung",
    FFMPEG_MISSING: "FFmpeg fehlt",
    DISK: "Kein Speicherplatz",
    NO_MATCH: "Kein Treffer gefunden",
    OTHER: "Sonstiger Fehler",
}

# Fehler, die den ganzen Lauf betreffen – hier ist Weitermachen sinnlos.
FATAL_ERRORS = (BOT_CHECK, FFMPEG_MISSING, DISK)

# Nur Vorübergehendes lohnt denselben Link noch einmal. Bei allem anderen
# (gesperrt, gelöscht, unbekannter Fehler) wird sofort der nächste Suchtreffer
# probiert, statt mehrfach ins Leere zu laufen.
RETRY_SAME_VIDEO = (BLOCKED, RATE_LIMITED, NETWORK)


class TrackError(Exception):
    """Fehlgeschlagener Song samt Ursache."""

    def __init__(self, message, category=OTHER):
        super().__init__(message)
        self.category = category


def classify_error(exc):
    """Ordnet eine yt-dlp-Fehlermeldung einer Ursache zu.

    Reihenfolge ist wichtig: Die Altersmeldung enthält ebenfalls "sign in",
    darf also nicht als Bot-Prüfung durchgehen.
    """
    text = str(exc).lower()
    if "confirm your age" in text or "age-restricted" in text or "inappropriate" in text:
        return AGE_RESTRICTED
    if "not a bot" in text or "confirm you" in text or "please sign in" in text:
        return BOT_CHECK
    if "no space left" in text or "errno 28" in text or "not enough space" in text:
        return DISK
    if "ffmpeg" in text or "ffprobe" in text or "postprocessing" in text:
        return FFMPEG_MISSING
    if "cookie" in text and ("could not" in text or "unable" in text or "failed" in text):
        return COOKIE_ERROR
    if "403" in text or "forbidden" in text:
        return BLOCKED
    if "429" in text or "too many requests" in text:
        return RATE_LIMITED
    # Muss vor UNAVAILABLE stehen – die Meldung enthält ebenfalls "not available".
    if "requested format" in text or "no video formats" in text or "list-formats" in text:
        return NO_FORMAT
    if ("urlopen error" in text or "getaddrinfo" in text or "timed out" in text
            or "connection" in text or "unreachable" in text or "name resolution" in text):
        return NETWORK
    if ("not available" in text or "private video" in text or "removed" in text
            or "unavailable" in text or "terminated" in text):
        return UNAVAILABLE
    return OTHER


def ytdlp_version():
    """Eingesetzte yt-dlp-Fassung – oder None, wenn sie sich nicht lesen lässt."""
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except Exception:
        return None


def is_frozen():
    """Läuft das Programm als eingepacktes Fertigprogramm (PyInstaller)?

    Entscheidend für den Rat bei einer Sperre: In der gepackten Fassung steckt
    yt-dlp fest im Programm und lässt sich nicht nachziehen – ein "pip install
    -U yt-dlp" hilft dort also nicht.
    """
    return getattr(sys, "frozen", False)


def stale_ytdlp_hint():
    """Rat für den Verdacht "yt-dlp ist zu alt für YouTubes aktuellen Player"."""
    fassung = ytdlp_version()
    stand = f" (eingesetzt: {fassung})" if fassung else ""
    if is_frozen():
        return ("Das trifft fast immer eine veraltete yt-dlp-Fassung"
                f"{stand}: YouTube ändert regelmäßig, wie die Tonspuren "
                "freigegeben werden.\n"
                "-> In diesem Fertigprogramm steckt yt-dlp fest eingebaut und "
                "kann sich nicht selbst erneuern. Es hilft nur eine neuere "
                "Fassung der App – oder der Start aus dem Quellcode nach "
                "\"pip install -U yt-dlp\".")
    return ("Das trifft fast immer eine veraltete yt-dlp-Fassung"
            f"{stand}: YouTube ändert regelmäßig, wie die Tonspuren "
            "freigegeben werden.\n"
            "-> \"pip install -U yt-dlp\" ausführen, danach die App neu "
            "starten. Bleibt es dabei, hilft \"yt-dlp --rm-cache-dir\".")


def error_hint(category):
    """Ein Satz Rat zu einer Fehlerart – für einzelne Songs.

    Bewusst getrennt von den Hinweisen der Playlist-Zusammenfassung: Dort kommt
    ein 403 meist von zu vielen gleichzeitigen Zugriffen. Bei einem einzelnen
    Link gibt es die nicht, deshalb steht hier der Verdacht auf ein veraltetes
    yt-dlp im Vordergrund.
    """
    if category in (BLOCKED, NO_FORMAT):
        return stale_ytdlp_hint()
    return {
        BOT_CHECK: ("YouTube stuft die Zugriffe als automatisiert ein.\n"
                    "-> 1–2 Stunden warten und bei \"Cookies\" den Browser "
                    "wählen, in dem du bei YouTube angemeldet bist."),
        RATE_LIMITED: "Zu viele Anfragen – ein paar Minuten warten.",
        AGE_RESTRICTED: ("Altersbeschränkt: geht nur mit angemeldetem Zugang.\n"
                         "-> Bei \"Cookies\" den Browser wählen, in dem du bei "
                         "YouTube angemeldet bist."),
        UNAVAILABLE: "Dieses Video ist nicht mehr abrufbar – anderes probieren.",
        COOKIE_ERROR: "-> Den gewählten Browser komplett schließen und erneut versuchen.",
        FFMPEG_MISSING: ("FFmpeg fehlt, deshalb ist keine Umwandlung möglich.\n"
                         "-> FFmpeg installieren und die App neu starten, oder "
                         "als Format \"Original\" wählen."),
        NETWORK: "Keine Verbindung – Internetverbindung prüfen.",
        DISK: "Kein Speicherplatz mehr auf dem Ziellaufwerk.",
        NO_MATCH: "Kein sicher passender Treffer – anderen Suchbegriff probieren.",
    }.get(category)


def search_youtube(query, limit=config.NUM_RESULTS):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'logger': QUIET,
        'extract_flat': 'in_playlist',
        'default_search': f'ytsearch{limit}',
    }
    with YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(query, download=False)
            results = []
            for entry in info.get('entries', []):
                if entry:
                    # Bei flat extraction webpage_url aus ID bauen falls nötig
                    if 'webpage_url' not in entry and 'url' in entry:
                        entry['webpage_url'] = entry['url']
                    elif 'webpage_url' not in entry and 'id' in entry:
                        entry['webpage_url'] = f"https://www.youtube.com/watch?v={entry['id']}"
                    results.append(entry)
            return results
        except Exception as e:
            print(f"Fehler bei der Suche: {e}")
            return []


def result_url(result):
    """URL eines Suchtreffers – auch wenn nur die Video-ID geliefert wurde."""
    return result.get("webpage_url") or f"https://www.youtube.com/watch?v={result['id']}"


def thumbnail_url(result):
    """Vorschaubild eines Treffers – notfalls aus der Video-ID gebaut."""
    if not result:
        return ""
    url = result.get("thumbnail")
    if url:
        return url
    thumbs = result.get("thumbnails")
    if thumbs:
        return thumbs[-1].get("url", "")
    if result.get("id"):
        return f"https://i.ytimg.com/vi/{result['id']}/hqdefault.jpg"
    return ""


def normalize(text):
    """Kleinschreibung, ohne Satzzeichen – auch typografische Apostrophe fliegen
    raus, damit "Don't" und "Don’t" gleich behandelt werden.

    Buchstaben aller Schriften bleiben erhalten: Ein Titel wie
    "爆破ミッション" darf nicht zu einer leeren Zeichenkette werden.
    """
    text = str(text or "").lower().replace("’", "").replace("'", "").replace("`", "")
    return re.sub(r"[\W_]+", " ", text, flags=re.UNICODE).strip()


def title_variants(title):
    """Schreibweisen, unter denen ein Titel auf YouTube stehen kann.

    Spotify führt oft Original und Übersetzung zusammen –
    "爆破ミッション (Bombing Mission)" –, während auf YouTube nur eine der
    beiden Fassungen im Titel steht.
    """
    varianten = [title]
    ohne_klammern = re.sub(r"[\(\[].*?[\)\]]", " ", title).strip()
    in_klammern = " ".join(re.findall(r"[\(\[](.*?)[\)\]]", title)).strip()
    for variante in (ohne_klammern, in_klammern):
        if len(normalize(variante).replace(" ", "")) >= 3:
            varianten.append(variante)
    return varianten


def _words(text):
    return set(normalize(text).split())


def _similarity(wanted, candidate):
    want = normalize(wanted).replace(" ", "")
    have = normalize(candidate).replace(" ", "")
    if not want:
        return 0.0
    if want in have:
        return 1.0
    matcher = difflib.SequenceMatcher(None, want, have, autojunk=False)
    gefunden = sum(block.size for block in matcher.get_matching_blocks())
    return min(1.0, gefunden / len(want))


def title_similarity(wanted, candidate):
    """Wie vollständig steckt `wanted` im Titel `candidate`? 0.0 bis 1.0.

    Wortwörtlich zu vergleichen scheitert an der Praxis: Spotify schreibt
    "C R E E D", YouTube "CREED"; YouTube entschärft "Fuck Tha Police" zu
    "Fuk Da Police". Deshalb wird ohne Leerzeichen verglichen und gemessen,
    wie viel des gesuchten Titels sich der Reihe nach wiederfindet – und das
    für jede Schreibweise des Titels, nicht nur die vollständige.
    """
    return max(_similarity(variante, candidate) for variante in title_variants(wanted))


def score_result(result, position, title, artists=(), duration=None):
    """Punktzahl dafür, wie sicher der Treffer wirklich dieser Song ist.

    Negativ oder unter der Mindestpunktzahl heißt: lieber nicht herunterladen.
    """
    candidate_title = result.get("title", "")
    channel = result.get("channel") or result.get("uploader") or ""
    candidate_words = _words(candidate_title)

    # Harte Bedingung: Der Songtitel muss im Treffer stecken. Ohne das landet
    # bei "BHZ - So Leben Kann" sonst "BHZ - SO HOCH" im Ordner.
    wanted = _words(title)
    aehnlichkeit = title_similarity(title, candidate_title)
    if not wanted or aehnlichkeit < config.MIN_TITLE_SIMILARITY:
        return None

    # Je unschärfer der Titel passt, desto mehr Abzug
    score = int(-60 * (1.0 - aehnlichkeit))

    # Länge – das stärkste Einzelsignal, wenn Spotify sie mitliefert
    length = result.get("duration")
    if duration and length:
        delta = abs(length - duration)
        if delta <= 3:
            score += 45
        elif delta <= 8:
            score += 35
        elif delta <= 20:
            score += 20
        elif delta <= 40:
            score += 0
        elif delta <= 90:
            score -= 35
        else:
            score -= 80

    # Interpret: im Kanalnamen ist das Signal deutlich stärker als im Titel
    channel_words = _words(channel)
    artist_word_sets = [_words(a) for a in artists if a]
    in_channel = any(w and w <= channel_words for w in artist_word_sets)
    in_title = any(w and w <= candidate_words for w in artist_word_sets)
    if in_channel:
        score += 30
        if normalize(channel).endswith(" topic"):
            score += 25  # automatisch erzeugter Musikkanal – praktisch immer das Original
    elif in_title:
        score += 15
    else:
        score -= 25

    # Hinweise auf eine offizielle Tonspur
    lowered = normalize(candidate_title)
    if "official audio" in lowered:
        score += 20
    elif "official" in lowered:
        score += 8
    elif "audio" in lowered:
        score += 8

    # Andere Fassung? Nur zählen, wenn das Wort nicht schon im Songtitel steht.
    for word in config.WRONG_VERSION_WORDS:
        if word in candidate_words and word not in wanted:
            score -= 100

    # "Fremder Interpret - Unser Titel" ist fast immer ein anderer Song
    if artists and foreign_artist_prefix(candidate_title, title, artists):
        score -= 60

    # Relevanz der Suche als leichter Ausgleich bei Gleichstand
    score += max(0, 8 - 2 * position)
    return score


def foreign_artist_prefix(candidate_title, title, artists=()):
    """Steht vor dem Titel ein fremder Interpret? ("Oxana - Ich träum von dir")

    Ohne diese Prüfung gewinnt bei der Suche nur nach dem Titel leicht der
    gleichnamige Song einer anderen Künstlerin, wenn die Länge zufällig passt.
    """
    teile = re.split(r"\s+[-–—]\s+", candidate_title, maxsplit=1)
    if len(teile) < 2:
        return False
    prefix_words = _words(teile[0])
    if not prefix_words:
        return False
    # Steht dort unser eigener Titel, ist es kein Interpretenname
    if title_similarity(title, teile[0]) >= 0.9:
        return False
    return not any(w and w <= prefix_words for w in (_words(a) for a in artists if a))


def is_strong_match(result, title, artists=(), duration=None):
    """Eindeutig derselbe Song – auch wenn die Länge abweicht?

    Bewusst streng: Der Titel muss wörtlich enthalten sein, der Interpret im
    Kanalnamen stehen (nicht bloß im Videotitel, das kann jeder schreiben) und
    nichts auf eine andere Fassung hindeuten.
    """
    candidate_title = result.get("title", "")
    if title_similarity(title, candidate_title) < 1.0:
        return False

    channel_words = _words(result.get("channel") or result.get("uploader") or "")
    if not any(w and w <= channel_words for w in (_words(a) for a in artists if a)):
        return False

    candidate_words = _words(candidate_title)
    wanted = _words(title)
    if any(w in candidate_words and w not in wanted for w in config.WRONG_VERSION_WORDS):
        return False

    # Ganze Alben und Stundenloops bleiben draußen
    length = result.get("duration")
    if length and length > max(config.RELAXED_MAX_SECONDS, duration or 0):
        return False
    return True


def find_matches(query, title=None, artists=(), duration=None, fallback_query=None):
    """Treffer, die wirklich dieser Song sein können – bester zuerst.

    Leere Liste heißt: nichts Passendes gefunden. Dann wird bewusst nichts
    heruntergeladen, statt irgendein gleichnamiges Video zu speichern.
    """
    def bewerten(results):
        scored = []
        for position, result in enumerate(results):
            score = score_result(result, position, title or query, artists, duration)
            if score is not None and score >= config.MIN_MATCH_SCORE:
                scored.append((-score, position, result))
        scored.sort()
        return [result for _, _, result in scored]

    gesehen = {}

    def suchen(begriff):
        ergebnisse = search_youtube(begriff, limit=config.PLAYLIST_CANDIDATES)
        for r in ergebnisse:
            gesehen.setdefault(r.get("id") or r.get("url"), r)
        return ergebnisse

    treffer = bewerten(suchen(query)) if query else []
    if treffer:
        return treffer

    # Auch wenn die Suche etwas geliefert hat, kann alles daneben liegen: Bei
    # Soundtracks verdrängt der Komponistenname die Titelsuche. Dann noch einmal
    # ohne Interpret suchen.
    if fallback_query and normalize(fallback_query) != normalize(query):
        treffer = bewerten(suchen(fallback_query))
        if treffer:
            return treffer

    # Letzter Versuch ohne Längenprüfung – nur für zweifelsfreie Treffer.
    if config.RELAXED_FALLBACK:
        return [r for r in gesehen.values()
                if is_strong_match(r, title or query, artists, duration)]
    return []


def download_audio(url, output_path, filename_base=None, should_cancel=None,
                   audio_format=None, cookies_from_browser=None, throttle=False):
    """Download one URL as audio file. Returns True if the file already existed."""
    audio_format = audio_format or config.DEFAULT_FORMAT

    if filename_base:
        safe = sanitize_filename(filename_base)
        vorhanden = audio_datei(output_path, safe, audio_format.codec)
        if vorhanden:
            if verify.is_complete(vorhanden):
                return True
            # Rest eines abgebrochenen Laufs – weg damit, sonst gilt die halbe
            # Datei für immer als fertig.
            try:
                os.remove(vorhanden)
            except OSError:
                pass
        outtmpl = os.path.join(output_path, safe.replace("%", "%%") + ".%(ext)s")
    else:
        outtmpl = os.path.join(output_path, "%(title)s.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio/best',
        # Ausdrücklich nach Bitrate und Abtastrate sortieren, damit immer die
        # beste Tonspur gewählt wird und nicht die zuerst gelistete.
        'format_sort': ['abr', 'asr'],
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'logger': QUIET,
        # YouTube liefert bei zu vielen Anfragen zeitweise 403/429 – yt-dlp soll
        # es selbst noch einmal versuchen, bevor wir den Song aufgeben.
        'retries': 5,
        'fragment_retries': 5,
        'extractor_retries': 3,
        'socket_timeout': 30,
    }

    # Ohne Codec wird nicht umgewandelt: Die Tonspur wird so gespeichert, wie
    # YouTube sie liefert (meist .m4a). Das ist der einzige Weg ohne FFmpeg.
    if audio_format.codec:
        postprocessor = {
            'key': 'FFmpegExtractAudio',
            'preferredcodec': audio_format.codec,
        }
        if audio_format.quality:
            postprocessor['preferredquality'] = audio_format.quality
        ydl_opts['postprocessors'] = [postprocessor]
        if audio_format.args:
            ydl_opts['postprocessor_args'] = {'extractaudio': list(audio_format.args)}
    if cookies_from_browser:
        ydl_opts['cookiesfrombrowser'] = (cookies_from_browser,)
    if throttle and config.SLEEP_BETWEEN[1]:
        # Kurze zufällige Pause, damit ein langer Lauf nicht als Bot auffällt
        ydl_opts['sleep_interval'], ydl_opts['max_sleep_interval'] = config.SLEEP_BETWEEN
    ffmpeg_dir = get_ffmpeg_path()
    if ffmpeg_dir:
        ydl_opts['ffmpeg_location'] = ffmpeg_dir

    if should_cancel:
        def hook(_status):
            if should_cancel():
                raise DownloadCancelled()
        ydl_opts['progress_hooks'] = [hook]

    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return False


def download_with_retries(url, output_path, on_retry=None, should_cancel=None, **kwargs):
    """Wie `download_audio`, aber mit Pausen bei kurzzeitigen Sperren.

    403, 429 und Verbindungsabbrüche sind meist nur Drosselung: Nach einer
    Pause klappt derselbe Link oft doch, weil yt-dlp die Medien-URL dabei neu
    auflöst. Alles andere – gesperrt, gelöscht, altersbeschränkt – wird sofort
    durchgereicht, statt mehrfach ins Leere zu laufen.

    `on_retry(versuch, ursache)` meldet jeden weiteren Anlauf, damit die
    Oberfläche "Neuer Versuch" anzeigen kann.

    Scheitert es endgültig, fliegt ein `TrackError` mit der Ursache – die
    rohe yt-dlp-Meldung bleibt als Text erhalten.
    """
    for attempt in range(len(config.RETRY_DELAYS) + 1):
        if should_cancel and should_cancel():
            raise DownloadCancelled()
        try:
            return download_audio(url, output_path, should_cancel=should_cancel, **kwargs)
        except DownloadCancelled:
            raise
        except Exception as e:
            category = classify_error(e)
            if category not in RETRY_SAME_VIDEO or attempt >= len(config.RETRY_DELAYS):
                raise TrackError(str(e), category) from e
            if on_retry:
                on_retry(attempt + 1, category)
            time.sleep(config.RETRY_DELAYS[attempt])
