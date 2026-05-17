# SPLIT.app — Open-Source Music Stem Separator

A self-hosted, open-source web app that separates any song into **Vocals**, **Bass**, **Melody**, and **Others** stems — and optionally exports a **MIDI** file.

Built with [Demucs](https://github.com/facebookresearch/demucs) (Meta AI) + [librosa](https://librosa.org) + Flask.

---

## Features

- **4-stem separation** — Vocals, Bass, Melody, Others
- **MIDI export** — optional piano-roll MIDI from the melody stem
- **Supports MP3, WAV, MP4, FLAC, OGG, M4A** upload
- **Download as WAV, MP3, or MP4**
- Runs fully **locally** — no cloud, no API keys
- Beautiful dark UI

---

## Quickstart

### 1. Install system dependencies
```bash
# Ubuntu/Debian
sudo apt install ffmpeg python3-pip

# macOS
brew install ffmpeg
```

### 2. Install Python packages
```bash
pip install -r requirements.txt
```

### 3. Run
```bash
python app.py
```

Then open **http://localhost:7860** in your browser.

---

## How it works

1. **Upload** — drop any audio/video file (extracts audio from MP4)
2. **Demucs** runs `htdemucs` (4-stem model) to separate: vocals, bass, drums/melody, others
3. **librosa pyin** performs pitch tracking on the melody stem
4. **pretty_midi** converts pitch contour → `.mid` file
5. **ffmpeg** re-encodes stems to your chosen output format
6. Download each stem individually

---

## Stack

| Layer | Library |
|-------|---------|
| Stem separation | [Demucs (htdemucs)](https://github.com/facebookresearch/demucs) |
| MIDI extraction | [librosa](https://librosa.org) + [pretty_midi](https://github.com/craffel/pretty-midi) |
| Audio conversion | ffmpeg |
| Backend | Flask |
| Frontend | Vanilla HTML/CSS/JS |

---

## License

MIT — free to use, modify, and deploy.
