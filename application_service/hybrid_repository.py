"""Google journal plus PostgreSQL completed records and shared write locking.

Journal copies of completed records are retained for audit/recovery. SQL stores
the complete snapshot and relational records atomically; no destructive journal
migration is implicit in deployment. Every call re-observes the journal under a
database session lock so separate function instances cannot race its writes.
"""
from dataclasses import asdict, is_dataclass
from threading import RLock, local
from contextlib import contextmanager
from .database import connect, installation_lock
from .journal_format import COMPLETED_STATES
from .models import Application, Artifact, Event


def plain(value):
    if is_dataclass(value):
        return asdict(value)
    return value


class HybridRepository:
    def __init__(self, journal, connection_factory=connect):
        self.journal = journal
        self.connection_factory = connection_factory
        self._lock = RLock()
        self._sessions = local()

    def _snapshot(self, connection, application):
        from psycopg.types.json import Jsonb
        app = plain(application)
        identity = app['application_id']
        events = [plain(e) for e in self.journal.list_events(identity)]
        artifacts = [plain(a) for a in self.journal.list_artifacts(identity)]
        research = self.journal.list_research(identity)
        snapshot = {'application': app, 'events': events, 'artifacts': artifacts, 'research': research}
        with connection.transaction():
            connection.execute('''INSERT INTO applications
                (application_id,company,role,posting_url,status,created_at,updated_at,next_action,next_action_at,
                 source_document_id,source_record_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(application_id) DO UPDATE SET company=excluded.company,role=excluded.role,
                posting_url=excluded.posting_url,status=excluded.status,updated_at=excluded.updated_at,
                next_action=excluded.next_action,next_action_at=excluded.next_action_at''',
                (identity, app['company'], app['role'], app['posting_url'], app['status'],
                 app['created_at'], app['updated_at'], app.get('next_action'), app.get('next_action_at'),
                 self.journal.client.document_id, identity))
            connection.execute('DELETE FROM events WHERE application_id=%s', (identity,))
            for e in events:
                connection.execute('INSERT INTO events(event_id,application_id,event_type,occurred_at,data) VALUES(%s,%s,%s,%s,%s)',
                                   (e['event_id'], identity, e['event_type'], e['occurred_at'], Jsonb(e['data'])))
            connection.execute('DELETE FROM artifacts WHERE application_id=%s', (identity,))
            for a in artifacts:
                connection.execute('INSERT INTO artifacts(artifact_id,application_id,name,drive_file_id,mime_type,created_at) VALUES(%s,%s,%s,%s,%s,%s)',
                                   (a['artifact_id'], identity, a['name'], a.get('drive_file_id'), a.get('mime_type'), a['created_at']))
            connection.execute('INSERT INTO completed_snapshots(application_id,snapshot) VALUES(%s,%s) '
                               'ON CONFLICT(application_id) DO UPDATE SET snapshot=excluded.snapshot', (identity, Jsonb(snapshot)))

    @staticmethod
    def _forget(connection, identity):
        with connection.transaction():
            for table in ('completed_snapshots', 'events', 'artifacts', 'applications'):
                connection.execute('DELETE FROM ' + table + ' WHERE application_id=%s', (identity,))

    @contextmanager
    def mutation_scope(self):
        """Hold the journal lock across a bulk item's precondition and mutation."""
        with self._lock:
            if getattr(self._sessions, 'connection', None) is not None:
                yield
                return
            with self.connection_factory() as connection, installation_lock(connection, 'journal:' + self.journal.client.document_id):
                self._sessions.connection = connection
                try:
                    yield
                finally:
                    del self._sessions.connection

    def __getattr__(self, name):
        target = getattr(self.journal, name)
        if not callable(target):
            return target

        def call(*args, **kwargs):
            with self.mutation_scope():
                connection = self._sessions.connection
                self.journal.client.invalidate_document()
                if self.journal.artifacts:
                    self.journal.artifacts._forget_metadata()
                self.journal._idempotency.clear()
                result = target(*args, **kwargs)
                if name == 'list_applications':
                    for app in result:
                        if app.status in COMPLETED_STATES:
                            self._snapshot(connection, app)
                elif name == 'delete_application':
                    self._forget(connection, args[0])
                elif name in {'create_application', 'get_application', 'transition', 'append_event',
                              'update_application', 'store_artifact', 'write_research', 'delete_last_event',
                              'repair_application_journal', 'rollback_ready_application'}:
                    identity = getattr(result, 'application_id', None) or (args[0] if args else None)
                    app = self.journal.get_application(identity) if identity else None
                    if app and app.status in COMPLETED_STATES:
                        self._snapshot(connection, app)
                    elif app:
                        self._forget(connection, identity)
                return result
        return call

    def _database_workload_path(self, path):
        import os
        from .file_paths import normalize_file_path
        from .storage_layout import WORKLOAD_STATE_DIRECTORY
        normalized = normalize_file_path(path)
        enabled = os.environ.get('AJSS_WORKLOAD_FILE_BACKEND') == 'database'
        return normalized, enabled and normalized.startswith(WORKLOAD_STATE_DIRECTORY + '/')

    def store_file(self, path, content, mime_type=None, idempotency_key=None):
        """Explicit database storage for workflow-owned files in hybrid installs.

        Journal/profile/application artifacts keep their configured storage. This
        is a configured backend, never a fallback after a failed Drive write.
        """
        import base64
        import hashlib
        from .initialization import resource, save_resource
        normalized, database = self._database_workload_path(path)
        if not database:
            return self.__getattr__('store_file')(path, content, mime_type, idempotency_key)
        if not isinstance(content, bytes):
            raise TypeError('file content must be bytes')
        identity = hashlib.sha256(normalized.encode()).hexdigest()
        digest = hashlib.sha256(content).hexdigest()
        key = 'workload_file:' + identity
        retry_key = ('workload_file_retry:' + hashlib.sha256(idempotency_key.encode()).hexdigest()
                     if idempotency_key else None)
        with self._lock, self.connection_factory() as connection, installation_lock(connection, 'workload-files'):
            if retry_key:
                previous = resource(connection, retry_key)
                if previous:
                    if previous['path'] != normalized or previous['sha256'] != digest or previous['mime_type'] != mime_type:
                        raise ValueError('idempotency_key already used with another file payload')
                    return previous['file']
            metadata = dict(file_id='internal_' + identity, path=normalized,
                            name=normalized.rsplit('/', 1)[-1], mime_type=mime_type)
            value = dict(file=metadata, content_base64=base64.b64encode(content).decode('ascii'))
            # File bytes and their retry receipt commit together.
            with connection.transaction():
                save_resource(connection, key, value)
                if retry_key:
                    save_resource(connection, retry_key, dict(path=normalized, sha256=digest,
                                                            mime_type=mime_type, file=metadata))
            return metadata

    def read_file(self, path):
        import base64
        import hashlib
        from .initialization import resource
        normalized, database = self._database_workload_path(path)
        if database:
            identity = hashlib.sha256(normalized.encode()).hexdigest()
            with self._lock, self.connection_factory() as connection:
                value = resource(connection, 'workload_file:' + identity)
            if value:
                return dict(file=value['file'], content=base64.b64decode(value['content_base64']))
        # Pre-existing receipts and checkpoints remain readable from their
        # original location; enabling this backend never discards that history.
        return self.__getattr__('read_file')(path)

    def readiness(self):
        with self.connection_factory() as connection:
            connection.execute('SELECT application_id FROM completed_snapshots LIMIT 1')
        status = self.journal_status()
        if not status['valid']:
            raise RuntimeError('Journal state tabs are invalid')
        # Reading root metadata verifies the runtime principal's Drive access.
        store = self.journal.artifacts
        store._request('GET', 'https://www.googleapis.com/drive/v3/files/' + store.root_folder_id + '?fields=id')
        return {'database': True, 'drive': True, 'journal': True}
