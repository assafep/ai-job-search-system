from unittest.mock import Mock, call
import pytest
from ajss.worker.effects import apply_effects
from ajss.worker.stages import bookmark_discovered, validate_bookmarked, prepare_validated, submission_plan
from ajss.submission.processing import submit_prepared


def test_bookmark_success_uses_verified_identity_and_archive(item, index):
    reader = Mock(return_value={"description":"Python " * 100, "company":"Verified Labs", "role":"Verified Role", "resolved_url":"https://jobs.example.test/canonical"})
    store = Mock(spec_set=["save_path"])
    fallback = Mock()
    output = bookmark_discovered(item, reader, index, smart_reader=fallback, store=store)
    assert output["code"] == "BOOKMARKED"
    assert output["data"]["company"] == "Verified Labs"
    reader.assert_called_once_with(item["posting_url"])
    index.reserve_identity.assert_not_called()
    store.save_path.assert_not_called()
    applied = apply_effects(output, {"index": index, "store": store})
    assert applied["code"] == "BOOKMARKED"
    assert "_effects" not in applied
    index.reserve_identity.assert_has_calls([
        call("item-1", "Example Labs", "Engineer", item["posting_url"], ""),
        call("item-1", "Verified Labs", "Verified Role", "https://jobs.example.test/canonical", "")])
    store.save_path.assert_called_once()
    assert store.save_path.call_args.args[2:] == ("application/json", "posting-item-1")
    fallback.assert_not_called()


@pytest.mark.parametrize("matches,read_calls", [(["old"],0), ([None,"old"],1)])
def test_bookmark_duplicates_skip_remaining_work(item,index,matches,read_calls):
    index.lookup_identity.side_effect = matches
    reader = Mock(return_value={"description":"Python " * 100})
    store, fallback = Mock(), Mock()
    output = bookmark_discovered(item,reader,index,store=store,smart_reader=fallback)
    assert output["code"] == "DUPLICATE"
    assert output["data"] == {"original_item_id":"old"}
    assert "_effects" not in output
    assert reader.call_count == read_calls
    index.reserve_identity.assert_not_called()
    store.assert_not_called()
    assert store.mock_calls == []
    fallback.assert_not_called()


@pytest.mark.parametrize("error,status", [(TimeoutError("fixture"),"RETRYABLE_FAILURE"),(ValueError("fixture"),"PERMANENT_FAILURE")])
def test_bookmark_reader_failure(item,index,error,status):
    reader = Mock(side_effect=error)
    output = bookmark_discovered(item,reader,index)
    assert (output["code"],output["execution_status"]) == ("ERROR",status)
    assert "_effects" not in output
    index.reserve_identity.assert_not_called()
    reader.assert_called_once()


def test_bookmark_fallback_is_explicit_and_called_once(item,index):
    primary = Mock(side_effect=ValueError("fixture"))
    fallback = Mock(return_value={"description":"Python " * 100})
    assert bookmark_discovered(item,primary,index,smart_reader=fallback)["code"] == "BOOKMARKED"
    primary.assert_called_once_with(item["posting_url"])
    fallback.assert_called_once_with(item["posting_url"])


@pytest.mark.parametrize("matched,code", [(True,"MATCH"),(False,"BELOW_THRESHOLD")])
def test_validation_maps_fixed_scorer_result(monkeypatch,item,matched,code):
    score = {"is_match":matched, "score":3}
    scorer = Mock(return_value=score)
    monkeypatch.setattr("ajss.worker.stages.decide_match",scorer)
    assert validate_bookmarked(item,{"skills":["Python"]},["Python"],2)["code"] == code
    scorer.assert_called_once_with(item["description"],["Python"],{"skills":["Python"]},2)


