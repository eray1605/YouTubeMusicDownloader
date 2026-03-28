import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
from yt_dlp import YoutubeDL
from PIL import Image, ImageTk, ImageDraw, ImageFont
import io
import requests
import os
import sys
import threading
import math
import subprocess


def get_download_folder():
    """Get the user's download folder cross-platform."""
    # Try XDG user dirs (Linux with localized folder names)
    try:
        result = subprocess.run(
            ["xdg-user-dir", "DOWNLOAD"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            path = result.stdout.strip()
            if os.path.isdir(path):
                return path
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: common folder names
    home = os.path.expanduser("~")
    for name in ["Downloads", "İndirilenler", "Téléchargements", "Descargas", "Загрузки"]:
        candidate = os.path.join(home, name)
        if os.path.isdir(candidate):
            return candidate

    # Last fallback: home directory
    return home


def get_ffmpeg_path():
    """Get FFmpeg path – bundled in PyInstaller EXE or system PATH."""
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'ffmpeg')
    return None

# --- Einstellungen ---
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

FONT_FAMILY = "Satoshi Medium"
NUM_RESULTS = 10


# --- Animated Theme Toggle ---

class AnimatedThemeToggle(tk.Canvas):
    TRACK_W = 56
    TRACK_H = 28
    KNOB_R = 10
    PAD = 4
    ANIM_MS = 12
    ANIM_STEPS = 14

    # Colors: (dark_track, light_track, knob)
    DARK_TRACK = "#2a2a4a"
    LIGHT_TRACK = "#87CEEB"
    KNOB_COLOR = "#ffffff"

    def __init__(self, parent, command=None, **kwargs):
        super().__init__(parent, width=self.TRACK_W, height=self.TRACK_H,
                         highlightthickness=0, bd=0, cursor="hand2", **kwargs)
        self._command = command
        self._is_light = False
        self._progress = 0.0  # 0=dark, 1=light
        self._animating = False

        # Pre-render icon images
        self._sun_img = self._create_sun_icon()
        self._moon_img = self._create_moon_icon()

        self._draw()
        self.bind("<ButtonPress-1>", self._on_click)

    def _create_moon_icon(self):
        size = 16
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Crescent moon
        draw.ellipse([1, 1, size - 2, size - 2], fill="#FFD700")
        draw.ellipse([4, 0, size + 2, size - 4], fill=(0, 0, 0, 0))
        return ImageTk.PhotoImage(img)

    def _create_sun_icon(self):
        size = 16
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx, cy = size // 2, size // 2
        # Rays
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            x1 = cx + int(5 * math.cos(rad))
            y1 = cy + int(5 * math.sin(rad))
            x2 = cx + int(7 * math.cos(rad))
            y2 = cy + int(7 * math.sin(rad))
            draw.line([(x1, y1), (x2, y2)], fill="#FFA500", width=1)
        # Center circle
        draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill="#FFA500")
        return ImageTk.PhotoImage(img)

    def _ease_in_out(self, t):
        return t * t * (3 - 2 * t)

    def _draw(self):
        self.delete("all")
        w, h = self.TRACK_W, self.TRACK_H
        r = h // 2
        p = self._progress

        # Interpolate track color
        dark_rgb = (42, 42, 74)
        light_rgb = (135, 206, 235)
        tr = int(dark_rgb[0] + (light_rgb[0] - dark_rgb[0]) * p)
        tg = int(dark_rgb[1] + (light_rgb[1] - dark_rgb[1]) * p)
        tb = int(dark_rgb[2] + (light_rgb[2] - dark_rgb[2]) * p)
        track_color = f"#{tr:02x}{tg:02x}{tb:02x}"

        # Track (pill shape)
        self.create_oval(0, 0, h, h, fill=track_color, outline=track_color)
        self.create_oval(w - h, 0, w, h, fill=track_color, outline=track_color)
        self.create_rectangle(r, 0, w - r, h, fill=track_color, outline=track_color)

        # Knob position
        knob_x0 = self.PAD + p * (w - 2 * self.PAD - 2 * self.KNOB_R)
        knob_cx = knob_x0 + self.KNOB_R
        knob_cy = h // 2

        # Knob shadow
        self.create_oval(knob_cx - self.KNOB_R, knob_cy - self.KNOB_R + 1,
                         knob_cx + self.KNOB_R, knob_cy + self.KNOB_R + 1,
                         fill="#888888", outline="")
        # Knob
        self.create_oval(knob_cx - self.KNOB_R, knob_cy - self.KNOB_R,
                         knob_cx + self.KNOB_R, knob_cy + self.KNOB_R,
                         fill=self.KNOB_COLOR, outline="#e0e0e0")

        # Icons (moon on left when dark, sun on right when light)
        icon_offset = 8
        # Moon icon (visible in dark mode, fades out)
        moon_alpha = 1.0 - p
        if moon_alpha > 0.1:
            self.create_image(w - icon_offset - 6, h // 2, image=self._moon_img, anchor="center")
        # Sun icon (visible in light mode, fades in)
        sun_alpha = p
        if sun_alpha > 0.1:
            self.create_image(icon_offset + 6, h // 2, image=self._sun_img, anchor="center")

    def _on_click(self, e):
        if self._animating:
            return
        self._is_light = not self._is_light
        self._animate(0)

    def _animate(self, step):
        if step > self.ANIM_STEPS:
            self._animating = False
            self._progress = 1.0 if self._is_light else 0.0
            self._draw()
            if self._command:
                self._command(self._is_light)
            return

        self._animating = True
        t = step / self.ANIM_STEPS
        eased = self._ease_in_out(t)

        if self._is_light:
            self._progress = eased
        else:
            self._progress = 1.0 - eased

        self._draw()
        self.after(self.ANIM_MS, lambda: self._animate(step + 1))

    def update_bg(self, bg):
        self.configure(bg=bg)


# --- Funktionen ---

def search_youtube(query):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',
        'default_search': f'ytsearch{NUM_RESULTS}',
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


def format_duration(seconds):
    if not seconds:
        return ""
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def round_image_corners(img, radius):
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, img.size[0], img.size[1]], radius=radius, fill=255)
    img = img.convert("RGBA")
    img.putalpha(mask)
    return img


