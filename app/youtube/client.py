"""Explicit OAuth and resumable upload, without running any local HTTP server."""
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from app.core.credentials import get, set_secret, delete

SCOPES = ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.readonly']
REDIRECT = 'http://127.0.0.1:8765/'


def start_auth(client_secrets):
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes=SCOPES)
    flow.redirect_uri = REDIRECT
    url, state = flow.authorization_url(access_type='offline', prompt='consent')
    return flow, url, state


def finish_auth(flow, redirect_url, state):
    params = parse_qs(urlparse(redirect_url).query)
    if params.get('state', [None])[0] != state or 'code' not in params:
        raise ValueError('Authorization URL is missing a valid code/state')
    flow.fetch_token(authorization_response=redirect_url)
    set_secret('youtube_oauth', flow.credentials.to_json())
    return service()


def disconnect():
    delete('youtube_oauth')


def service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    raw = get('youtube_oauth')
    if not raw:
        raise ValueError('Connect a YouTube channel first')
    credentials = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        set_secret('youtube_oauth', credentials.to_json())
    if not credentials.valid:
        raise ValueError('YouTube authentication expired; reconnect your channel')
    return build('youtube', 'v3', credentials=credentials, cache_discovery=False)


def channel_name(api):
    response = api.channels().list(part='snippet', mine=True).execute()
    return response['items'][0]['snippet']['title'] if response.get('items') else 'No YouTube channel found'


def upload(api, video, metadata, title, visibility, publish_at=None, image=None, progress=None):
    from googleapiclient.http import MediaFileUpload
    if not Path(video).is_file():
        raise FileNotFoundError(video)
    if not title.strip():
        raise ValueError('Enter a title before uploading')
    if publish_at:
        if publish_at.tzinfo is None or publish_at.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError('Schedule must be a future timezone-aware date/time')
        visibility = 'private'
    body = {'snippet': {'title': title[:100], 'description': metadata.description[:5000],
                        'tags': metadata.tags[:30], 'categoryId': metadata.category_id},
            'status': {'privacyStatus': visibility, 'selfDeclaredMadeForKids': False}}
    if publish_at:
        body['status']['publishAt'] = publish_at.astimezone(timezone.utc).isoformat()
    request = api.videos().insert(part='snippet,status', body=body,
                                  media_body=MediaFileUpload(str(video), mimetype='video/mp4', chunksize=8*1024*1024, resumable=True))
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status and progress:
            progress(round(status.progress() * 100))
    video_id = response['id']
    if image and Path(image).is_file():
        api.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(image), mimetype='image/jpeg')).execute()
    return video_id
