"""Ablaufsteuerung für den Playlist-Download – bewusst ohne UI-Abhängigkeiten.

Der Fortschritt wird über Callbacks gemeldet; die Oberfläche entscheidet selbst,
wie sie die Zustände darstellt.

Mehrere Songs werden parallel geladen. Die Reihenfolge der Playlist steckt in der
Nummer im Dateinamen (`001 - ...`) und bleibt dadurch unabhängig davon erhalten,
in welcher Reihenfolge die Downloads tatsächlich fertig werden.
"""

import os
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ytmd import config
from ytmd import tags, verify
from ytmd.albumart import find_cover_url
from ytmd.covers import fetch_cover
from ytmd.utils import audio_datei, free_bytes, get_download_folder, sanitize_filename
from ytmd.youtube import (FATAL_ERRORS, NO_MATCH, DownloadCancelled, TrackError,
                          classify_error, download_with_retries, find_matches,
                          result_url, thumbnail_url)

# Zustände, die per on_status gemeldet werden
SEARCHING = "searching"
DOWNLOADING = "downloading"
RETRYING = "retrying"
DONE = "done"
SKIPPED = "skipped"
FAILED = "failed"
CANCELLED = "cancelled"
NO_SPACE = "no_space"

# Gründe für einen vorzeitigen Abbruch (neben den Fehlerarten aus youtube.py)
DISK_FULL = "disk_full"
MASS_FAILURE = "mass_failure"

# Führende Positionsnummer im Dateinamen ("0042 - Artist - Titel")
NUMBER_PREFIX = re.compile(r"^\d+ - ")


def estimate_bytes(tracks, audio_format):
    """Grobe Größe der gesamten Playlist im gewählten Format."""
    seconds = sum(t.duration or config.AVERAGE_TRACK_SECONDS for t in tracks)
    return int(seconds * audio_format.bytes_per_second)


@dataclass
class PlaylistResult:
    folder: str
    downloaded: int = 0
    skipped: int = 0
    covers: int = 0        # eingebettete Cover
    covers_failed: int = 0  # Songs, bei denen kein Cover zu bekommen war
    failed: List[str] = field(default_factory=list)
    # Ursache -> Anzahl, z. B. {"blocked": 39, "age": 5}
    failed_categories: Dict[str, int] = field(default_factory=dict)
    cancelled: bool = False
    stopped_reason: Optional[str] = None
    remaining: int = 0  # noch nicht abgearbeitete Songs


