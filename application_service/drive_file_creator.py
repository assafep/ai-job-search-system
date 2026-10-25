"""Create app-owned files, then place them in the configured shared folder.

Service accounts cannot own My Drive files. The existing drive.file user grant
can create/move the app's own files; the service account edits them after they
inherit the chosen folder's permissions. IDs are allocated durably before create
so a response loss cannot produce another file on retry. No broader scope is used.
"""
import hashlib
import json
import urllib.error
import urllib.parse


def allocate_id(marker, owner):
    from .database import connect, installation_lock
    from .initialization import resource, save_resource
    with connect() as connection, installation_lock(connection, 'drive-file-ids'):
        records = resource(connection, 'drive_file_ids') or {}
        if marker not in records:
            generated = owner._request('GET', 'https://www.googleapis.com/drive/v3/files/generateIds?count=1&space=drive')
            records[marker] = generated['ids'][0]
            save_resource(connection, 'drive_file_ids', records)
        return records[marker]


class FileCreator:
    def __init__(self, service, owner_token, allocator=allocate_id):
        from .google_drive_artifacts import GoogleDriveArtifactStore
        self.service = service
        self.owner = GoogleDriveArtifactStore(owner_token, 'root')
        self.allocate = allocator

    def create(self, metadata):
        parents = metadata.get('parents') or []
        if len(parents) != 1:
            raise ValueError('New files must have exactly one configured destination parent')
        marker = hashlib.sha256(json.dumps({key: metadata.get(key) for key in
            ('parents', 'name', 'mimeType')}, sort_keys=True).encode()).hexdigest()
        identity = self.allocate(marker, self.owner)
        url = 'https://www.googleapis.com/drive/v3/files/' + identity
        fields = 'id,name,mimeType,parents,webViewLink,trashed'
        try:
            file = self.owner._request('GET', url + '?fields=' + fields)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            original = {key: value for key, value in metadata.items() if key != 'parents'}
            original.update(id=identity, appProperties=dict(metadata.get('appProperties') or {}, ajss_create_marker=marker))
            try:
                file = self.owner._request('POST', 'https://www.googleapis.com/drive/v3/files?fields=' + fields,
                                           json.dumps(original).encode())
            except urllib.error.HTTPError as conflict:
                if conflict.code != 409:
                    raise
                file = self.owner._request('GET', url + '?fields=' + fields)
        if file.get('trashed'):
            raise ValueError('The allocated Drive file was trashed; reconcile it before recreating this path')
        if file.get('name') != metadata['name'] or file.get('mimeType') != metadata['mimeType']:
            raise ValueError('The allocated Drive file changed; reconcile before updating it')
        current = file.get('parents') or []
        if parents[0] not in current:
            query = {'addParents': parents[0], 'fields': fields}
            if current:
                query['removeParents'] = ','.join(current)
            # The owner can move its own app-created file; the service account
            # cannot remove it from the owner's My Drive root.
            file = self.owner._request('PATCH', url + '?' + urllib.parse.urlencode(query), b'{}')
        if metadata.get('appProperties'):
            # appProperties are private to the OAuth app. Copy retry/completion
            # tags using the service identity that later queries those tags.
            self.service._request('PATCH', url, json.dumps({'appProperties': metadata['appProperties']}).encode())
        return file
