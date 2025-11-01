import tkinter as tk
from gc import enable
from tkinter import messagebox, ttk
from yt_dlp import YoutubeDL
from PIL import Image, ImageTk
import io
import requests
import os
import threading

# --- Konfiguration für ein moderneres Aussehen ---
BG_COLOR = "#2e2e2e" # Dunkler Hintergrund
FG_COLOR = "#ffffff" # Heller Text
ACCENT_COLOR = "#007acc" # Akzentfarbe für Buttons/Links
FONT_FAMILY = "Segoe UI" # Eine moderne Schriftart, die auf Windows/macOS gut aussieht
LARGE_FONT = (FONT_FAMILY, 14)
NORMAL_FONT = (FONT_FAMILY, 12)
BUTTON_FONT = (FONT_FAMILY, 12, 'bold')

# --- Funktionen ---

# Funktion zur YouTube-Suche
def search_youtube(query):
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'default_search': 'ytsearch3',
    }
    with YoutubeDL(ydl_opts) as ydl:
        try:
            results = ydl.extract_info(query, download=False)['entries']
            return results
        except Exception as e:
            print(f"Fehler bei der Suche: {e}")
            return []

# Funktion zum Herunterladen von Audio (mit Thread, Cursor-Änderung und Statusmeldung)
def download_audio(url, output_path=os.path.expanduser("~/Downloads")):
    if not url.strip():
        messagebox.showwarning("Fehler", "Bitte gib eine YouTube-URL ein.")
        return

        # Button deaktivieren
    download_button.config(state=tk.DISABLED)

    # Cursor ändern und Statusmeldung anzeigen
    root.config(cursor="wait") # Mauszeiger zu Warte-Symbol ändern
    status_label.config(text="Download läuft...", fg=ACCENT_COLOR) # Statusmeldung aktualisieren
    root.update_idletasks() # GUI sofort aktualisieren

    def _download():
        try:
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': f'{output_path}/%(title)s.%(ext)s',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'flac',
                }],
            }
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
                root.after(0, lambda: messagebox.showinfo("Download abgeschlossen", "Der Download wurde erfolgreich abgeschlossen!"))
        except Exception as e:
            root.after(0, lambda: messagebox.showerror("Download Fehler", f"Der Download konnte nicht abgeschlossen werden: {e}"))
        finally:
            # Nach Abschluss den Cursor zurücksetzen und Statusmeldung leeren
            root.after(0, lambda: root.config(cursor="")) # Cursor zurücksetzen
            root.after(0, lambda: status_label.config(text="")) # Statusmeldung leeren
            root.after(0, lambda: download_button.config(state=tk.NORMAL))


    # Download in einem separaten Thread starten
    download_thread = threading.Thread(target=_download)
    download_thread.start()


# Funktion zum Setzen der URL in die readonly-Textbox
def set_url(url):
    url_entry.config(state="normal")
    url_entry.delete(0, tk.END)
    url_entry.insert(0, url)
    url_entry.config(state="readonly")

# Hilfsfunktion, um Bilder im Haupt-Thread zu laden
def load_thumbnail(thumbnail_url):
    try:
        response = requests.get(thumbnail_url, timeout=5)
        img_data = response.content
        img = Image.open(io.BytesIO(img_data)).resize((80, 60))
        return ImageTk.PhotoImage(img)
    except Exception as e:
        print(f"Fehler beim Laden des Thumbnails: {e}")
        return None

# Funktion zur Anzeige der Suchergebnisse (wird vom Thread aufgerufen)
def _display_search_results(results):
    for widget in results_frame.winfo_children():
        widget.destroy()

    if not results:
        tk.Label(results_frame, text="Keine Ergebnisse gefunden.", font=NORMAL_FONT, fg=FG_COLOR, bg=BG_COLOR).pack(pady=10)
        return

    for i, result in enumerate(results):
        title = result.get("title", "Kein Titel")
        url = result.get("webpage_url", "")
        thumbnail_url = result.get("thumbnail")

        entry_frame = tk.Frame(results_frame, bg=BG_COLOR, bd=1, relief="solid", padx=5, pady=3)
        entry_frame.pack(fill='x', padx=5, pady=3)

        img = load_thumbnail(thumbnail_url)

        if img:
            thumbnail_label = tk.Label(entry_frame, image=img, bg=BG_COLOR)
            thumbnail_label.image = img
            thumbnail_label.pack(side='left', padx=5)

        text_frame = tk.Frame(entry_frame, bg=BG_COLOR)
        text_frame.pack(side='left', fill='both', expand=True)

        title_label = tk.Label(text_frame, text=title, font=LARGE_FONT, fg=FG_COLOR, bg=BG_COLOR, wraplength=400, anchor='w', justify='left')
        title_label.pack(anchor='w')

        url_label = tk.Label(text_frame, text=url, font=NORMAL_FONT, fg=ACCENT_COLOR, bg=BG_COLOR, cursor="hand2", anchor='w', justify='left')
        url_label.pack(anchor='w')
        url_label.bind("<Button-1>", lambda e, url=url: set_url(url))

