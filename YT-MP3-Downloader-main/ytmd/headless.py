"""Playlist-Download ohne Oberfläche.

Dies ist die Naht für andere Plattformen: Android ruft `run_playlist` aus Kotlin
heraus auf (über Chaquopy) und bekommt den Fortschritt per Callback zurück – die
Oberfläche wird dort nativ gebaut, die Logik bleibt diese hier.

Auf dem Desktop lässt sich dieselbe Funktion über die Kommandozeile nutzen:

    python -m ytmd.headless meine_playlist.csv --format mp3 --workers 2
"""

import argparse
import os
import sys

from ytmd import config, utils
from ytmd.downloader import PlaylistDownloader
from ytmd.playlist import parse_playlist_file


def format_by_name(name):
    """Zielformat über einen kurzen Namen wählen ("mp3", "wav", "flac")."""
    if not name:
        return config.DEFAULT_FORMAT
    gesucht = name.strip().lower()
    if gesucht in ("original", "auto", "none", ""):
        return next(f for f in config.AUDIO_FORMATS if f.codec is None)
    for audio_format in config.AUDIO_FORMATS:
        if gesucht in (audio_format.kennung, audio_format.label.lower()):
            return audio_format
    # "wav" und "mp3" ohne Zusatz treffen den erstbesten Eintrag
    for audio_format in config.AUDIO_FORMATS:
        if audio_format.codec == gesucht:
            return audio_format
    erlaubt = ", ".join(f.kennung for f in config.AUDIO_FORMATS)
    raise ValueError(f"Unbekanntes Format {name!r}. Möglich: {erlaubt}")


def run_playlist(playlist_file, target_folder=None, audio_format=None, workers=None,
                 cookies_from_browser=None, save_covers=True, ffmpeg_path=None,
                 on_status=None, on_progress=None):
    """Playlist einlesen und herunterladen. Gibt ein PlaylistResult zurück.

    Blockiert bis zum Ende – der Aufrufer sorgt für den Hintergrund-Thread
    (auf Android der Foreground Service).
    """
    if ffmpeg_path:
        utils.set_ffmpeg_path(ffmpeg_path)

    name, tracks = parse_playlist_file(playlist_file)
    if not tracks:
        raise ValueError(f"Keine Songs in {playlist_file} erkannt")

    job = PlaylistDownloader(
        tracks, name,
        audio_format=format_by_name(audio_format) if isinstance(audio_format, str)
        else audio_format,
        target_root=target_folder,
        workers=workers,
        cookies_from_browser=cookies_from_browser,
        save_covers=save_covers,
        on_status=on_status,
        on_progress=on_progress,
    )
    return job.run()


def suche(query, limit=None):
    """Suchergebnisse als JSON – Gegenstück zur Suchleiste der Desktop-App."""
    import json

    from ytmd.utils import format_duration
    from ytmd.youtube import result_url, search_youtube, thumbnail_url

    treffer = search_youtube(query, limit=limit or config.NUM_RESULTS)
    return json.dumps([{
        "title": r.get("title", ""),
        "channel": r.get("channel") or r.get("uploader") or "",
        "duration": format_duration(r.get("duration")),
        "url": result_url(r),
        "thumb": thumbnail_url(r),
    } for r in treffer])


def fehlertext(exc):
    """Rohen yt-dlp-Fehler in eine verständliche Meldung samt Rat übersetzen.

    Ursache und Rat kommen aus youtube.py, damit Android und Desktop bei
    demselben Fehler dasselbe erzählen.
    """
    from ytmd import youtube

    art = exc.category if isinstance(exc, youtube.TrackError) else youtube.classify_error(exc)
    text = youtube.ERROR_LABELS.get(art, "Fehler")
    rat = youtube.error_hint(art)
    return f"{text}\n{rat}" if rat else text


def einzeln_laden(url, target_folder, audio_format="wav", ffmpeg_path=None):
    """Einen einzelnen Song laden – wie das URL-Feld der Desktop-App."""
    from ytmd.youtube import download_with_retries

    if ffmpeg_path:
        utils.set_ffmpeg_path(ffmpeg_path)
    os.makedirs(target_folder, exist_ok=True)
    try:
        download_with_retries(url, target_folder,
                              audio_format=format_by_name(audio_format))
    except Exception as e:
        return fehlertext(e)
    return "Fertig – gespeichert in " + target_folder


def playlist_info(playlist_file, audio_format="wav", max_tracks=300):
    """Kurzinfo für eine Oberfläche, als JSON.

    Damit zeigt die Android-App dieselben Angaben wie die Desktop-Version:
    Playlistname, Anzahl, geschätzter Platzbedarf und die Songliste.
    """
    import json

    from ytmd.downloader import estimate_bytes
    from ytmd.utils import format_duration, format_size

    name, tracks = parse_playlist_file(playlist_file)
    fmt = format_by_name(audio_format)
    return json.dumps({
        "name": name,
        "count": len(tracks),
        "size": format_size(estimate_bytes(tracks, fmt)),
        "tracks": [{"title": t.title,
                    "artist": t.artist,
                    "duration": format_duration(t.duration)}
                   for t in tracks[:max_tracks]],
    })


