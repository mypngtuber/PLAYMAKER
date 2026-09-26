"""Gemini receives transcript and/or audio only. Source video never leaves the machine."""
import hashlib
import json
from pathlib import Path
from app.ai.schemas import Analysis, Metadata
from app.core.credentials import get
from app.video.captions import load_srt, transcript

ANALYSIS_VERSION = 'analysis-v1'
METADATA_VERSION = 'metadata-v1'


def digest(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def client_for(purpose):
    from google import genai
    secret = get(purpose)
    if not secret:
        raise ValueError(f"Configure the {purpose} Gemini API key in Settings first")
    return genai.Client(api_key=secret)


def generate(client, model, contents, schema):
    from google.genai import types
    last = None
    for _ in range(2):
        try:
            response = client.models.generate_content(
                model=model, contents=contents,
                config=types.GenerateContentConfig(response_mime_type='application/json', response_schema=schema))
            if not response.text:
                raise ValueError('Gemini returned an empty response')
            return schema.model_validate_json(response.text)
        except (ValueError, json.JSONDecodeError) as exc:
            last = exc
    raise ValueError(f'Gemini returned invalid structured data: {last}')


def analyze(store, settings, duration, force=False):
    from google.genai import types
    project = store.project()
    srt, audio = project['srt'], project['audio']
    if not srt and not audio:
        raise ValueError('Provide SRT and/or audio for analysis')
    text = transcript(load_srt(srt)) if srt else ''
    audio_path = Path(audio) if audio else None
    audio_fingerprint = (audio_path.name, audio_path.stat().st_size, audio_path.stat().st_mtime_ns) if audio_path else None
    options = json.loads(project['settings'])
    model = settings['analysis_model']
    key = digest(ANALYSIS_VERSION, model, text, audio_fingerprint, options, duration)
    cached = None if force else store.cached(key)
    if cached:
        return Analysis.model_validate(cached)
    count = max(10, min(30, options['count'] * 3))
    prompt = (f'Find {count} DISTINCT, coherent standalone YouTube Shorts from this source. '
              f'Source duration: {duration:.3f} seconds. Each clip must last {options["min"]}–{options["max"]} seconds. '
              'Use absolute source timestamps HH:MM:SS.mmm and accurate duration. Find a strong hook, context, '
              'development and payoff; finish complete sentences. Avoid overlap, repetition and missing context. '
              'Scores (0-10) should assess hook, curiosity, value, pacing, payoff and completeness. '
              'Return the required JSON schema. Do NOT invent dialogue.\nTRANSCRIPT:\n' + text)
    client = client_for('analysis')
    uploaded = None
    try:
        contents = [prompt]
        if audio_path:
            # Small audio uses inline bytes; large audio uses temporary Files API upload.
            mime = {'.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.m4a': 'audio/mp4',
                    '.aac': 'audio/aac', '.flac': 'audio/flac', '.ogg': 'audio/ogg'}.get(audio_path.suffix.lower())
            if not mime:
                raise ValueError('Unsupported audio format. Use MP3, WAV, M4A, AAC, FLAC or OGG')
            if audio_path.stat().st_size <= 18 * 1024 * 1024:
                contents.append(types.Part.from_bytes(data=audio_path.read_bytes(), mime_type=mime))
            else:
                uploaded = client.files.upload(file=str(audio_path), config={'mime_type': mime})
                contents.append(uploaded)
        result = generate(client, model, contents, Analysis)
        store.cache(key, result.model_dump())
        return result
    finally:
        if uploaded is not None:
            client.files.delete(name=uploaded.name)
        client.close()


def metadata(store, settings, candidate, captions_text='', force=False):
    model = settings['metadata_model']
    context = {'topic': candidate.topic, 'hook': candidate.hook, 'summary': candidate.summary,
               'keywords': candidate.keywords, 'duration': candidate.duration, 'transcript': captions_text}
    key = digest(METADATA_VERSION, model, context)
    cached = None if force else store.cached(key)
    if cached:
        return Metadata.model_validate(cached)
    client = client_for('metadata')
    try:
        result = generate(client, model,
                          'Generate five accurate YouTube Shorts title options, a concise description, relevant tags, '
                          '2-4 hashtags, category_id and keywords. Do not invent facts. Return JSON.\n' + json.dumps(context), Metadata)
        store.cache(key, result.model_dump())
        return result
    finally:
        client.close()
