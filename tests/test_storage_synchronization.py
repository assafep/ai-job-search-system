import json
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Lock

from services.approval_registry import ApprovalRegistry
from services.event_dispatcher import dispatch
from services.application_service_client import ApplicationServiceClient
from services.workload_registry import WorkloadRegistry


def test_eight_threads_serialize_reads_and_writes_through_shared_client():
    guard = Lock()
    active, peak = [0], [0]
    methods = []
    def opener(request):
        with guard:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            methods.append(request.get_method())
        time.sleep(0.002)
        with guard:
            active[0] -= 1
        return BytesIO(b'{}')
    client = ApplicationServiceClient(opener=opener)
    def work(index):
        for _ in range(5):
            client.get_application(str(index))
            client.append_event(str(index), 'test', {}, 'key-' + str(index))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(8)))
    assert peak[0] == 1
    assert set(methods) == {'GET', 'POST'}
    statistics = client.storage_statistics()
    assert {key: statistics[key] for key in ('requests', 'peak_concurrent_requests', 'active_requests')} == {
        'requests': 80, 'peak_concurrent_requests': 1, 'active_requests': 0}
    assert statistics['operations']['GET /applications/{id}']['requests'] == 40
    assert statistics['operations']['POST /applications/{id}/events']['requests'] == 40
    assert sum(item['wait_seconds'] for item in statistics['operations'].values()) > 0


def test_workload_json_writes_do_not_clobber_each_other(tmp_path):
    path = tmp_path / 'workloads.json'
    registry = WorkloadRegistry(storage_path=str(path))
    def work(index):
        for number in range(10):
            envelope = dispatch('application_package_schedule', {}, '{}-{}'.format(index, number), registry)
            registry.claim(envelope['workload_id'])
            registry.complete(envelope['workload_id'])
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(8)))
    content = json.loads(path.read_text())
    assert len(content) == 80
    assert all(record['status'] == 'succeeded' for record in content.values())
    assert len(WorkloadRegistry(storage_path=str(path)).records) == 80


def test_approval_idempotency_is_atomic_across_eight_threads(tmp_path):
    path = tmp_path / 'approvals.json'
    approvals = ApprovalRegistry(str(path))
    def work(index):
        return approvals.request('submit_job_application', 'work', {}, 'same-operation').request_id
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(work, range(80)))
    assert len(set(ids)) == 1
    assert len(json.loads(path.read_text())['records']) == 1