def cover_lesen(pfad):
    """Eingebettetes Cover als Bytes – oder None.

    Wird beim Umwandeln auf dem Telefon gebraucht: Das Bild muss vor der
    Umwandlung gesichert und danach wieder eingesetzt werden, weil aus der
    Tonspur nur rohe Abtastwerte entstehen.
    """
    try:
        import mutagen
        datei = mutagen.File(pfad)
        if datei is None or not getattr(datei, "tags", None):
            return None
        # MP4 legt das Bild unter "covr" ab, ID3 unter "APIC:"
        covr = datei.tags.get("covr")
        if covr:
            return bytes(covr[0])
        for schluessel in datei.tags.keys():
            if str(schluessel).startswith("APIC"):
                return datei.tags[schluessel].data
        bilder = getattr(datei, "pictures", None)
        if bilder:
            return bilder[0].data
    except Exception:
        return None
    return None


def cover_schreiben(pfad, daten):
    """Cover in eine Datei einbetten. True bei Erfolg."""
    from ytmd import tags

    if not daten:
        return False
    return tags.embed_cover(pfad, bytes(daten))


def selbsttest():
    """Kurzbericht, ob der Kern einsatzbereit ist.

    Auf Android die einzige Möglichkeit, ohne Download zu sehen, ob die
    Python-Laufzeit samt Abhängigkeiten wirklich hochkommt.
    """
    import platform

    teile = [f"Python {platform.python_version()}"]
    try:
        import yt_dlp
        teile.append(f"yt-dlp {yt_dlp.version.__version__}")
    except Exception as e:
        teile.append(f"yt-dlp FEHLT ({e})")
    for name in ("requests", "mutagen"):
        try:
            __import__(name)
            teile.append(name)
        except Exception:
            teile.append(f"{name} FEHLT")

    from ytmd import tags
    teile.append("Cover: " + ("ja" if tags.AVAILABLE else "nein"))
    if utils.ffmpeg_available():
        teile.append("FFmpeg: ja")
    else:
        teile.append("ohne FFmpeg – Format \"Original\"")
    return " · ".join(teile)


def formate():
    """Wählbare Formate als JSON – ohne FFmpeg bleibt nur "Original"."""
    import json

    umwandeln = utils.ffmpeg_available()
    return json.dumps([
        {"name": f.kennung,
         "label": f.label,
         "available": bool(umwandeln or f.codec is None)}
        for f in config.AUDIO_FORMATS
    ])


def run_for_listener(playlist_file, target_folder, audio_format, workers, listener,
                     cookies_from_browser=None, save_covers=True, ffmpeg_path=None):
    """Einstieg für Android (Chaquopy).

    Kotlin kann keine Python-Funktion als Rückruf übergeben, wohl aber ein
    Objekt. Hier wird es auf die Callbacks des Kerns umgesetzt.
    """
    def status(index, state, detail=None):
        text = detail[0] if isinstance(detail, tuple) else detail
        listener.onStatus(index, state, str(text) if text else None)

    ergebnis = run_playlist(
        playlist_file, target_folder=target_folder, audio_format=audio_format,
        workers=workers, cookies_from_browser=cookies_from_browser,
        save_covers=save_covers, ffmpeg_path=ffmpeg_path,
        on_status=status,
        on_progress=lambda done, total: listener.onProgress(done, total))

    zusammenfassung = (f"Geladen: {ergebnis.downloaded} · Übersprungen: "
                       f"{ergebnis.skipped} · Fehler: {len(ergebnis.failed)} · "
                       f"Cover: {ergebnis.covers}")
    if ergebnis.stopped_reason:
        zusammenfassung += (f"\nVorzeitig gestoppt ({ergebnis.stopped_reason}), "
                            f"{ergebnis.remaining} Songs offen")
    return zusammenfassung


def _cli(argv=None):
    parser = argparse.ArgumentParser(
        prog="ytmd.headless",
        description="Spotify-Playlist-Export herunterladen – ohne Oberfläche.")
    parser.add_argument("playlist", help="Exportierte Playlist (JSON oder CSV)")
    parser.add_argument("--target", help="Zielordner (Standard: Download-Ordner)")
    parser.add_argument("--format", default="wav", help="wav, flac oder mp3")
    parser.add_argument("--workers", type=int, default=config.DEFAULT_WORKERS,
                        help="gleichzeitige Downloads")
    parser.add_argument("--cookies", help="Browser für Cookies, z. B. chrome")
    parser.add_argument("--no-covers", action="store_true", help="keine Cover einbetten")
    parser.add_argument("--ffmpeg", help="Ordner mit den FFmpeg-Programmen")
    args = parser.parse_args(argv)

    gesamt = {"total": 0}

    def status(index, state, detail=None):
        if state in ("done", "skipped", "failed", "no_space"):
            grund = f"  ({detail[0]})" if isinstance(detail, tuple) else ""
            print(f"[{index + 1}] {state}{grund}", flush=True)

    def progress(done, total):
        gesamt["total"] = total
        print(f"  ... {done}/{total}", flush=True)

    ergebnis = run_playlist(
        args.playlist, target_folder=args.target, audio_format=args.format,
        workers=args.workers, cookies_from_browser=args.cookies,
        save_covers=not args.no_covers, ffmpeg_path=args.ffmpeg,
        on_status=status, on_progress=progress)

    print(f"\nGeladen: {ergebnis.downloaded} | Übersprungen: {ergebnis.skipped} | "
          f"Fehler: {len(ergebnis.failed)} | Cover: {ergebnis.covers}")
    print(f"Ordner: {ergebnis.folder}")
    if ergebnis.stopped_reason:
        print(f"Vorzeitig gestoppt: {ergebnis.stopped_reason} "
              f"({ergebnis.remaining} Songs offen)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
