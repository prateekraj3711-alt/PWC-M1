import os
import asyncio
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = [
    'https://www.googleapis.com/auth/drive',
]


def get_drive_service():
    creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
    if not creds_json:
        raise RuntimeError('GOOGLE_CREDENTIALS_JSON not set')
    creds = service_account.Credentials.from_service_account_info(
        __import__('json').loads(creds_json), scopes=SCOPES
    )
    return build('drive', 'v3', credentials=creds, cache_discovery=False)


class DriveClient:
    def __init__(self):
        self.service = get_drive_service()

    async def ensure_child_folder(self, parent_id: Optional[str], name: str) -> str:
        params = {
            'q': f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and '{parent_id or 'root'}' in parents and trashed = false",
            'fields': 'files(id, name)',
            'spaces': 'drive'
        }
        res = await asyncio.to_thread(self.service.files().list(**params).execute)
        files = res.get('files', [])
        if files:
            return files[0]['id']
        file_metadata = {
            'name': name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id] if parent_id else None
        }
        created = await asyncio.to_thread(self.service.files().create(body=file_metadata, fields='id').execute)
        return created['id']

    async def ensure_root_folder(self, name: str) -> str:
        return await self.ensure_child_folder(None, name)

    async def upload_file(self, parent_id: str, path: str, mime_type: str) -> str:
        media = MediaFileUpload(path, mimetype=mime_type, resumable=True)
        body = {'name': os.path.basename(path), 'parents': [parent_id]}
        created = await asyncio.to_thread(self.service.files().create(body=body, media_body=media, fields='id').execute)
        return created['id']