class PlaylistDownloader:
    """Lädt alle Tracks einer Playlist in einen eigenen Ordner.

    on_status(index, state)     -> Zustand eines einzelnen Songs
    on_progress(done, total)    -> `done` von `total` Songs sind abgearbeitet

    Beide Callbacks werden aus den Worker-Threads aufgerufen und müssen damit
    zurechtkommen (die Oberfläche reicht sie über eine Queue an den UI-Thread).
    """

    def __init__(self, tracks, playlist_name, audio_format=None, target_root=None,
                 workers=None, cookies_from_browser=None, save_covers=True,
                 on_status=None, on_progress=None):
        self.tracks = tracks
        self.playlist_name = playlist_name
        self.audio_format = audio_format or config.DEFAULT_FORMAT
        self.target_root = target_root or get_download_folder()
        self.workers = max(1, int(workers or config.DEFAULT_WORKERS))
        self.cookies_from_browser = cookies_from_browser
        self.save_covers = save_covers
        self._on_status = on_status
        self._on_progress = on_progress

        self._cancelled = False
        self._stop_reason = None  # gesetzt, sobald Weitermachen sinnlos ist
        self._streak = 0          # Fehler in Folge
        self._lock = threading.Lock()
        self._processed = 0
        self._failed = []  # (index, text) – am Ende nach Playlist-Position sortiert
        self._categories = Counter()
        self._existing = {}  # Songname (klein) -> vorhandene Datei

    # --- Steuerung ---
    def cancel(self):
        self._cancelled = True

    @property
    def cancelled(self):
        return self._cancelled

    def target_folder(self):
        return os.path.join(self.target_root,
                            sanitize_filename(self.playlist_name or "Playlist"))

    def _track_bytes(self, track):
        seconds = track.duration or config.AVERAGE_TRACK_SECONDS
        return int(seconds * self.audio_format.bytes_per_second)

    # --- Bereits geladene Songs wiederfinden ---
    def _scan_existing(self, folder):
        """Vorhandene Songs einlesen – anhand des Namens ohne Positionsnummer.

        Dadurch wird ein Song auch dann erkannt, wenn er in der Playlist
        inzwischen an einer anderen Stelle steht.
        """
        found = {}
        # Ohne Umwandlung bestimmt YouTube die Endung, deshalb alle zulassen
        suffixe = (["." + self.audio_format.codec] if self.audio_format.codec
                   else list(config.AUDIO_EXTENSIONS))
        try:
            names = os.listdir(folder)
        except OSError:
            return found
        for name in names:
            suffix = next((s for s in suffixe if name.lower().endswith(s)), None)
            if suffix is None:
                continue
            # Halbe Datei aus einem abgebrochenen Lauf gilt nicht als erledigt
            if not verify.is_complete(os.path.join(folder, name)):
                continue
            stem = name[:-len(suffix)]
            key = NUMBER_PREFIX.sub("", stem)
            if key != stem:  # nur nummerierte Playlist-Dateien
                found[key.lower()] = name
        return found

    def _reuse_existing(self, folder, safe_name):
        """Song schon vorhanden? Dann ggf. auf die neue Position umbenennen.

        Jede Datei wird nur einmal beansprucht – sonst würde bei doppelt
        enthaltenen Songs die zweite Kopie fälschlich als erledigt gelten.
        """
        key = NUMBER_PREFIX.sub("", safe_name).lower()
        with self._lock:
            old_name = self._existing.pop(key, None)
        if not old_name:
            return False

        # Ohne Umwandlung behält die Datei ihre eigene Endung
        endung = ("." + self.audio_format.codec if self.audio_format.codec
                  else os.path.splitext(old_name)[1])
        wanted = safe_name + endung
        if old_name != wanted:
            old_path = os.path.join(folder, old_name)
            new_path = os.path.join(folder, wanted)
            if os.path.exists(old_path) and not os.path.exists(new_path):
                try:
                    os.rename(old_path, new_path)
                except OSError:
                    pass  # Nummer bleibt alt, Hauptsache kein erneuter Download
        return True

    def _has_space_for(self, track, folder):
        """Platz für diesen Song plus Reserve? Beim Umwandeln liegen Quelle und
        Zieldatei kurz gleichzeitig auf der Platte, und es laufen mehrere
        Downloads parallel – beides wird eingerechnet."""
        needed = self._track_bytes(track) * 2 * self.workers + config.DISK_RESERVE_BYTES
        return free_bytes(folder) >= needed

    # --- Ablauf ---
    def run(self):
        """Blockiert bis alles geladen ist – gehört in einen eigenen Thread.

        Wirft OSError, wenn der Zielordner nicht angelegt werden kann.
        """
        folder = self.target_folder()
        os.makedirs(folder, exist_ok=True)

        result = PlaylistResult(folder=folder)
        total = len(self.tracks)
        width = max(2, len(str(total)))  # 01, 02 ... bzw. 001 bei langen Playlists
        self._existing = self._scan_existing(folder)

        with ThreadPoolExecutor(max_workers=self.workers,
                                thread_name_prefix="ytmd-dl") as pool:
            futures = [pool.submit(self._process, i, track, folder, width, result, total)
                       for i, track in enumerate(self.tracks)]
            for future in futures:
                future.result()  # Fehler einzelner Songs werden intern behandelt

        result.failed = [text for _, text in sorted(self._failed)]
        result.failed_categories = dict(self._categories)
        result.cancelled = self._cancelled
        result.stopped_reason = self._stop_reason
        result.remaining = total - self._processed
        return result

    def _process(self, index, track, folder, width, result, total):
        """Ein Song – läuft in einem Worker-Thread."""
        if self._cancelled or self._stop_reason:
            return

        filename_base = sanitize_filename(f"{index + 1:0{width}d} - {track.label}")

        # Schon aus einem früheren Lauf vorhanden? Dann nichts erneut laden.
        if self._reuse_existing(folder, filename_base):
            # Cover kann trotzdem fehlen, wenn der Song vor dieser Funktion kam
            self._write_tags(track, folder, filename_base, index)
            self._save_cover(track, folder, filename_base, result)
            with self._lock:
                result.skipped += 1
                self._processed += 1
                done = self._processed
            self._status(index, SKIPPED)
            self._progress(done, total)
            return

        # Lieber sauber stoppen, als das Laufwerk komplett vollzuschreiben.
        if not self._has_space_for(track, folder):
            self._stop_reason = DISK_FULL
            self._status(index, NO_SPACE)
            return

        self._status(index, SEARCHING)
        try:
            existed, candidate = self._download_track(index, track, folder, filename_base)
            self._write_tags(track, folder, filename_base, index)
            self._save_cover(track, folder, filename_base, result, candidate)
            with self._lock:
                self._streak = 0
                if existed:
                    result.skipped += 1
                else:
                    result.downloaded += 1
            self._status(index, SKIPPED if existed else DONE)
        except DownloadCancelled:
            self._status(index, CANCELLED)
            return
        except Exception as e:
            category = e.category if isinstance(e, TrackError) else classify_error(e)
            with self._lock:
                self._failed.append((index, f"{index + 1}. {track.label} ({e})"))
                self._categories[category] += 1
                self._streak += 1
                streak = self._streak
            self._status(index, FAILED, (category, str(e)))

            # Manche Fehler betreffen jeden weiteren Song – dann sofort stoppen,
            # statt den Rest der Playlist sinnlos durchzuprobieren.
            if category in FATAL_ERRORS:
                self._stop_reason = category
            elif streak >= config.MAX_CONSECUTIVE_FAILURES:
                self._stop_reason = MASS_FAILURE

        with self._lock:
            self._processed += 1
            done = self._processed
        self._progress(done, total)

        # Sicherheitsnetz, falls das Laufwerk während des Downloads volllief
        if free_bytes(folder) < config.DISK_RESERVE_BYTES:
            self._stop_reason = DISK_FULL

    def _write_tags(self, track, folder, filename_base, index):
        """Titel, Interpret, Album und Datum eintragen.

        Ohne das zeigt jeder Player "Unknown", obwohl die Angaben im Export
        stehen. Läuft auch für schon vorhandene Dateien, die noch keine haben.
        """
        if not tags.AVAILABLE:
            return
        path = audio_datei(folder, filename_base, self.audio_format.codec)
        if not path or tags.has_tags(path):
            return
        tags.write_tags(path, title=track.title, artist=track.artist,
                        album=track.album, released=track.released,
                        track_number=index + 1)

    def _save_cover(self, track, folder, filename_base, result, candidate=None):
        """Cover in die Audiodatei einbetten: Spotify-Bild, sonst YouTube-Bild."""
        if not self.save_covers or not tags.AVAILABLE:
            return
        path = audio_datei(folder, filename_base, self.audio_format.codec)
        if not path or tags.has_cover(path):
            return

        # 1. Bild aus dem Export, 2. echtes Albumcover, 3. YouTube-Vorschaubild.
        # Das Vorschaubild ist die Notlösung: Bei Lyric-Videos ist es nur eine
        # Textkarte statt eines Covers.
        url = track.image_url
        if not url and config.ALBUM_ART_LOOKUP:
            url = find_cover_url(track.primary_artist, track.album)
        if not url:
            url = thumbnail_url(candidate)
        if not url and config.COVER_SEARCH_FOR_EXISTING and not self._cancelled:
            # Schon vorhandener Song: Es gab keinen Treffer, aus dem ein
            # Vorschaubild stammen könnte – also einmal nachsehen.
            url = thumbnail_url(self._search_cover(track))

        image = fetch_cover(url)
        geschafft = bool(image) and tags.embed_cover(path, image)
        with self._lock:
            if geschafft:
                result.covers += 1
            else:
                result.covers_failed += 1

    def _search_cover(self, track):
        """Treffer nur zum Zweck des Vorschaubilds suchen (kein Download)."""
        try:
            matches = find_matches(track.search_query,
                                   title=track.clean_title,
                                   artists=track.artist_names,
                                   duration=track.duration,
                                   fallback_query=track.clean_title)
        except Exception:
            return None
        return matches[0] if matches else None

    def _download_track(self, index, track, folder, filename_base):
        """Song laden und dabei Rückschläge abfangen.

        Gesperrte oder altersbeschränkte Videos bringt ein erneuter Versuch nicht
        weiter – dann wird der nächste Suchtreffer genommen. Ein 403/429 ist
        dagegen meist nur eine kurzzeitige Drosselung und wird nach einer Pause
        wiederholt (dabei löst yt-dlp die Medien-URL neu auf).
        """
        candidates = find_matches(track.search_query,
                                  title=track.clean_title,
                                  artists=track.artist_names,
                                  duration=track.duration,
                                  fallback_query=track.clean_title)
        if not candidates:
            raise TrackError("Kein sicher passender Treffer gefunden", NO_MATCH)

        last_error = None
        last_category = None

        for candidate in candidates[:config.MAX_CANDIDATES]:
            if self._cancelled:
                raise DownloadCancelled()
            if last_error:
                self._status(index, RETRYING)
            try:
                # Die Pausen bei 403/429 stecken in download_with_retries; hier
                # bleibt nur der Wechsel zum nächsten Suchtreffer, wenn ein Video
                # endgültig nicht geht (gesperrt, gelöscht, altersbeschränkt).
                existed = download_with_retries(
                    result_url(candidate), folder,
                    on_retry=lambda *_: self._status(index, RETRYING),
                    should_cancel=lambda: self._cancelled,
                    filename_base=filename_base,
                    audio_format=self.audio_format,
                    cookies_from_browser=self.cookies_from_browser,
                    throttle=True,
                )
                return existed, candidate
            except DownloadCancelled:
                raise
            except TrackError as e:
                last_error, last_category = e, e.category

        raise TrackError(str(last_error), last_category)

    # --- Callbacks ---
    def _status(self, index, state, detail=None):
        """`detail` trägt bei FAILED die Ursache (siehe youtube.ERROR_LABELS)."""
        if self._on_status:
            self._on_status(index, state, detail)

    def _progress(self, done, total):
        if self._on_progress:
            self._on_progress(done, total)