# Funktion, die im separaten Thread ausgeführt wird, um die Suche durchzuführen
def _perform_search_and_display():
    query = search_entry.get()
    if not query:
        root.after(0, lambda: messagebox.showwarning("Leeres Feld", "Bitte gib einen Songnamen ein."))
        root.after(0, lambda: [widget.destroy() for widget in results_frame.winfo_children()])
        # Cursor zurücksetzen und Status leeren, wenn nichts eingegeben wurde
        root.after(0, lambda: root.config(cursor=""))
        root.after(0, lambda: status_label.config(text=""))
        root.after(0, lambda: search_button.config(state=tk.NORMAL))
        return

    # Cursor ändern und Statusmeldung anzeigen
    root.after(0, lambda: root.config(cursor="watch")) # "watch" oder "wait"
    root.after(0, lambda: status_label.config(text="Suche läuft...", fg=ACCENT_COLOR))
    root.after(0, lambda: [widget.destroy() for widget in results_frame.winfo_children()]) # Alte Ergebnisse/Status löschen
    root.after(0, root.update_idletasks) # GUI sofort aktualisieren

    results = search_youtube(query) # Blocking operation

    # Ergebnisse im Haupt-Thread anzeigen
    root.after(0, lambda: _display_search_results(results)) # Anzeige der Ergebnisse im Haupt-Thread
    root.after(0, lambda: root.config(cursor="")) # Cursor zurücksetzen
    root.after(0, lambda: status_label.config(text="")) # Statusmeldung leeren
    root.after(0, lambda: search_button.config(state=tk.NORMAL))

# Hauptfunktion für den "Suchen"-Button
def show_search_results_threaded():
    search_button.config(state=tk.DISABLED)
    search_thread = threading.Thread(target=_perform_search_and_display)
    search_thread.start()

# Scrollen mit dem Mausrad aktivieren
def on_mousewheel(event):
    if event.num == 4 or event.delta > 0:
        canvas.yview_scroll(-1, "units")
    elif event.num == 5 or event.delta < 0:
        canvas.yview_scroll(1, "units")

# --- GUI erstellen ---
root = tk.Tk()
root.title("YouTube Music Downloader")
root.geometry("800x650")
root.configure(bg=BG_COLOR)
root.iconbitmap("C:\\Users\\Eray\\Downloads\\YT-MP3-Downloader-main\\YT-MP3-Downloader-main\\temp_icon.ico")
# Oder mit absolutem Pfad, wenn die ICO-Datei woanders liegt:
# root.iconbitmap("C:\\Users\\Eray\\Downloads\\YT-MP3-Downloader-main\\YT-MP3-Downloader-main\\temp_icon.ico")

# Style für ttk-Widgets definieren
style = ttk.Style()
style.theme_use('clam')

style.configure("TLabel", background=BG_COLOR, foreground=FG_COLOR, font=NORMAL_FONT)
style.configure("TButton", background=ACCENT_COLOR, foreground=FG_COLOR, font=BUTTON_FONT, padding=6)
style.map("TButton", background=[('active', ACCENT_COLOR)])

style.configure("TEntry", fieldbackground="#4a4a4a", foreground=FG_COLOR, font=NORMAL_FONT, borderwidth=1, relief="flat")
style.configure("TProgressbar", background=ACCENT_COLOR, troughcolor="#4a4a4a")


# Eingabebereich
input_frame = tk.Frame(root, bg=BG_COLOR, pady=10)
input_frame.pack(pady=10)

tk.Label(input_frame, text="Gib den Songnamen ein:", font=LARGE_FONT, fg=FG_COLOR, bg=BG_COLOR).pack(pady=5)
search_entry = ttk.Entry(input_frame, width=40, font=NORMAL_FONT)
search_entry.pack(pady=5)

search_button = ttk.Button(input_frame, text="Suchen", command=show_search_results_threaded)
search_button.pack(pady=5)

# --- Scrollable Bereich für Suchergebnisse ---
canvas_frame = tk.Frame(root, bg=BG_COLOR)
canvas_frame.pack(fill='both', expand=True, padx=20, pady=10)

canvas = tk.Canvas(canvas_frame, bg=BG_COLOR, highlightthickness=0)
scrollable_frame = tk.Frame(canvas, bg=BG_COLOR)

scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
canvas.create_window((0, 0), window=scrollable_frame, anchor="nw", width=700)

canvas.pack(side="left", fill="both", expand=True)

canvas.bind_all("<MouseWheel>", on_mousewheel)
canvas.bind_all("<Button-4>", on_mousewheel)
canvas.bind_all("<Button-5>", on_mousewheel)

results_frame = scrollable_frame

# --- Download Bereich ---
download_frame = tk.Frame(root, bg=BG_COLOR, pady=10)
download_frame.pack(pady=10)

tk.Label(download_frame, text="YouTube-URL zum Download:", font=LARGE_FONT, fg=FG_COLOR, bg=BG_COLOR).pack(pady=5)
url_entry = ttk.Entry(download_frame, width=50, font=NORMAL_FONT)
url_entry.pack(pady=5)
download_button = ttk.Button(download_frame, text="Download starten", command=lambda: download_audio(url_entry.get()))
download_button.pack(ipady=5)

# --- Globaler Statusbereich am unteren Rand ---
status_label = tk.Label(root, text="", font=NORMAL_FONT, bg=BG_COLOR, fg=FG_COLOR)
status_label.pack(side="bottom", fill="x", pady=5)

root.mainloop()