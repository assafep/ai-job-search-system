"""User Drive access limited to files this application creates."""
import os

from .google_auth import GoogleAccessToken
from .google_drive_artifacts import GoogleDriveArtifactStore

FILE_SCOPE_VARS = (
    'GOOGLE_DRIVE_FILE_CLIENT_ID',
    'GOOGLE_DRIVE_FILE_CLIENT_SECRET',
    'GOOGLE_DRIVE_FILE_REFRESH_TOKEN',
)


def file_credentials(environ=None):
    environ = os.environ if environ is None else environ
    values = [environ.get(name) for name in FILE_SCOPE_VARS]
    if not any(values):
        return None
    if not all(values):
        raise RuntimeError('drive.file login requires ' + ', '.join(FILE_SCOPE_VARS))
    return values


def completion_store(service_account_store, environ=None):
    """Return a store that can upload file bytes, or the service-account store."""
    credentials = file_credentials(environ)
    if credentials is None:
        return service_account_store
    client_id, client_secret, refresh_token = credentials
    token = GoogleAccessToken(refresh_token, client_id, client_secret)
    bootstrap = GoogleDriveArtifactStore(token, 'root')
    folder = bootstrap.app_owned_folder()
    return GoogleDriveArtifactStore(token, folder['id'])
