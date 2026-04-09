from pathlib import Path

from desktop_automation_agent.idempotency_guard import IdempotencyGuard


def test_idempotency_guard_executes_action_once_and_caches_result(tmp_path):
    guard = IdempotencyGuard(storage_path=str(Path(tmp_path) / "idempotency.json"))
    calls = {"count": 0}

    def action():
        calls["count"] += 1
        return {"value": "done", "count": calls["count"]}

    first = guard.run_once(action_id="step-1", action=action)
    second = guard.run_once(action_id="step-1", action=action)

    assert first.executed is True
    assert first.cached is False
    assert second.executed is False
    assert second.cached is True
    assert second.result_payload == {"value": "done", "count": 1}
    assert calls["count"] == 1


def test_idempotency_guard_restores_cached_result_in_resumed_session(tmp_path):
    storage = Path(tmp_path) / "idempotency.json"
    guard = IdempotencyGuard(storage_path=str(storage))
    guard.run_once(action_id="step-2", action=lambda: {"status": "ok"})

    resumed = IdempotencyGuard(storage_path=str(storage))
    result = resumed.run_once(action_id="step-2", action=lambda: {"status": "new"})

    assert result.executed is False
    assert result.cached is True
    assert result.result_payload == {"status": "ok"}


def test_idempotency_guard_resets_specific_action_for_deliberate_rerun(tmp_path):
    storage = Path(tmp_path) / "idempotency.json"
    guard = IdempotencyGuard(storage_path=str(storage))
    counter = {"count": 0}

    def action():
        counter["count"] += 1
        return {"run": counter["count"]}

    guard.run_once(action_id="step-3", action=action)
    changed = guard.reset_action("step-3")
    rerun = guard.run_once(action_id="step-3", action=action)

    assert changed is True
    assert rerun.executed is True
    assert rerun.result_payload == {"run": 2}


def test_idempotency_guard_can_reset_all_completed_actions(tmp_path):
    storage = Path(tmp_path) / "idempotency.json"
    guard = IdempotencyGuard(storage_path=str(storage))
    guard.run_once(action_id="a", action=lambda: {"ok": True})
    guard.run_once(action_id="b", action=lambda: {"ok": True})

    guard.reset_all()

    assert guard.get_completed_action("a") is None
    assert guard.get_completed_action("b") is None
