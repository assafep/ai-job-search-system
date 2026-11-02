import pytest
from ajss.submission import configured_strategies, strategy
from ajss.worker.stages import submit_prepared
from ajss.queue.store import WorkIndex


def test_configuration_defaults_disabled(monkeypatch):
    monkeypatch.delenv('AJSS_SUBMISSION_BACKEND',raising=False)
    assert configured_strategies({})==()
    monkeypatch.setenv('AJSS_SUBMISSION_BACKEND','unexpected')
    with pytest.raises(ValueError):configured_strategies({})


def test_prepared_package_reaches_submitter_once(tmp_path):
    class Store:
        def read_path(self,path):
            assert path=='prepared.pdf'
            return {'content':b'%PDF-fixture'}
    class Loader:
        def profile(self):return {'skills':['Python']}
    calls=[]
    def browser(context):
        calls.append(context)
        return {'status':'Submitted successfully','confirmation_text':'Application received'}
    worker=strategy({'store':Store(),'loader':Loader()},browser)
    item={'item_id':'one','posting_url':'https://www.comeet.com/jobs/example/id/role',
          'events':[{'outcome':{'code':'PREPARED','data':{'path':'prepared.pdf'}}}]}
    index=WorkIndex(tmp_path/'index.db')
    assert submit_prepared(item,index,[worker])['code']=='SUBMITTED'
    assert submit_prepared(item,index,[worker])['code']=='ALREADY_SUBMITTED'
    assert len(calls)==1
    assert calls[0]['package_bytes']==b'%PDF-fixture'


def test_missing_package_never_calls_browser():
    def browser(context):raise AssertionError('Must not submit')
    result=strategy({},browser)({'events':[]})
    assert result['status']=='Blocked'


def test_submission_public_imports_preserve_function_identity():
    from ajss.submission.processing import submit_prepared as implementation
    from ajss.submission import submit_prepared as public
    from ajss.worker.stages import submit_prepared as compatibility
    from adapters.job_boards.submission.contracts import validate_submission_outcome
    from adapters.job_boards.submission import validate_submission_outcome as exported
    from adapters.job_boards.comeet_submit import submit as old_adapter
    from adapters.job_boards.submission.comeet.adapter import submit as adapter
    assert implementation is public is compatibility
    assert exported is validate_submission_outcome
    assert old_adapter is adapter


def test_comeet_worker_is_packaged_beside_adapter():
    from pathlib import Path
    from adapters.job_boards.submission.comeet import adapter
    assert Path(adapter.__file__).with_name('browser.cjs').is_file()
