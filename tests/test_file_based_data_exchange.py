import json
from pathlib import Path

from desktop_automation_perception.allowlist_enforcer import ActionAllowlistEnforcer
from desktop_automation_perception.automation import FileBasedDataExchange
from desktop_automation_perception.models import (
    DataExportFileFormat,
    ScreenVerificationResult,
)


class FakeStateVerifier:
    def __init__(self, *, should_pass: bool = True):
        self.should_pass = should_pass
        self.calls = []

    def verify(self, checks):
        self.calls.append(list(checks))
        if self.should_pass:
            return ScreenVerificationResult(passed_checks=[], failed_checks=[])
        return ScreenVerificationResult(
            passed_checks=[],
            failed_checks=[type("FailedCheck", (), {"detail": "Import banner did not appear."})()],
        )


def test_file_based_data_exchange_writes_json_triggers_import_and_cleans_up(tmp_path):
    verifier = FakeStateVerifier()
    imported = {}

    def importer(file_path: str):
        path = Path(file_path)
        imported["path"] = file_path
        imported["content"] = path.read_text(encoding="utf-8")
        return {"imported": True, "path": file_path}

    exchange = FileBasedDataExchange(
        shared_directory=str(tmp_path),
        state_verifier=verifier,
        import_trigger=importer,
    )

    result = exchange.transfer(
        {"account": "acct-a", "amount": 12},
        file_format=DataExportFileFormat.JSON,
        verification_checks=[],
    )

    assert result.succeeded is True
    assert result.cleaned_up is True
    assert result.file_format is DataExportFileFormat.JSON
    assert json.loads(imported["content"]) == {"account": "acct-a", "amount": 12}
    assert not Path(imported["path"]).exists()


def test_file_based_data_exchange_supports_csv_rows_and_encoding_selection(tmp_path):
    verifier = FakeStateVerifier()
    imported = {}

    def importer(file_path: str):
        path = Path(file_path)
        imported["path"] = file_path
        imported["raw_bytes"] = path.read_bytes()
        imported["decoded"] = path.read_text(encoding="utf-16")
        return {"ok": True}

    exchange = FileBasedDataExchange(
        shared_directory=str(tmp_path),
        state_verifier=verifier,
        import_trigger=importer,
    )

    result = exchange.transfer(
        [{"name": "Ana", "city": "Boston"}, {"name": "Luis", "city": "Miami"}],
        file_format=DataExportFileFormat.CSV,
        verification_checks=[],
        encoding="utf-16",
    )

    assert result.succeeded is True
    assert result.encoding == "utf-16"
    assert "name,city" in imported["decoded"]
    assert "Ana,Boston" in imported["decoded"]
    assert imported["raw_bytes"].startswith(b"\xff\xfe")


def test_file_based_data_exchange_supports_xml_payloads(tmp_path):
    verifier = FakeStateVerifier()
    imported = {}

    def importer(file_path: str):
        imported["content"] = Path(file_path).read_text(encoding="utf-8")
        return {"ok": True}

    exchange = FileBasedDataExchange(
        shared_directory=str(tmp_path),
        state_verifier=verifier,
        import_trigger=importer,
    )

    result = exchange.transfer(
        {"customer": "acct-a", "tags": ["new", "priority"]},
        file_format=DataExportFileFormat.XML,
        verification_checks=[],
    )

    assert result.succeeded is True
    assert "<customer>acct-a</customer>" in imported["content"]
    assert "<tags><item>new</item><item>priority</item></tags>" in imported["content"]


def test_file_based_data_exchange_reports_failed_ui_verification(tmp_path):
    verifier = FakeStateVerifier(should_pass=False)

    exchange = FileBasedDataExchange(
        shared_directory=str(tmp_path),
        state_verifier=verifier,
        import_trigger=lambda file_path: {"ok": True},
    )

    result = exchange.transfer(
        {"status": "pending"},
        file_format=DataExportFileFormat.JSON,
        verification_checks=[],
    )

    assert result.succeeded is False
    assert result.cleaned_up is True
    assert result.reason == "Target application did not reach the expected post-import UI state."


def test_file_based_data_exchange_blocks_disallowed_file_path(tmp_path):
    verifier = FakeStateVerifier()
    allowlist_path = Path(tmp_path) / "allowlist.json"
    allowlist_path.write_text(
        json.dumps(
            {
                "action_types": ["file_transfer"],
                "applications": ["*"],
                "urls": ["https://safe.example/*"],
                "file_paths": [str(Path(tmp_path) / "approved" / "*")],
            }
        ),
        encoding="utf-8",
    )

    exchange = FileBasedDataExchange(
        shared_directory=str(tmp_path),
        state_verifier=verifier,
        import_trigger=lambda file_path: {"ok": True},
        allowlist_enforcer=ActionAllowlistEnforcer(config_path=str(allowlist_path)),
    )

    result = exchange.transfer(
        {"status": "pending"},
        file_format=DataExportFileFormat.JSON,
        verification_checks=[],
        workflow_id="wf-file",
    )

    assert result.succeeded is False
    assert "allowlist" in (result.reason or "")
