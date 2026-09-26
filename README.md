# AI Shorts Maker Pro

A native, Windows-first PySide6 desktop app for turning a local long-form video into reviewed, portrait YouTube Shorts. No web UI or local web server is used. The original video is never sent to Gemini; only the provided SRT and/or audio is analyzed.

## Setup (Windows, Python 3.12 x64)

1. Install [FFmpeg](https://ffmpeg.org/download.html) (including `ffprobe`) and [VLC media player](https://www.videolan.org/vlc/) 64-bit. Add FFmpeg to PATH or set executable paths in Settings.
2. In a terminal in this directory: `py -3.12 -m venv .venv`, `.venv\Scripts\activate`, `pip install -r requirements.txt`, `python main.py`.
3. Enter separate Gemini analysis and metadata API keys in Settings. Keys and YouTube OAuth tokens are stored only in Windows Credential Manager via `keyring`.
4. Create a project with a local video and at least an SRT or an audio file. Analyze; select candidates; render; review the video and metadata; explicitly confirm upload.
5. For YouTube, enable YouTube Data API v3 in Google Cloud, create an OAuth **Desktop** client and choose its downloaded JSON on the Publishing screen. Authorize in the browser; when redirected to localhost (which has no listener), copy the full address-bar URL and paste it into the desktop dialog. No server is started. Test-mode OAuth apps must add the Google account as a test user.

Projects are saved by default under `Videos/AIShortsMakerPro/<video name>/` with `project.db`, `project.json`, `analysis.json`, and separate `shorts/`, `captions/`, `metadata/`, `thumbnails/`, `previews/`, and `logs/` folders. Reopen a project folder to continue. Interrupted job records reset to pending on reopen. Finished renders and manually edited metadata are retained.

## Windows packaging

Run on a Windows machine with Python 3.12 and dependencies installed: `pyinstaller --noconfirm --windowed --name "AI Shorts Maker Pro" main.py`. Keep FFmpeg and VLC installed (or distribute them in compliance with their licenses); they are external executables/libraries, not bundled by this command. The EXE is produced in `dist/`.

## Tests

`python -m pytest -q tests` (unit tests do not call Gemini or YouTube). FFmpeg, Gemini API keys, YouTube OAuth consent and 64-bit VLC must be tested manually for a full real-world end-to-end run.

## Current scope

Implemented: SRT/audio Gemini analysis with structured validation and hash cache, deterministic candidate ranking/deduplication, editable clip timing, local FFmpeg center/manual crop and subtitle burn-in, four audio presets, thumbnail frame extraction, metadata generation and manual editing, embedded VLC playback when installed, OAuth via manually pasted redirect URL, resumable YouTube upload and scheduling, per-project SQLite state and background GUI operations. No artificial results or simulated uploads.

Not implemented yet: smart/face/speaker reframing, silence reduction, AI caption timing for audio-only sources, subtitle style designer, thumbnail editor, cancellable FFmpeg operations, batch across multiple projects and folder watcher. **Audio-only projects render without burned captions** until an SRT is supplied. A full Windows EXE and authenticated Gemini/YouTube integration have not been verified in this Linux development environment.
