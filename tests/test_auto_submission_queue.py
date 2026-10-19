from services.automation_daemon import AutomationDaemon
from services.workload_registry import WorkloadRegistry


def test_scheduler_only_consumes_auto_apply_queue():
    class Client:
        def list_applications(self, status):
            assert status == 'Prepared'
            return [{'application_id': 'auto-app'}]
    class Runtime:
        pass
    daemon = AutomationDaemon(WorkloadRegistry(), Runtime(), Client(), lambda p, c: {})
    envelopes = daemon.enqueue_ready_submission()
    assert len(envelopes) == 1
    assert envelopes[0]['event']['payload'] == {'application_id': 'auto-app'}
