"""Safe FFmpeg argument construction, validation and rendering."""
import json
import subprocess
from pathlib import Path
from app.core.timecode import parse_time
from app.video.captions import load_srt, shift_srt


def probe(source, ffprobe='ffprobe'):
    if not Path(source).is_file():
        raise FileNotFoundError(source)
    proc = subprocess.run([ffprobe, '-v', 'error', '-show_format', '-show_streams',
                           '-of', 'json', str(source)], capture_output=True, text=True, check=True)
    data = json.loads(proc.stdout)
    if not any(stream['codec_type'] == 'video' for stream in data.get('streams', [])):
        raise ValueError('Source has no video stream')
    return data


def duration_of(data):
    return float(data['format']['duration'])


def crop_filter(mode='center', offset=0):
    # Scale to fill, then crop to a portrait frame; manual offset is normalized 0..1.
    if mode not in ('center', 'manual'):
        raise ValueError('Only center and manual crop are implemented')
    if not 0 <= offset <= 1:
        raise ValueError('Crop offset must be between 0 and 1')
    x = '(iw-1080)/2' if mode == 'center' else f'(iw-1080)*{offset:.4f}'
    return f'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920:{x}:0,setsar=1'


def escape_filter_path(path):
    # FFmpeg filtergraph escaping, including Windows drive colons and quote characters.
    value = str(Path(path).resolve()).replace('\\', '/')
    return value.replace(':', '\\:').replace("'", "\\'").replace('[', '\\[').replace(']', '\\]').replace(',', '\\,')


def render_command(source, destination, start, end, captions=None, ffmpeg='ffmpeg',
                   crop_mode='center', crop_offset=.5, audio_preset='balanced'):
    if start < 0 or end <= start:
        raise ValueError('Invalid clip timestamps')
    video = crop_filter(crop_mode, crop_offset)
    if captions:
        video += f",subtitles='{escape_filter_path(captions)}':force_style='FontSize=22,Outline=2,Alignment=2,MarginV=140'"
    audio = {'original': 'anull', 'balanced': 'loudnorm=I=-16:TP=-1.5:LRA=11',
             'loud': 'loudnorm=I=-14:TP=-1.5:LRA=9',
             'voice': 'highpass=f=80,lowpass=f=10000,loudnorm=I=-16:TP=-1.5:LRA=11'}[audio_preset]
    return [ffmpeg, '-y', '-ss', str(start), '-i', str(source), '-t', str(end-start),
            '-vf', video, '-af', audio, '-c:v', 'libx264', '-preset', 'medium',
            '-crf', '20', '-pix_fmt', 'yuv420p', '-r', '30', '-c:a', 'aac',
            '-b:a', '192k', '-movflags', '+faststart', str(destination)]


def render(store, candidate, settings, progress=None):
    project = store.project()
    source = project['source']
    info = probe(source, settings['ffprobe'])
    start, end = parse_time(candidate.start), parse_time(candidate.end)
    if end > duration_of(info) + .25:
        raise ValueError('Clip ends beyond source video')
    if not any(s['codec_type'] == 'audio' for s in info['streams']):
        raise ValueError('Source video has no audio stream')
    options = json.loads(project['settings'])
    name = f'short_{candidate.id:03}'
    captions = None
    if project['srt']:
        text = shift_srt(load_srt(project['srt']), start, end)
        if text:
            captions = store.folder / 'captions' / f'{name}.srt'
            captions.write_text(text, encoding='utf-8')
    output = store.folder / 'shorts' / f'{name}.mp4'
    temporary = store.folder / 'shorts' / f'{name}.partial.mp4'
    command = render_command(source, temporary, start, end, captions, settings['ffmpeg'],
                             options.get('crop', 'center'), options.get('offset', .5),
                             options.get('audio_preset', 'balanced'))
    try:
        proc = subprocess.run(command, capture_output=True, text=True)
        if proc.returncode:
            raise RuntimeError('FFmpeg render failed: ' + proc.stderr[-2000:])
        probe(temporary, settings['ffprobe'])
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return str(output), str(captions) if captions else None


def thumbnail(source, output, ffmpeg='ffmpeg', at=1):
    proc = subprocess.run([ffmpeg, '-y', '-ss', str(at), '-i', str(source), '-frames:v', '1',
                           '-vf', 'scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720',
                           str(output)], capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError('Thumbnail extraction failed: ' + proc.stderr[-1000:])
    return str(output)
