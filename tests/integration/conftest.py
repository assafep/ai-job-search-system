"""Opt-in real Google Docs + PostgreSQL fixtures. Never target an existing Doc."""
import os
from dataclasses import dataclass, field
from uuid import uuid4
from urllib.parse import urlsplit
import pytest
from adapters.google.auth import token_from_environment
from adapters.google.docs import GoogleDocsClient
from ajss.queue.postgres import PostgresWorkIndex
from ajss.queue.schema import (
    DELETE_EVENTS, DELETE_IDENTITIES, DELETE_ITEMS, DELETE_ROUND_ROBIN,
    PROBE_IDENTITIES, PROBE_POSTING_KEYS,
)
from ajss.queue import GDTQ, prop, children


def pytest_addoption(parser):
    parser.addoption("--live-integration", action="store_true", default=False,
                     help="Run real Google Docs/PostgreSQL tests using dedicated test credentials")


@dataclass
class LiveResources:
    queue: GDTQ
    index: PostgresWorkIndex
    namespace: str
    item_ids: list = field(default_factory=list)

    def create_item(self):
        doc=self.queue.snapshot()
        parent=prop(self.queue.queue_tab(doc,"Discovered"),"tabId")
        title="Fixture " + uuid4().hex[:12] + " / Engineer"
        self.queue.client.batch_update([{"addDocumentTab":{"tabProperties":{
            "title":title,"parentTabId":parent}}}],doc["revisionId"])
        tab=next(t for t in children(self.queue.snapshot(),parent) if prop(t,"title")==title)
        item_id=prop(tab,"tabId")
        self.item_ids.append(item_id)
        return item_id


@pytest.fixture
def live_resources(request):
    if not request.config.getoption("--live-integration"):
        pytest.skip("Requires --live-integration and dedicated nonproduction resources")
    dsn=os.environ.get("AJSS_TEST_DATABASE_URL","")
    expected_host=os.environ.get("AJSS_TEST_DATABASE_HOST","")
    branch=os.environ.get("AJSS_TEST_BRANCH_ID","")
    if not dsn or not expected_host or not branch:
        pytest.fail("Set AJSS_TEST_DATABASE_URL, AJSS_TEST_DATABASE_HOST and AJSS_TEST_BRANCH_ID")
    if branch=="br-billowing-bread-b7tbdq2n":
        pytest.fail("Refusing known production branch")
    if urlsplit(dsn).hostname!=expected_host or dsn==os.environ.get("DATABASE_URL"):
        pytest.fail("Test database must match the separately verified nonproduction endpoint")
    token=token_from_environment()
    if token is None:pytest.fail("Google test credentials are missing")
    namespace="ajss-integration-"+uuid4().hex
    index=PostgresWorkIndex(dsn)
    # Establish database access and required schema before creating cloud artifacts.
    try:
        with index._tx() as conn:
            conn.execute(PROBE_IDENTITIES)
            conn.execute(PROBE_POSTING_KEYS)
        previous_cursor=index.checkpoint("round_robin_cursor")
    except Exception:
        index.close()
        pytest.fail("Test database connection/schema preflight failed",pytrace=False)
    client=GoogleDocsClient(token,"")
    document_id=None
    resources=None
    try:
        created=client.request("POST","https://docs.googleapis.com/v1/documents",
                               {"title":"AJSS Integration Test "+namespace})
        document_id=created["documentId"]
        client.document_id=document_id
        queue=GDTQ(client)
        resources=LiveResources(queue,index,namespace)
        queue.ensure_state_tabs(["Discovered","Bookmarked","Validated","Prepared","Applied","Dead Letters Queue"])
        print("\nIntegration document: https://docs.google.com/document/d/"+document_id+"/edit")
        print("Test branch: "+branch)
        yield resources
    finally:
        try:
            if resources:
                with index._tx() as conn:
                    for item_id in resources.item_ids:
                        conn.execute(DELETE_EVENTS,(item_id,))
                        conn.execute(DELETE_ITEMS,(item_id,))
                        conn.execute(DELETE_IDENTITIES,(item_id,))
                    if previous_cursor is None:
                        conn.execute(DELETE_ROUND_ROBIN)
                    else:
                        # Restore the pre-test checkpoint through the real index below.
                        pass
                if previous_cursor is not None:index.checkpoint("round_robin_cursor",previous_cursor)
        finally:
            index.close()
            if document_id and os.environ.get("AJSS_KEEP_TEST_DOCUMENT")!="1":
                client.request("PATCH","https://www.googleapis.com/drive/v3/files/"+document_id,
                               {"trashed":True})