def load_thumbnail(thumbnail_url):
    try:
        response = requests.get(thumbnail_url, timeout=5)
        img = Image.open(io.BytesIO(response.content)).resize((140, 79), Image.LANCZOS)
        img = round_image_corners(img, 8)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(140, 79))
    except Exception:
        return None


# --- App ---

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("YouTube Music Downloader")
        self.geometry("900x720")
        self.minsize(720, 560)

        try:
            self.iconbitmap("C:\\Users\\Eray\\Downloads\\YT-MP3-Downloader-main\\YT-MP3-Downloader-main\\temp_icon.ico")
        except Exception:
            pass

        self._thumbnail_refs = []

        self._build_ui()

    def _build_ui(self):
        # === Header ===
        header = ctk.CTkFrame(self, corner_radius=0)
        header.pack(fill="x")

        ctk.CTkLabel(header, text="YouTube Music Downloader",
                     font=(FONT_FAMILY, 24, "bold"),
                     text_color=("#e94560", "#e94560")).pack(pady=(16, 2))
        ctk.CTkLabel(header, text="Suche nach Songs und lade sie als WAV herunter",
                     font=(FONT_FAMILY, 12),
                     text_color=("gray50", "gray60")).pack(pady=(0, 12))

        # Theme Toggle
        self._header = header
        toggle_container = ctk.CTkFrame(header, fg_color="transparent")
        toggle_container.place(relx=1.0, rely=0.5, anchor="e", x=-16)

        self._toggle = AnimatedThemeToggle(toggle_container,
                                           command=self._on_theme_toggle,
                                           bg=header._apply_appearance_mode(
                                               header.cget("fg_color")))
        self._toggle.pack()

        # === Suchbereich ===
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
                                         fg_color="#e94560", hover_color="#ff6b81",
                                         command=self._start_search)
        self._search_btn.pack(side="left")

        # === Ergebnisbereich (scrollbar) ===
        self._results_frame = ctk.CTkScrollableFrame(self, corner_radius=10,
                                                      label_text="",
                                                      fg_color="transparent")
        self._results_frame.pack(fill="both", expand=True, padx=20, pady=(6, 6))

        # === Trennlinie ===
        ctk.CTkFrame(self, height=2, corner_radius=0,
                     fg_color=("gray80", "gray30")).pack(fill="x", padx=30, pady=(4, 8))

        # === Download Bereich ===
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
                                     fg_color="#2ecc71", hover_color="#27ae60",
                                     command=self._start_download)
        self._dl_btn.pack(side="left")

        # === Statuszeile ===
        self._status = ctk.CTkLabel(self, text="", font=(FONT_FAMILY, 10),
                                    text_color=("gray50", "gray60"),
                                    anchor="w")
        self._status.pack(fill="x", padx=16, pady=(0, 6))

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
        self._status.configure(text="  Suche läuft...", text_color="#e94560")
        self._clear_results()

        ctk.CTkLabel(self._results_frame, text="Suche...",
                     font=(FONT_FAMILY, 14),
                     text_color=("gray50", "gray60")).pack(pady=30)

        threading.Thread(target=self._search_thread, args=(query,), daemon=True).start()

    def _search_thread(self, query):
        results = search_youtube(query)
        self.after(0, lambda: self._show_results(results))

    def _clear_results(self):
        self._thumbnail_refs.clear()
        for w in self._results_frame.winfo_children():
            w.destroy()

    def _show_results(self, results):
        self._clear_results()
        self._search_btn.configure(state="normal")
        self._status.configure(text="")

        if not results:
            ctk.CTkLabel(self._results_frame, text="Keine Ergebnisse gefunden.",
                         font=(FONT_FAMILY, 13),
                         text_color=("gray50", "gray60")).pack(pady=20)
            return

        for i, result in enumerate(results):
            self._create_card(i, result)

    def _create_card(self, index, result):
        title = result.get("title", "Kein Titel")
        url = result.get("webpage_url", "")
        thumbnail_url = result.get("thumbnail")
        if not thumbnail_url:
            thumbs = result.get("thumbnails")
            if thumbs:
                thumbnail_url = thumbs[-1].get("url")
            elif result.get("id"):
                thumbnail_url = f"https://i.ytimg.com/vi/{result['id']}/hqdefault.jpg"
        duration = format_duration(result.get("duration"))
        channel = result.get("channel", result.get("uploader", ""))

        card = ctk.CTkFrame(self._results_frame, corner_radius=12, height=90)
        card.pack(fill="x", padx=4, pady=4)
        card.pack_propagate(False)

        # Thumbnail
        img = load_thumbnail(thumbnail_url)
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
                         text_color=("gray50", "gray60"),
                         anchor="w").pack(anchor="w", pady=(2, 0))

        # Button
        ctk.CTkButton(card, text="Auswählen", width=100, height=32,
                      font=(FONT_FAMILY, 11, "bold"),
                      corner_radius=10,
                      fg_color="#e94560", hover_color="#ff6b81",
                      command=lambda u=url: self._select_url(u)
                      ).pack(side="right", padx=(8, 14), pady=8)

    def _select_url(self, url):
        self._url_entry.configure(state="normal")
        self._url_entry.delete(0, "end")
        self._url_entry.insert(0, url)

    # --- Download ---
    def _start_download(self):
        url = self._url_entry.get().strip()
        if not url:
            messagebox.showwarning("Fehler", "Bitte gib eine YouTube-URL ein.")
            return

        self._dl_btn.configure(state="disabled")
        self._search_btn.configure(state="disabled")
        self.configure(cursor="wait")
        self._status.configure(text="  Download läuft...", text_color="#e94560")

        threading.Thread(target=self._download_thread, args=(url,), daemon=True).start()

    def _download_thread(self, url, output_path=None):
        if output_path is None:
            output_path = get_download_folder()
        try:
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': f'{output_path}/%(title)s.%(ext)s',
                'quiet': True,
                'no_warnings': True,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'wav',
                }],
                'postprocessor_args': {
                    'extractaudio': ['-ar', '44100', '-ac', '2', '-sample_fmt', 's16'],
                },
            }
            ffmpeg_dir = get_ffmpeg_path()
            if ffmpeg_dir:
                ydl_opts['ffmpeg_location'] = ffmpeg_dir
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
                self.after(0, lambda: messagebox.showinfo("Fertig", "Download erfolgreich abgeschlossen!"))
        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda: messagebox.showerror("Fehler", f"Download fehlgeschlagen: {err_msg}"))
        finally:
            self.after(0, lambda: self.configure(cursor=""))
            self.after(0, lambda: self._status.configure(text=""))
            self.after(0, lambda: self._dl_btn.configure(state="normal"))
            self.after(0, lambda: self._search_btn.configure(state="normal"))


if __name__ == "__main__":
    app = App()
    app.mainloop()
