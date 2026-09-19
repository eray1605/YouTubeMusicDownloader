"""Fenster und Bedienoberfläche."""

import os
import queue
import sys
import threading
from datetime import datetime
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk

from ytmd import config, downloader, tags, youtube
from ytmd.config import FONT_FAMILY
from ytmd.downloader import PlaylistDownloader
from ytmd.images import load_thumbnail
from ytmd.playlist import parse_playlist_file
from ytmd.utils import (format_duration, format_size, free_bytes,
                        get_download_folder, resource_dir)
from ytmd.widgets import AnimatedThemeToggle
from ytmd.youtube import (ERROR_LABELS, TrackError, download_with_retries,
                          error_hint, search_youtube, ytdlp_version)

# Anzeige der Playlist-Zustände aus downloader.py: (Text, Farbe)
STATUS_TEXTS = {
    downloader.SEARCHING: ("Suche...", config.COLOR_WARNING),
    downloader.DOWNLOADING: ("Lädt...", config.COLOR_ACCENT),
    downloader.RETRYING: ("Neuer Versuch", config.COLOR_WARNING),
    downloader.DONE: ("Fertig", config.COLOR_SUCCESS),
    downloader.SKIPPED: ("Schon da", config.COLOR_INFO),
    downloader.FAILED: ("Fehler", config.COLOR_ERROR),
    downloader.CANCELLED: ("Abgebrochen", config.COLOR_WARNING),
    downloader.NO_SPACE: ("Kein Platz", config.COLOR_ERROR),
}

