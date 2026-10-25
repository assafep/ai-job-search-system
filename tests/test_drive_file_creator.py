import json
import urllib.error
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from application_service.drive_file_creator import FileCreator


def test_owner_create_move_and_service_tags_are_recoverable():
    calls, files = [], {}
    class Owner:
        def _request(self, method, url, payload=None):
            calls.append(('owner', method))
            if method == 'GET':
                if not files: raise urllib.error.HTTPError(url, 404, 'missing', None, None)
                return dict(files['allocated'])
            data = json.loads(payload)
            if method == 'POST':
                assert data['id'] == 'allocated'
                assert 'parents' not in data
                files['allocated'] = dict(data, parents=['owner-root'])
            else:
                query = parse_qs(urlsplit(url).query)
                assert query['removeParents'] == ['owner-root']
                files['allocated']['parents'] = query['addParents']
            return dict(files['allocated'])
    class Service:
        def _request(self, method, url, payload=None):
            calls.append(('service', method))
            assert json.loads(payload) == {'appProperties': {'idempotency_key': 'retry-key'}}
    creator = object.__new__(FileCreator)
    creator.owner, creator.service = Owner(), Service()
    creator.allocate = lambda marker, owner: 'allocated'
    metadata = {'name': 'Posting.json', 'mimeType': 'application/json', 'parents': ['chosen-folder'],
                'appProperties': {'idempotency_key': 'retry-key'}}
    assert creator.create(metadata)['parents'] == ['chosen-folder']
    assert creator.create(metadata)['id'] == 'allocated'
    assert calls.count(('owner', 'POST')) == 1
    assert calls.count(('owner', 'PATCH')) == 1
    assert calls.count(('service', 'PATCH')) == 2


def test_trashed_allocated_file_is_not_silently_recreated():
    creator = object.__new__(FileCreator)
    creator.allocate = lambda marker, owner: 'allocated'
    creator.owner = SimpleNamespace(_request=lambda *args: {'trashed': True})
    creator.service = object()
    with pytest.raises(ValueError, match='trashed'):
        creator.create({'name': 'Resume.pdf', 'mimeType': 'application/pdf', 'parents': ['chosen-folder']})
