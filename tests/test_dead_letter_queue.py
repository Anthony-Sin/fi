from pathlib import Path

from desktop_automation_perception.dead_letter_queue import DeadLetterQueueHandler
from desktop_automation_perception.models import (
    RetryAttemptLog,
    RetryDisposition,
    RetryFailureResult,
)


def make_failure(message="failed"):
    return RetryFailureResult(
        attempts=[
            RetryAttemptLog(
                attempt_number=1,
                delay_seconds=0.5,
                exception_type="RuntimeError",
                exception_message=message,
                disposition=RetryDisposition.RETRY,
            )
        ],
        final_exception_type="RuntimeError",
        final_exception_message=message,
        reason="Retry attempts exhausted.",
    )


def test_dead_letter_queue_enqueues_and_inspects_items(tmp_path):
    handler = DeadLetterQueueHandler(storage_path=str(Path(tmp_path) / "dlq_enqueues.json"))

    enqueue = handler.enqueue(
        action_type="submit",
        inputs={"target": "chat"},
        retry_failure=make_failure(),
        item_id="dlq-1",
    )
    inspected = handler.inspect()

    assert enqueue.succeeded is True
    assert inspected.succeeded is True
    assert len(inspected.items) == 1
    assert inspected.items[0].item_id == "dlq-1"


def test_dead_letter_queue_sends_alert_when_threshold_exceeded(tmp_path):
    alerts = []
    handler = DeadLetterQueueHandler(
        storage_path=str(Path(tmp_path) / "dlq_alerts.json"),
        alert_threshold=1,
        alert_callback=alerts.append,
    )

    handler.enqueue(action_type="a", inputs={}, retry_failure=make_failure("one"))
    handler.enqueue(action_type="b", inputs={}, retry_failure=make_failure("two"))
    handler.enqueue(action_type="c", inputs={}, retry_failure=make_failure("three"))

    assert alerts == [2, 3]


def test_dead_letter_queue_retries_individual_item_and_removes_it(tmp_path):
    handler = DeadLetterQueueHandler(storage_path=str(Path(tmp_path) / "dlq_retry_one.json"))
    handler.enqueue(
        action_type="submit",
        inputs={"value": 1},
        retry_failure=make_failure(),
        item_id="dlq-2",
    )

    retried = handler.retry_item("dlq-2", lambda item: {"ok": item.inputs["value"]})
    inspected = handler.inspect()

    assert retried.succeeded is True
    assert inspected.items == []


def test_dead_letter_queue_bulk_reprocess_keeps_remaining_failures(tmp_path):
    handler = DeadLetterQueueHandler(storage_path=str(Path(tmp_path) / "dlq_bulk.json"))
    handler.enqueue(action_type="a", inputs={"id": 1}, retry_failure=make_failure("one"), item_id="1")
    handler.enqueue(action_type="b", inputs={"id": 2}, retry_failure=make_failure("two"), item_id="2")

    def retry_action(item):
        if item.item_id == "2":
            raise RuntimeError("still broken")
        return {"ok": True}

    result = handler.bulk_reprocess(retry_action)
    inspected = handler.inspect()

    assert result.succeeded is True
    assert [item.item_id for item in result.items] == ["1"]
    assert [item.item_id for item in inspected.items] == ["2"]


def test_dead_letter_queue_exports_report(tmp_path):
    storage = Path(tmp_path) / "dlq_export.json"
    report = Path(tmp_path) / "report.json"
    handler = DeadLetterQueueHandler(storage_path=str(storage))
    handler.enqueue(action_type="submit", inputs={"x": 1}, retry_failure=make_failure(), item_id="dlq-3")

    exported = handler.export_report(str(report))

    assert exported.succeeded is True
    assert exported.report_path == str(report)
    assert report.exists()
    assert '"item_id": "dlq-3"' in report.read_text(encoding="utf-8")
