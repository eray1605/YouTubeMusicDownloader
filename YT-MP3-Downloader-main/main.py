import customtkinter as ctk
from tkinter import messagebox
from yt_dlp import YoutubeDL
from PIL import Image, ImageTk, ImageDraw
import io
import requests
import os
import threading

# --- Einstellungen ---
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

FONT_FAMILY = "Segoe UI Variable"
NUM_RESULTS = 10


# --- Funktionen ---

def search_youtube(query):
    ydl_opts = {
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'default_search': f'ytsearch{NUM_RESULTS}',
        'cookiesfrombrowser': ('firefox',),
    }
    with YoutubeDL(ydl_opts) as ydl:
        try:
            results = ydl.extract_info(query, download=False)['entries']
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
        ctk.CTkLabel(header, text="Suche nach Songs und lade sie als FLAC herunter",
                     font=(FONT_FAMILY, 12),
                     text_color=("gray50", "gray60")).pack(pady=(0, 12))

        # Theme Toggle
        self._theme_switch = ctk.CTkSwitch(header, text="Light Mode",
                                           font=(FONT_FAMILY, 11),
                                           command=self._toggle_theme,
                                           onvalue="light", offvalue="dark")
        self._theme_switch.place(relx=1.0, rely=0.5, anchor="e", x=-20)

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
    def _toggle_theme(self):
        mode = self._theme_switch.get()
        ctk.set_appearance_mode(mode)
        self._theme_switch.configure(text="Dark Mode" if mode == "light" else "Light Mode")

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

    def _download_thread(self, url, output_path=os.path.expanduser("~/Downloads")):
        try:
            ydl_opts = {
                'format': 'bestaudio*',
                'outtmpl': f'{output_path}/%(title)s.%(ext)s',
                'quiet': True,
                'no_warnings': True,
                'cookiesfrombrowser': ('firefox',),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'flac',
                }],
            }
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
