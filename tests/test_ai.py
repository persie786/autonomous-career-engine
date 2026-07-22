import pytest
from modules import ai_evaluator
from utils import ai_router

SAMPLE_JOB = {"role": "Software Engineer", "company": "Acme", "job_description": "A great job."}


def test_evaluate_job_valid_go(monkeypatch):
    monkeypatch.setattr(
        ai_evaluator, "generate_json",
        lambda *a, **k: ('{"decision": "GO", "match_score": 0.85, "reason": "Good fit"}', "mock:model"),
    )
    result = ai_evaluator.evaluate_job(SAMPLE_JOB)
    assert result["decision"] == "GO"
    assert result["match_score"] == 0.85
    assert result["model_used"] == "mock:model"


def test_evaluate_job_clamps_out_of_range_score(monkeypatch):
    monkeypatch.setattr(
        ai_evaluator, "generate_json",
        lambda *a, **k: ('{"decision": "GO", "match_score": 1.5, "reason": "x"}', "mock:model"),
    )
    assert ai_evaluator.evaluate_job(SAMPLE_JOB)["match_score"] == 1.0


def test_evaluate_job_invalid_decision_returns_none(monkeypatch):
    monkeypatch.setattr(
        ai_evaluator, "generate_json",
        lambda *a, **k: ('{"decision": "MAYBE", "match_score": 0.5, "reason": "x"}', "mock:model"),
    )
    assert ai_evaluator.evaluate_job(SAMPLE_JOB) is None


def test_evaluate_job_malformed_json_returns_none(monkeypatch):
    monkeypatch.setattr(ai_evaluator, "generate_json", lambda *a, **k: ("not valid json", "mock:model"))
    assert ai_evaluator.evaluate_job(SAMPLE_JOB) is None


def test_run_sourcing_pipeline_routes_by_threshold(temp_db, monkeypatch):
    monkeypatch.setattr(ai_evaluator.time, "sleep", lambda *_: None)  # skip the real pacing delay in tests
    monkeypatch.setattr(ai_evaluator, "load_settings", lambda: {"confidence_threshold": 0.75, "red_flags": []})

    responses = iter([
        ('{"decision": "GO", "match_score": 0.9, "reason": "strong fit"}', "mock:model"),   # -> auto-approved
        ('{"decision": "GO", "match_score": 0.5, "reason": "weak fit"}', "mock:model"),     # -> manual review
        ('{"decision": "NO-GO", "match_score": 0.1, "reason": "not relevant"}', "mock:model"),  # -> never saved
    ])
    monkeypatch.setattr(ai_evaluator, "generate_json", lambda *a, **k: next(responses))

    candidates = [
        {"company": "A", "role": "R1", "job_url": "url1", "job_description": "d"},
        {"company": "B", "role": "R2", "job_url": "url2", "job_description": "d"},
        {"company": "C", "role": "R3", "job_url": "url3", "job_description": "d"},
    ]
    counts = ai_evaluator.run_sourcing_pipeline(candidates)

    assert counts == {"auto_approved": 1, "manual_review": 1, "rejected": 1, "needs_consultation": 0}
    assert len(temp_db.get_jobs()) == 2  # the NO-GO candidate was never saved at all


def test_is_rate_limit_error_detects_signal_regardless_of_source():
    class FakeError(Exception):
        pass

    assert ai_router._is_rate_limit_error(FakeError("429 Too Many Requests")) is True
    assert ai_router._is_rate_limit_error(FakeError("RESOURCE_EXHAUSTED: quota exceeded")) is True
    assert ai_router._is_rate_limit_error(ValueError("some unrelated error")) is False