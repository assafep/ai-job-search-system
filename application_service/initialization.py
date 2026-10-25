"""Explicit API-owned initialization of an empty provisioned Drive root."""
import json
import os
import urllib.parse
import urllib.error
from .database import connect, installation_lock
from .google_auth import token_from_environment
from .google_docs_repository import GoogleDocsClient, GoogleDocsRepository, document_id_from_url
from .google_drive_artifacts import folder_id_from_url


def resource(connection, key):
    row = connection.execute('SELECT data FROM installation_resources WHERE resource_key=%s', (key,)).fetchone()
    return row[0] if row else None


def save_resource(connection, key, value):
    from psycopg.types.json import Jsonb
    connection.execute('INSERT INTO installation_resources(resource_key,data) VALUES(%s,%s) '
                       'ON CONFLICT(resource_key) DO UPDATE SET data=excluded.data', (key, Jsonb(value)))


def _quota_exceeded(exc):
    try:
        payload = json.loads(exc.read().decode())
    except (ValueError, AttributeError):
        return False
    reasons = [item.get('reason') for item in (payload.get('error') or {}).get('errors') or []]
    return 'storageQuotaExceeded' in reasons


def journal_id():
    with connect() as connection:
        value = resource(connection, 'journal')
    return value.get('document_id') if value else None


def initialize():
    root = os.environ['GOOGLE_ARTIFACT_ROOT_URL']
    folder = folder_id_from_url(root)
    token = token_from_environment()
    client = GoogleDocsClient(token, '')
    with connect() as connection, installation_lock(connection, 'initialize:' + folder):
        saved = resource(connection, 'journal') or {}
        if saved.get('root_folder_id') not in (None, folder):
            raise RuntimeError('Journal is bound to a different Drive root')
        doc_id = saved.get('document_id')
        if not doc_id:
            query = "'%s' in parents and trashed=false and appProperties has { key='ajss_kind' and value='journal' }" % folder.replace("'", "\\'")
            params = urllib.parse.urlencode({'q': query, 'fields': 'nextPageToken,files(id)', 'pageSize': 100})
            found = client.request('GET', 'https://www.googleapis.com/drive/v3/files?' + params)
            if found.get('nextPageToken') or len(found.get('files', [])) > 1:
                raise RuntimeError('Multiple managed journals found; reconciliation required')
            if found.get('files'):
                doc_id = found['files'][0]['id']
            elif saved.get('pending'):
                # Native Docs cannot use preallocated IDs. Never blindly create
                # after a response loss: the marker may not yet be searchable.
                raise RuntimeError('Journal creation outcome is unknown; retry discovery or reconcile the pending journal')
            else:
                save_resource(connection, 'journal', {'pending': True, 'root_folder_id': folder})
                try:
                    created = client.request('POST', 'https://www.googleapis.com/drive/v3/files?fields=id', {
                        'name': 'Application Journal', 'mimeType': 'application/vnd.google-apps.document',
                        'parents': [folder], 'appProperties': {'ajss_kind': 'journal'},
                    })
                    doc_id = created['id']
                except urllib.error.HTTPError as exc:
                    # Explicit client rejection proves no create was accepted.
                    if exc.code in (400, 401, 403):
                        save_resource(connection, 'journal', {'root_folder_id': folder})
                    configured = os.environ.get('GOOGLE_INTERVIEW_JOURNAL_URL')
                    if exc.code == 403 and configured and _quota_exceeded(exc):
                        # A service account has no My Drive quota, so it cannot
                        # create the journal file. Bind the journal that Drive
                        # provisioning already pointed at when that file is readable.
                        doc_id = document_id_from_url(configured)
                        client.request('GET', 'https://www.googleapis.com/drive/v3/files/' + doc_id + '?fields=id')
                    else:
                        raise
            save_resource(connection, 'journal', {'document_id': doc_id, 'root_folder_id': folder})
        repository = GoogleDocsRepository(token, doc_id, root)
        status = repository.repair_journal()
        if not status['valid']:
            raise RuntimeError('Journal initialization did not produce valid state tabs')
        return {'document_id': doc_id, 'root_folder_id': folder, 'journal_valid': True}