# Kurzzeichen für Konsole und Logdatei
LOG_LABELS = {
    downloader.DONE: "OK      ",
    downloader.SKIPPED: "schon da",
    downloader.FAILED: "FEHLER  ",
    downloader.CANCELLED: "abgebr. ",
    downloader.NO_SPACE: "KEIN PLATZ",
}
LOG_FILENAME = "_download-log.txt"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("YouTube Music Downloader")
        self.geometry("900x720")
        self.minsize(720, 560)

        self._set_app_icon()

        self._thumbnail_refs = []

        # Playlist-Status
        self._playlist_tracks = []
        self._playlist_name = ""
        self._playlist_status_labels = {}
        self._playlist_job = None
        self._log_file = None
        self._last_line = ""

        self._audio_format = config.DEFAULT_FORMAT
        self._target_root = get_download_folder()
        self._workers = config.DEFAULT_WORKERS
        self._cookies_browser = None
        # Worker-Threads melden über diese Queue an den UI-Thread
        self._ui_queue = queue.Queue()

        self._build_ui()

    def _set_app_icon(self):
        """Set app icon (works cross-platform)."""
        try:
            base_path = resource_dir()
            icon_ico = os.path.join(base_path, "temp_icon.ico")
            icon_png = os.path.join(base_path, "icon.png")

            if sys.platform == "win32" and os.path.exists(icon_ico):
                self.iconbitmap(icon_ico)
            elif os.path.exists(icon_png):
                icon_image = ImageTk.PhotoImage(Image.open(icon_png))
                self.iconphoto(True, icon_image)
                self._icon_ref = icon_image  # prevent garbage collection
            elif os.path.exists(icon_ico):
                icon_image = ImageTk.PhotoImage(Image.open(icon_ico))
                self.iconphoto(True, icon_image)
                self._icon_ref = icon_image
        except Exception:
            pass

    # --- Aufbau der Oberfläche ---
    def _build_ui(self):
        self._build_header()
        self._build_search_bar()
        self._build_playlist_bar()

        # === Ergebnisbereich (scrollbar) ===
        self._results_frame = ctk.CTkScrollableFrame(self, corner_radius=10,
                                                      label_text="",
                                                      fg_color="transparent")
        self._results_frame.pack(fill="both", expand=True, padx=20, pady=(6, 6))

        # === Trennlinie ===
        ctk.CTkFrame(self, height=2, corner_radius=0,
                     fg_color=("gray80", "gray30")).pack(fill="x", padx=30, pady=(4, 8))

        self._build_download_bar()

        # === Statuszeile ===
        self._status = ctk.CTkLabel(self, text="", font=(FONT_FAMILY, 10),
                                    text_color=config.COLOR_MUTED,
                                    anchor="w")
        self._status.pack(fill="x", padx=16, pady=(0, 6))

    def _build_header(self):
        header = ctk.CTkFrame(self, corner_radius=0)
        header.pack(fill="x")

        ctk.CTkLabel(header, text="YouTube Music Downloader",
                     font=(FONT_FAMILY, 24, "bold"),
                     text_color=(config.COLOR_ACCENT, config.COLOR_ACCENT)).pack(pady=(16, 2))
        ctk.CTkLabel(header, text="Suche nach Songs und lade sie als WAV herunter",
                     font=(FONT_FAMILY, 12),
                     text_color=config.COLOR_MUTED).pack(pady=(0, 2))

        # Fassung sichtbar machen – vor allem die von yt-dlp: Ist die zu alt für
        # YouTubes aktuellen Player, hagelt es 403er, und im Fertigprogramm
        # steckt sie unveränderlich fest. Ohne Anzeige rät man beim Suchen.
        ytdlp = ytdlp_version() or "unbekannt"
        ctk.CTkLabel(header, text=f"Fassung {config.APP_VERSION}  ·  yt-dlp {ytdlp}",
                     font=(FONT_FAMILY, 10),
                     text_color=config.COLOR_MUTED).pack(pady=(0, 12))

        # Theme Toggle
        self._header = header
        toggle_container = ctk.CTkFrame(header, fg_color="transparent")
        toggle_container.place(relx=1.0, rely=0.5, anchor="e", x=-16)

        self._toggle = AnimatedThemeToggle(toggle_container,
                                           command=self._on_theme_toggle,
                                           bg=header._apply_appearance_mode(
                                               header.cget("fg_color")))
        self._toggle.pack()

    def _build_search_bar(self):
        search_frame = ctk.CTkFrame(self, fg_color="transparent")
        search_frame.pack(fill="x", padx=30, pady=(14, 6))

        search_row = ctk.CTkFrame(search_frame, fg_color="transparent")
        search_row.pack()

        self._search_entry = ctk.CTkEntry(search_row, width=420, height=42,
                                          placeholder_text="Songname eingeben...",
                                          font=(FONT_FAMILY, 14),
                                          corner_radius=12)
        self._search_entry.pack(side="left", padx=(0, 10))
        self._search_entry.bind("<Return>", lambda e: self._start_search())

        self._search_btn = ctk.CTkButton(search_row, text="Suchen", width=120, height=42,
                                         font=(FONT_FAMILY, 13, "bold"),
                                         corner_radius=12,
                                         fg_color=config.COLOR_ACCENT,
                                         hover_color=config.COLOR_ACCENT_HOVER,
                                         command=self._start_search)
        self._search_btn.pack(side="left")

        self._playlist_btn = ctk.CTkButton(search_row, text="Playlist laden", width=130, height=42,
                                           font=(FONT_FAMILY, 13, "bold"),
                                           corner_radius=12,
                                           fg_color=config.COLOR_PLAYLIST,
                                           hover_color=config.COLOR_PLAYLIST_HOVER,
                                           command=self._load_playlist)
        self._playlist_btn.pack(side="left", padx=(10, 0))

    def _build_playlist_bar(self):
        """Leiste mit Playlistname, Buttons und Fortschritt – erst nach dem Laden sichtbar."""
        # Fester Platzhalter, damit die Leiste immer an derselben Stelle auftaucht.
        pl_container = ctk.CTkFrame(self, fg_color="transparent")
        pl_container.pack(fill="x", padx=20)

        self._pl_bar = ctk.CTkFrame(pl_container, corner_radius=12)

        pl_row = ctk.CTkFrame(self._pl_bar, fg_color="transparent")
        pl_row.pack(fill="x", padx=12, pady=(10, 4))

        self._pl_label = ctk.CTkLabel(pl_row, text="", font=(FONT_FAMILY, 13, "bold"),
                                      anchor="w")
        self._pl_label.pack(side="left", fill="x", expand=True)

        self._covers_var = ctk.BooleanVar(value=tags.AVAILABLE)
        self._covers_box = ctk.CTkCheckBox(
            pl_row, text="Cover einbetten", variable=self._covers_var,
            font=(FONT_FAMILY, 12), checkbox_width=18, checkbox_height=18,
            fg_color=config.COLOR_PLAYLIST, hover_color=config.COLOR_PLAYLIST_HOVER)
        self._covers_box.pack(side="right", padx=(12, 0))
        if not tags.AVAILABLE:
            # Ohne mutagen lässt sich nichts einbetten – dann gar nicht erst anbieten.
            self._covers_box.configure(state="disabled", text="Cover (mutagen fehlt)")

        self._pl_folder_btn = ctk.CTkButton(pl_row, text="Ordner...", width=90, height=34,
                                            font=(FONT_FAMILY, 12),
                                            corner_radius=10,
                                            fg_color="transparent", border_width=1,
                                            border_color=("gray70", "gray40"),
                                            text_color=("gray20", "gray80"),
                                            hover_color=("gray85", "gray25"),
                                            command=self._choose_target_folder)
        self._pl_folder_btn.pack(side="right", padx=(8, 0))

        self._pl_cancel_btn = ctk.CTkButton(pl_row, text="Abbrechen", width=100, height=34,
                                            font=(FONT_FAMILY, 12, "bold"),
                                            corner_radius=10, state="disabled",
                                            fg_color="gray40", hover_color="gray30",
                                            command=self._cancel_playlist_download)
        self._pl_cancel_btn.pack(side="right", padx=(8, 0))

        self._pl_dl_btn = ctk.CTkButton(pl_row, text="Alle herunterladen", width=170, height=34,
                                        font=(FONT_FAMILY, 12, "bold"),
                                        corner_radius=10,
                                        fg_color=config.COLOR_PLAYLIST,
                                        hover_color=config.COLOR_PLAYLIST_HOVER,
                                        command=self._start_playlist_download)
        self._pl_dl_btn.pack(side="right")

        # Zielordner und freier Platz
        self._pl_hint = ctk.CTkLabel(self._pl_bar, text="", font=(FONT_FAMILY, 10),
                                     text_color=config.COLOR_MUTED, anchor="w")
        self._pl_hint.pack(fill="x", padx=12, pady=(0, 4))

        self._pl_progress = ctk.CTkProgressBar(self._pl_bar, height=8, corner_radius=4,
                                               progress_color=config.COLOR_PLAYLIST)
        self._pl_progress.set(0)
        self._pl_progress.pack(fill="x", padx=12, pady=(0, 10))

    def _build_download_bar(self):
        dl_frame = ctk.CTkFrame(self, fg_color="transparent")
        dl_frame.pack(fill="x", padx=30, pady=(0, 8))

        dl_row = ctk.CTkFrame(dl_frame, fg_color="transparent")
        dl_row.pack()

        ctk.CTkLabel(dl_row, text="URL:", font=(FONT_FAMILY, 14),
                     text_color=("gray40", "gray60")).pack(side="left", padx=(0, 8))

        self._url_entry = ctk.CTkEntry(dl_row, width=420, height=40,
                                       placeholder_text="Klicke 'Auswählen' bei einem Ergebnis",
                                       font=(FONT_FAMILY, 13),
                                       corner_radius=12)
        self._url_entry.pack(side="left", padx=(0, 10))

        self._dl_btn = ctk.CTkButton(dl_row, text="Download", width=130, height=40,
                                     font=(FONT_FAMILY, 13, "bold"),
                                     corner_radius=12,
                                     fg_color=config.COLOR_DOWNLOAD,
                                     hover_color=config.COLOR_DOWNLOAD_HOVER,
                                     command=self._start_download)
        self._dl_btn.pack(side="left")

        # Format gilt für Einzel- und Playlist-Downloads
        format_row = ctk.CTkFrame(dl_frame, fg_color="transparent")
        format_row.pack(pady=(8, 0))

        ctk.CTkLabel(format_row, text="Format:", font=(FONT_FAMILY, 12),
                     text_color=config.COLOR_MUTED).pack(side="left", padx=(0, 8))

        self._format_menu = ctk.CTkOptionMenu(
            format_row, width=200, height=30, corner_radius=10,
            font=(FONT_FAMILY, 12),
            values=[f.label for f in config.AUDIO_FORMATS],
            fg_color=("gray85", "gray25"), button_color=("gray70", "gray35"),
            button_hover_color=("gray60", "gray45"),
            text_color=("gray10", "gray90"),
            command=self._on_format_change)
        self._format_menu.set(self._audio_format.label)
        self._format_menu.pack(side="left")

        self._format_hint = ctk.CTkLabel(format_row, text="", font=(FONT_FAMILY, 11),
                                         text_color=config.COLOR_MUTED)
        self._format_hint.pack(side="left", padx=(10, 0))
        self._update_format_hint()

        ctk.CTkLabel(format_row, text="Gleichzeitig:", font=(FONT_FAMILY, 12),
                     text_color=config.COLOR_MUTED).pack(side="left", padx=(20, 8))

        self._workers_menu = ctk.CTkOptionMenu(
            format_row, width=70, height=30, corner_radius=10,
            font=(FONT_FAMILY, 12),
            values=[str(n) for n in config.WORKER_CHOICES],
            fg_color=("gray85", "gray25"), button_color=("gray70", "gray35"),
            button_hover_color=("gray60", "gray45"),
            text_color=("gray10", "gray90"),
            command=self._on_workers_change)
        self._workers_menu.set(str(self._workers))
        self._workers_menu.pack(side="left")

        ctk.CTkLabel(format_row, text="Cookies:", font=(FONT_FAMILY, 12),
                     text_color=config.COLOR_MUTED).pack(side="left", padx=(20, 8))

        self._cookies_menu = ctk.CTkOptionMenu(
            format_row, width=110, height=30, corner_radius=10,
            font=(FONT_FAMILY, 12),
            values=list(config.COOKIE_BROWSERS),
            fg_color=("gray85", "gray25"), button_color=("gray70", "gray35"),
            button_hover_color=("gray60", "gray45"),
            text_color=("gray10", "gray90"),
            command=self._on_cookies_change)
        self._cookies_menu.set(config.COOKIE_BROWSERS[0])
        self._cookies_menu.pack(side="left")

    # --- Theme ---
    def _on_theme_toggle(self, is_light):
        mode = "light" if is_light else "dark"
        ctk.set_appearance_mode(mode)
        # Update toggle canvas background after theme change
        self.after(50, self._update_toggle_bg)

    def _update_toggle_bg(self):
        try:
            bg = self._header._apply_appearance_mode(self._header.cget("fg_color"))
            self._toggle.update_bg(bg)
        except Exception:
            pass

    # --- Suche ---
    def _start_search(self):
        query = self._search_entry.get().strip()
        if not query:
            messagebox.showwarning("Leeres Feld", "Bitte gib einen Songnamen ein.")
            return

        self._search_btn.configure(state="disabled")
        self._status.configure(text="  Suche läuft...", text_color=config.COLOR_ACCENT)
        self._clear_results()
        self._pl_bar.pack_forget()

        ctk.CTkLabel(self._results_frame, text="Suche...",
                     font=(FONT_FAMILY, 14),
                     text_color=config.COLOR_MUTED).pack(pady=30)

        threading.Thread(target=self._search_thread, args=(query,), daemon=True).start()

    def _search_thread(self, query):
        results = search_youtube(query)
        self.after(0, lambda: self._show_results(results))

    def _clear_results(self):
        self._thumbnail_refs.clear()
        self._playlist_status_labels.clear()
        for w in self._results_frame.winfo_children():
            w.destroy()

    def _show_results(self, results):
        self._clear_results()
        self._search_btn.configure(state="normal")
        self._status.configure(text="")

        if not results:
            ctk.CTkLabel(self._results_frame, text="Keine Ergebnisse gefunden.",
                         font=(FONT_FAMILY, 13),
                         text_color=config.COLOR_MUTED).pack(pady=20)
            return

        for i, result in enumerate(results):
            self._create_card(i, result)

    def _create_card(self, index, result):
        title = result.get("title", "Kein Titel")
        url = result.get("webpage_url", "")
        duration = format_duration(result.get("duration"))
        channel = result.get("channel", result.get("uploader", ""))

        card = ctk.CTkFrame(self._results_frame, corner_radius=12, height=90)
        card.pack(fill="x", padx=4, pady=4)
        card.pack_propagate(False)

        # Thumbnail
        img = load_thumbnail(youtube.thumbnail_url(result))
        if img:
            self._thumbnail_refs.append(img)
            thumb = ctk.CTkLabel(card, image=img, text="")
            thumb.pack(side="left", padx=(12, 10), pady=8)

            if duration:
                dur_label = ctk.CTkLabel(card, text=f" {duration} ",
                                         font=(FONT_FAMILY, 9, "bold"),
                                         fg_color="black", text_color="white",
                                         corner_radius=4)
                dur_label.place(x=12 + 140 - 6, y=8 + 79 - 6, anchor="se")

        # Text
        text_frame = ctk.CTkFrame(card, fg_color="transparent")
        text_frame.pack(side="left", fill="both", expand=True, pady=10)

        ctk.CTkLabel(text_frame, text=f"{index + 1}.  {title}",
                     font=(FONT_FAMILY, 13, "bold"),
                     wraplength=400, anchor="w", justify="left").pack(anchor="w")

        if channel:
            ctk.CTkLabel(text_frame, text=channel,
                         font=(FONT_FAMILY, 10),
                         text_color=config.COLOR_MUTED,
                         anchor="w").pack(anchor="w", pady=(2, 0))

        # Button
        ctk.CTkButton(card, text="Auswählen", width=100, height=32,
                      font=(FONT_FAMILY, 11, "bold"),
                      corner_radius=10,
                      fg_color=config.COLOR_ACCENT,
                      hover_color=config.COLOR_ACCENT_HOVER,
                      command=lambda u=url: self._select_url(u)
                      ).pack(side="right", padx=(8, 14), pady=8)

    def _select_url(self, url):
        self._url_entry.configure(state="normal")
        self._url_entry.delete(0, "end")
        self._url_entry.insert(0, url)

    # --- Playlist laden und anzeigen ---
    def _load_playlist(self):
        path = filedialog.askopenfilename(
            title="Spotify-Playlist auswählen",
            filetypes=[("Playlist-Export", "*.json *.csv"),
                       ("JSON", "*.json"), ("CSV", "*.csv"), ("Alle Dateien", "*.*")])
        if not path:
            return

        try:
            name, tracks = parse_playlist_file(path)
        except Exception as e:
            messagebox.showerror("Fehler", f"Datei konnte nicht gelesen werden:\n{e}")
            return

        if not tracks:
            messagebox.showwarning(
                "Keine Songs gefunden",
                "In der Datei wurden keine Songs erkannt.\n\n"
                "Erwartet wird ein Export mit Titel- und Interpreten-Feldern "
                "(z. B. von Exportify oder der Spotify-API).")
            return

        self._playlist_tracks = tracks
        self._playlist_name = name
        self._show_playlist()

    def _show_playlist(self):
        self._clear_results()
        tracks = self._playlist_tracks

        self._update_playlist_summary()
        self._pl_progress.set(0)
        self._pl_bar.pack(fill="x", pady=(4, 0))

        # Nur die ersten Zeilen zeichnen – bei mehreren tausend Songs würde der
        # Aufbau sonst minutenlang blockieren. Geladen wird trotzdem alles.
        width = max(2, len(str(len(tracks))))
        for i, track in enumerate(tracks[:config.MAX_VISIBLE_ROWS]):
            self._create_playlist_row(i, track, width)

        hidden = len(tracks) - config.MAX_VISIBLE_ROWS
        if hidden > 0:
            ctk.CTkLabel(self._results_frame,
                         text=f"... und {hidden} weitere Songs "
                              f"(werden ebenfalls geladen, siehe Fortschritt unten)",
                         font=(FONT_FAMILY, 12),
                         text_color=config.COLOR_MUTED).pack(pady=14)

    def _create_playlist_row(self, index, track, number_width):
        row = ctk.CTkFrame(self._results_frame, corner_radius=10, height=52)
        row.pack(fill="x", padx=4, pady=3)
        row.pack_propagate(False)

        ctk.CTkLabel(row, text=f"{index + 1:0{number_width}d}", width=36,
                     font=(FONT_FAMILY, 12, "bold"),
                     text_color=config.COLOR_MUTED).pack(side="left", padx=(12, 4))

        text_frame = ctk.CTkFrame(row, fg_color="transparent")
        text_frame.pack(side="left", fill="both", expand=True, pady=6)

        ctk.CTkLabel(text_frame, text=track.title, font=(FONT_FAMILY, 12, "bold"),
                     anchor="w").pack(anchor="w")

        subtitle = track.artist or track.album
        if track.duration:
            length = format_duration(track.duration)
            subtitle = f"{subtitle}  ·  {length}" if subtitle else length
        if subtitle:
            ctk.CTkLabel(text_frame, text=subtitle, font=(FONT_FAMILY, 10),
                         text_color=config.COLOR_MUTED,
                         anchor="w").pack(anchor="w")

        status = ctk.CTkLabel(row, text="Wartet", width=110,
                              font=(FONT_FAMILY, 11),
                              text_color=config.COLOR_MUTED, anchor="e")
        status.pack(side="right", padx=(8, 14))
        self._playlist_status_labels[index] = status

    # --- Format, Zielordner und Platzbedarf ---
    def _on_format_change(self, label):
        for fmt in config.AUDIO_FORMATS:
            if fmt.label == label:
                self._audio_format = fmt
                break
        self._update_format_hint()
        if self._playlist_tracks:
            self._update_playlist_summary()

    def _on_workers_change(self, value):
        self._workers = int(value)

    def _on_cookies_change(self, value):
        """Cookies aus dem Browser helfen bei altersbeschränkten Videos.
        yt-dlp liest sie lokal aus; die App selbst sieht die Werte nie."""
        self._cookies_browser = None if value == config.COOKIE_BROWSERS[0] else value

    def _update_format_hint(self):
        per_minute = format_size(self._audio_format.per_minute)
        self._format_hint.configure(text=f"≈ {per_minute} pro Minute")

    def _choose_target_folder(self):
        folder = filedialog.askdirectory(title="Zielordner wählen",
                                         initialdir=self._target_root)
        if folder:
            self._target_root = folder
            self._update_playlist_summary()

    def _estimated_bytes(self):
        return downloader.estimate_bytes(self._playlist_tracks, self._audio_format)

    def _update_playlist_summary(self):
        """Kopfzeile und Hinweiszeile der Playlist-Leiste neu beschriften."""
        tracks = self._playlist_tracks
        needed = self._estimated_bytes()
        self._pl_label.configure(
            text=f"{self._playlist_name}  ·  {len(tracks)} Songs  ·  ca. {format_size(needed)}")

        free = free_bytes(self._target_root)
        target = os.path.join(self._target_root, self._playlist_name or "Playlist")
        hint = f"Ziel: {target}  ·  {format_size(free)} frei"
        fits = free >= needed + config.DISK_RESERVE_BYTES
        self._pl_hint.configure(
            text=hint if fits else hint + "  ·  reicht nicht – anderes Format oder Laufwerk wählen",
            text_color=config.COLOR_MUTED if fits else config.COLOR_WARNING)

    def _set_track_status(self, index, state):
        label = self._playlist_status_labels.get(index)
        if label is not None and label.winfo_exists():
            text, color = STATUS_TEXTS[state]
            label.configure(text=text, text_color=color)

    # --- Playlist herunterladen ---
    def _start_playlist_download(self):
        if not self._playlist_tracks or not self._confirm_disk_space():
            return

        self._ui_queue = queue.Queue()
        self._playlist_job = PlaylistDownloader(
            self._playlist_tracks, self._playlist_name,
            audio_format=self._audio_format,
            target_root=self._target_root,
            workers=self._workers,
            cookies_from_browser=self._cookies_browser,
            save_covers=self._covers_var.get(),
            # Aus Worker-Threads darf Tkinter nicht direkt angefasst werden.
            on_status=lambda i, state, detail=None: self._ui_queue.put(
                ("status", i, state, detail)),
            on_progress=lambda done, total: self._ui_queue.put(("progress", done, total)),
        )

        self._pl_dl_btn.configure(state="disabled")
        self._pl_cancel_btn.configure(state="normal")
        self._playlist_btn.configure(state="disabled")
        self._dl_btn.configure(state="disabled")
        self._search_btn.configure(state="disabled")
        self._format_menu.configure(state="disabled")
        self._workers_menu.configure(state="disabled")
        self._pl_folder_btn.configure(state="disabled")
        self._pl_progress.set(0)
        self._last_line = ""
        self._log_file = None
        self._show_progress(0, len(self._playlist_tracks))

        threading.Thread(target=self._playlist_thread, daemon=True).start()
        self._poll_ui_queue()

    def _confirm_disk_space(self):
        """Vor dem Start warnen, wenn der Platz absehbar nicht reicht."""
        needed = self._estimated_bytes()
        free = free_bytes(self._target_root)
        if free >= needed + config.DISK_RESERVE_BYTES:
            return True

        possible = max(0, (free - config.DISK_RESERVE_BYTES)) // max(
            1, needed // max(1, len(self._playlist_tracks)))
        return messagebox.askyesno(
            "Speicherplatz reicht nicht",
            f"{len(self._playlist_tracks)} Songs als {self._audio_format.label}\n"
            f"benötigen ca. {format_size(needed)}.\n\n"
            f"Auf {self._target_root} sind nur {format_size(free)} frei –\n"
            f"das reicht für etwa {possible} Songs.\n\n"
            "Tipp: ein kleineres Format wählen (MP3 320 kbps klingt sehr gut und "
            "braucht nur ein Zehntel von WAV) oder über \"Ordner...\" eine externe "
            "Festplatte als Ziel setzen.\n\n"
            "Trotzdem starten? Der Download stoppt automatisch, bevor das "
            "Laufwerk volläuft – bereits geladene Songs bleiben erhalten.")

    def _cancel_playlist_download(self):
        if self._playlist_job:
            self._playlist_job.cancel()
        self._pl_cancel_btn.configure(state="disabled")
        self._status.configure(text="  Abbruch nach dem aktuellen Song...",
                               text_color=config.COLOR_WARNING)

    def _playlist_thread(self):
        """Worker-Thread: schickt nur Nachrichten, fasst kein Widget an."""
        try:
            result = self._playlist_job.run()
        except OSError as e:
            self._ui_queue.put(("error", str(e)))
            return
        self._ui_queue.put(("finished", result))

    def _poll_ui_queue(self):
        """Sammelt im UI-Thread ein, was die Worker gemeldet haben."""
        finished = None
        error = None
        try:
            while True:
                message = self._ui_queue.get_nowait()
                kind = message[0]
                if kind == "status":
                    self._set_track_status(message[1], message[2])
                    self._log_track(message[1], message[2], message[3])
                elif kind == "progress":
                    self._show_progress(message[1], message[2])
                elif kind == "finished":
                    finished = message[1]
                elif kind == "error":
                    error = message[1]
        except queue.Empty:
            pass

        if error is not None:
            self._close_log()
            messagebox.showerror("Fehler", f"Ordner konnte nicht erstellt werden:\n{error}")
            self._finish_playlist_download()
            return
        if finished is not None:
            self._close_log(finished)
            self._show_summary(finished)
            self._finish_playlist_download()
            return

        self.after(120, self._poll_ui_queue)

    # --- Mitschrift des Laufs ---
    def _log_track(self, index, state, detail=None):
        """Fertige Songs in Konsole, Statuszeile und Logdatei festhalten."""
        if state not in LOG_LABELS:
            return  # laufende Zustände (Suche, lädt, neuer Versuch) nicht mitschreiben

        track = self._playlist_tracks[index]
        total = len(self._playlist_tracks)
        reason = ""
        if detail:
            # Bei Fehlern: Ursache UND die Originalmeldung von yt-dlp – ohne die
            # lässt sich "Sonstiger Fehler" nicht auseinanderklamüsern.
            category, message = detail
            reason = f"  ({ERROR_LABELS.get(category, category)}"
            if message:
                text = " ".join(str(message).split())
                if len(text) > 130:
                    text = text[:130] + "..."
                reason += f": {text}"
            reason += ")"
        line = (f"[{index + 1:>{len(str(total))}}/{total}] "
                f"{LOG_LABELS[state]}  {track.label}{reason}")

        self._last_line = line
        try:
            # Ohne Konsole (--noconsole-Build) ist stdout None und print wirkungslos.
            # Abgesichert, weil ein Fehler hier die UI-Schleife anhalten würde.
            print(line, flush=True)
        except (OSError, ValueError, AttributeError):
            pass
        self._write_log(line)

    def _write_log(self, line):
        """Anhängen an _download-log.txt im Playlist-Ordner (best effort)."""
        if self._log_file is None:
            if not self._playlist_job:
                return
            path = os.path.join(self._playlist_job.target_folder(), LOG_FILENAME)
            try:
                self._log_file = open(path, "a", encoding="utf-8")
                self._log_file.write(
                    f"\n=== {datetime.now():%d.%m.%Y %H:%M} · {self._playlist_name} · "
                    f"{len(self._playlist_tracks)} Songs · {self._audio_format.label} · "
                    f"{self._workers} gleichzeitig ===\n")
            except OSError:
                self._log_file = False  # nicht erneut versuchen
        if self._log_file:
            try:
                self._log_file.write(f"{datetime.now():%H:%M:%S}  {line}\n")
                self._log_file.flush()
            except OSError:
                pass

    def _close_log(self, result=None):
        if self._log_file:
            try:
                if result:
                    self._log_file.write(
                        f"--- geladen: {result.downloaded} · übersprungen: "
                        f"{result.skipped} · fehlgeschlagen: {len(result.failed)}\n")
                self._log_file.close()
            except OSError:
                pass
        self._log_file = None

    def _show_progress(self, done, total):
        self._pl_progress.set(done / total if total else 0)
        parallel = f"  ·  {self._workers} gleichzeitig" if self._workers > 1 else ""
        # Zuletzt fertiger Song, damit sichtbar ist, dass es vorangeht
        last = f"  ·  {self._last_line.split('] ', 1)[-1]}" if self._last_line else ""
        self._status.configure(text=f"  {done}/{total} Songs{parallel}{last}",
                               text_color=config.COLOR_ACCENT)

    def _show_summary(self, result):
        if result.stopped_reason == downloader.DISK_FULL:
            title = "Speicherplatz voll"
        elif result.stopped_reason:
            title = "Vorzeitig gestoppt"
        elif result.cancelled:
            title = "Abgebrochen"
        else:
            title = "Playlist fertig"
        messagebox.showinfo(title, self._summary_text(result))

    @staticmethod
    def _summary_text(result):
        text = ""
        if result.stopped_reason:
            text += App._stop_text(result) + (
                "\n\nAlles bereits Geladene bleibt erhalten – nach dem Beheben einfach "
                "erneut starten, fertige Songs werden übersprungen und es geht an "
                "derselben Stelle weiter.\n\n")

        text += (f"Heruntergeladen: {result.downloaded}\n"
                 f"Übersprungen (schon vorhanden): {result.skipped}\n"
                 f"Fehlgeschlagen: {len(result.failed)}\n")
        if result.covers or result.covers_failed:
            text += f"Cover eingebettet: {result.covers}"
            if result.covers_failed:
                text += f"  (ohne Cover: {result.covers_failed})"
            text += "\n"

        # Nach Ursache gruppieren – das sagt mehr als 40 gleiche Fehlerzeilen
        for category, count in sorted(result.failed_categories.items(),
                                      key=lambda item: -item[1]):
            text += f"   · {count}× {ERROR_LABELS.get(category, category)}\n"

        text += f"\nOrdner: {result.folder}"

        tips = App._failure_tips(result.failed_categories)
        if tips:
            text += "\n\n" + "\n".join(tips)
        if result.failed:
            text += "\n\nNicht geladen:\n" + "\n".join(result.failed[:10])
            if len(result.failed) > 10:
                text += f"\n... und {len(result.failed) - 10} weitere"
        return text

    @staticmethod
    def _stop_text(result):
        """Erklärt, warum der Lauf vorzeitig beendet wurde."""
        fehlen = f"Es fehlen noch {result.remaining} Songs."
        reason = result.stopped_reason

        if reason == downloader.DISK_FULL:
            return f"Der Platz auf dem Ziellaufwerk ist aufgebraucht. {fehlen}"
        if reason == youtube.BOT_CHECK:
            return ("YouTube stuft die Zugriffe als automatisiert ein und verlangt eine "
                    f"Anmeldung (\"not a bot\"). {fehlen}\n\n"
                    "Das trifft jeden weiteren Song, deshalb wurde gestoppt.\n"
                    "-> 1-2 Stunden warten, \"Gleichzeitig\" auf 1 stellen und bei "
                    "\"Cookies\" den Browser wählen, in dem du bei YouTube angemeldet bist.")
        if reason == youtube.FFMPEG_MISSING:
            return ("FFmpeg wurde nicht gefunden – ohne das lässt sich nicht in WAV, "
                    f"FLAC oder MP3 umwandeln. {fehlen}\n\n"
                    "-> Entweder FFmpeg installieren (und die App danach neu starten, "
                    "damit sie den Pfad sieht),\n"
                    "-> oder als Format \"Original\" wählen: Dann wird die Tonspur so "
                    "gespeichert, wie YouTube sie liefert – ohne Umwandlung.")
        if reason == downloader.MASS_FAILURE:
            return (f"{config.MAX_CONSECUTIVE_FAILURES} Songs sind hintereinander "
                    f"fehlgeschlagen – da stimmt etwas Grundsätzliches nicht. {fehlen}\n\n"
                    "-> Internetverbindung prüfen; wenn die steht, blockt YouTube "
                    "vermutlich: Pause machen und \"Gleichzeitig\" auf 1 stellen.")
        return f"Vorzeitig gestoppt ({reason}). {fehlen}"

    @staticmethod
    def _failure_tips(categories):
        """Konkrete Hinweise passend zu den aufgetretenen Fehlern."""
        tips = []
        if categories.get(youtube.BLOCKED) or categories.get(youtube.RATE_LIMITED):
            tips.append(
                "Viele Blockaden (403/429) bedeuten meist, dass YouTube wegen zu "
                "vieler gleichzeitiger Zugriffe ausbremst.\n"
                "-> \"Gleichzeitig\" auf 1 oder 2 stellen und erneut starten. "
                "Fertige Songs werden übersprungen, es werden nur die fehlenden geholt.")
        if categories.get(youtube.AGE_RESTRICTED):
            tips.append(
                "Altersbeschränkte Videos brauchen einen angemeldeten Zugang.\n"
                "-> Bei \"Cookies\" den Browser wählen, in dem du bei YouTube "
                "angemeldet bist, und erneut starten.")
        if categories.get(youtube.COOKIE_ERROR):
            tips.append(
                "Die Cookies konnten nicht gelesen werden.\n"
                "-> Den gewählten Browser komplett schließen und erneut starten.")
        if categories.get(youtube.NO_FORMAT):
            tips.append(
                "\"Keine Tonspur geliefert\" heißt fast immer, dass YouTube die Zugriffe "
                "gerade blockt – nicht, dass die Videos kaputt sind.\n"
                "-> Wie bei der Bot-Prüfung: Pause machen, \"Gleichzeitig\" auf 1 und "
                "Cookies setzen.")
        if categories.get(youtube.NETWORK):
            tips.append("Es gab Verbindungsabbrüche – Internetverbindung prüfen.")
        if categories.get(youtube.OTHER):
            tips.append(
                "Bei \"Sonstiger Fehler\" steht die Originalmeldung von yt-dlp in der "
                f"Datei {LOG_FILENAME} im Playlist-Ordner.")
        return tips

    def _finish_playlist_download(self):
        self._playlist_job = None
        self._status.configure(text="")
        self._pl_dl_btn.configure(state="normal")
        self._pl_cancel_btn.configure(state="disabled")
        self._playlist_btn.configure(state="normal")
        self._dl_btn.configure(state="normal")
        self._search_btn.configure(state="normal")
        self._format_menu.configure(state="normal")
        self._workers_menu.configure(state="normal")
        self._pl_folder_btn.configure(state="normal")

    # --- Einzel-Download ---
    def _start_download(self):
        url = self._url_entry.get().strip()
        if not url:
            messagebox.showwarning("Fehler", "Bitte gib eine YouTube-URL ein.")
            return

        self._dl_btn.configure(state="disabled")
        self._search_btn.configure(state="disabled")
        self.configure(cursor="watch" if sys.platform != "win32" else "wait")
        self._status.configure(text="  Download läuft...", text_color=config.COLOR_ACCENT)

        threading.Thread(target=self._download_thread, args=(url,), daemon=True).start()

    def _show_retry(self, attempt, category):
        """Zwischenstand, solange ein einzelner Song erneut versucht wird."""
        text = (f"  {ERROR_LABELS.get(category, category)} – "
                f"neuer Versuch ({attempt}/{len(config.RETRY_DELAYS)})...")
        self.after(0, lambda: self._status.configure(text=text,
                                                     text_color=config.COLOR_WARNING))

    @staticmethod
    def _error_text(exc):
        """Verständliche Meldung statt der rohen yt-dlp-Zeile.

        Die Originalmeldung bleibt am Ende stehen – ohne sie lässt sich
        "Sonstiger Fehler" nicht auseinanderklamüsern.
        """
        category = exc.category if isinstance(exc, TrackError) else youtube.classify_error(exc)
        text = ERROR_LABELS.get(category, "Fehler")
        rat = error_hint(category)
        if rat:
            text += f"\n\n{rat}"
        original = " ".join(str(exc).split())
        if original:
            if len(original) > 200:
                original = original[:200] + "..."
            text += f"\n\nMeldung von yt-dlp:\n{original}"
        return text

    def _download_thread(self, url, output_path=None):
        if output_path is None:
            output_path = get_download_folder()
        try:
            # Dieselbe Wiederholung wie beim Playlist-Download: Ein 403 ist oft
            # nur eine kurze Drosselung und klappt nach einer Pause doch.
            download_with_retries(url, output_path,
                                  audio_format=self._audio_format,
                                  on_retry=self._show_retry)
            self.after(0, lambda: messagebox.showinfo("Fertig", "Download erfolgreich abgeschlossen!"))
        except Exception as e:
            text = self._error_text(e)
            self.after(0, lambda: messagebox.showerror("Download fehlgeschlagen", text))
        finally:
            self.after(0, lambda: self.configure(cursor=""))
            self.after(0, lambda: self._status.configure(text=""))
            self.after(0, lambda: self._dl_btn.configure(state="normal"))
            self.after(0, lambda: self._search_btn.configure(state="normal"))
