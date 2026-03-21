# YouTube Music Downloader

A modern desktop application to search and download audio from YouTube videos as high-quality WAV files. Built with Python and CustomTkinter, featuring a sleek dark/light theme UI with animated toggle.

---

## Preview

![App Screenshot](UI.png)

---

## Features

- **Song Search** — Search for any song or video by name, powered by yt-dlp
- **10 Results** — Displays up to 10 search results with thumbnails, titles, channel names and duration
- **One-Click Select** — Click "Auswählen" to pick a result for download
- **High-Quality WAV** — Downloads the best available audio and converts to uncompressed WAV (44.1 kHz, 16-bit, stereo)
- **Dark / Light Theme** — Animated theme toggle with sun/moon icons
- **Standalone EXE** — Runs as a single portable `.exe` on Windows — no Python installation needed

---

## Download

Download the latest release from the [Releases](../../releases) page. Extract the ZIP and run `YT Music Downloader.exe`.

> **Note:** FFmpeg is bundled in the EXE — no extra setup required.

---

## Run from Source

### Prerequisites

- Python 3.10+
- FFmpeg installed and added to your system PATH

### Installation

```bash
pip install customtkinter yt-dlp Pillow requests
```

### Start

```bash
cd YT-MP3-Downloader-main
python main.py
```

---

## How It Works

1. Enter a song name in the search bar and click **Suchen**
2. Browse the results — each card shows a thumbnail, title, channel and duration
3. Click **Auswählen** on the song you want
4. The URL is filled in automatically — click **Download**
5. The WAV file is saved to your `Downloads` folder

---

## Tech Stack

- **CustomTkinter** — Modern themed GUI toolkit
- **yt-dlp** — YouTube search and audio extraction
- **FFmpeg** — Audio conversion to WAV
- **Pillow** — Thumbnail loading and image processing
- **PyInstaller** — Packaging as standalone Windows EXE

---

## Disclaimer

This project is for educational purposes only. Downloading copyrighted material may violate YouTube's terms of service and the laws of your country. Please use this tool responsibly and respect copyright laws.