def test_preparation_passes_fixed_inputs_and_writes_expected_artifacts(monkeypatch,item):
    cover = "Supported cover letter. " * 5
    writer = Mock(return_value=cover)
    pdf = Mock(return_value=b"%PDF-fixed")
    monkeypatch.setattr("ajss.documents.generator.generate_application_pdf",pdf)
    store = Mock(spec_set=["save_path"])
    store.save_path.return_value = {"id":"package-id"}
    output = prepare_validated(item,{},b"%PDF-resume",writer,store)
    assert output["code"] == "PREPARED"
    assert "package" not in output["data"]
    store.save_path.assert_not_called()
    applied = apply_effects(output, {"store": store})
    assert applied["data"]["package"] == "package-id"
    assert "_effects" not in applied
    writer.assert_called_once_with({"profile":{}, "role":item["role"], "company":item["company"], "description":item["description"], "resume_pdf":b"%PDF-resume"})
    pdf.assert_called_once_with(cover,resume_pdf=b"%PDF-resume",applicant_profile={},calendly_url=None,application=item)
    assert store.save_path.call_count == 2
    assert store.save_path.call_args_list[0].args[1:] == (b"%PDF-fixed","application/pdf","submission-package-item-1")
    assert store.save_path.call_args_list[1].args[1:] == (cover.encode(),"text/plain","cover-letter-item-1")


@pytest.mark.parametrize("failure,status", [(TimeoutError("fixture"),"RETRYABLE_FAILURE"),(ValueError("fixture"),"PERMANENT_FAILURE"),(None,"PERMANENT_FAILURE")])
def test_preparation_failure_never_saves(monkeypatch,item,failure,status):
    writer = Mock(side_effect=failure,return_value="short")
    pdf = Mock()
    monkeypatch.setattr("ajss.documents.generator.generate_application_pdf",pdf)
    store = Mock()
    result = prepare_validated(item,{},b"resume",writer,store)
    assert (result["code"],result["execution_status"]) == ("ERROR",status)
    pdf.assert_not_called()
    assert store.mock_calls == []


@pytest.mark.parametrize("status", ["Submitted successfully","Already submitted"])
def test_submission_checkpoints_before_action_and_prevents_repeat(item,index,status):
    receipt = {"status":status,"confirmation_text":"Fixture acceptance"}
    def action(value):
        assert index.checkpoint("submission:item-1")["status"] == "begun"
        return receipt
    worker = Mock(side_effect=action)
    assert submit_prepared(item,index,[worker])["code"] == "SUBMITTED"
    assert submit_prepared(item,index,[worker])["code"] == "ALREADY_SUBMITTED"
    worker.assert_called_once_with(item)
    assert index.checkpoint("submission:item-1") == {"status":"confirmed","evidence":receipt}


@pytest.mark.parametrize("result", [TimeoutError("fixture"),{}, {"status":"Submitted successfully"}, {"status":"Submission unconfirmed","blockers":["Missing receipt"]}])
def test_uncertain_submission_never_falls_back_or_retries(item,index,result):
    worker = Mock(side_effect=result if isinstance(result,Exception) else None,return_value=result)
    fallback = Mock()
    for _ in range(2):
        assert submit_prepared(item,index,[worker],strong_agent=fallback)["code"] == "SUBMISSION_UNCONFIRMED"
    worker.assert_called_once()
    fallback.assert_not_called()
    assert index.checkpoint("submission:item-1")["status"] == "begun"


def test_submit_worker_plans_the_attempt_without_performing_it(item,index):
    receipt = {"status":"Submitted successfully","confirmation_text":"Fixture acceptance"}
    worker = Mock(return_value=receipt)
    planned = submission_plan(item,[worker])
    planned["attempt_id"] = "attempt-1"
    worker.assert_not_called()
    index.checkpoint.assert_not_called()
    applied = apply_effects(planned, {"index": index})
    assert applied["code"] == "SUBMITTED"
    assert applied["attempt_id"] == "attempt-1"
    assert "_effects" not in applied
    worker.assert_called_once_with(item)
    assert index.checkpoint("submission:item-1")["status"] == "confirmed"


def test_definite_non_submission_can_try_next_strategy(item,index):
    blocked = Mock(return_value={"status":"Blocked","blockers":["Unsupported form"]})
    prepared = Mock(return_value={"status":"Prepared but not submitted","blockers":["Missing answer"]})
    assert submit_prepared(item,index,[blocked,prepared])["code"] == "MANUAL_REQUIRED"
    blocked.assert_called_once(); prepared.assert_called_once()
    assert index.checkpoint("submission:item-1")["status"] == "not_submitted"
