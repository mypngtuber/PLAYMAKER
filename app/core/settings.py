"""Non-secret application preferences."""
import json
from pathlib import Path

SETTINGS_PATH = Path.home() / 'AppData' / 'Local' / 'AIShortsMakerPro' / 'settings.json' if __import__('sys').platform == 'win32' else Path.home() / '.config' / 'AIShortsMakerPro' / 'settings.json'
DEFAULTS = {'analysis_model': 'gemini-2.5-flash', 'metadata_model': 'gemini-2.5-flash', 'ffmpeg': 'ffmpeg', 'ffprobe': 'ffprobe', 'projects_dir': str(Path.home() / 'Videos' / 'AIShortsMakerPro'), 'recent': []}

def load():
    try:
        return {**DEFAULTS, **json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))}
    except (FileNotFoundError, ValueError):
        return DEFAULTS.copy()

def save(settings):
    allowed = set(DEFAULTS)
    safe = {k: v for k, v in settings.items() if k in allowed}
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(safe, indent=2), encoding='utf-8')
