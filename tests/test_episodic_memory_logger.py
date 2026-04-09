from pathlib import Path

from desktop_automation_perception.episodic_memory_logger import EpisodicMemoryLogger


def test_episodic_memory_logger_records_episode(tmp_path):
    logger = EpisodicMemoryLogger(storage_path=str(Path(tmp_path) / "episodes.json"))

    result = logger.log_episode(
        task_description="Submit a weekly operations report in ChatGPT.",
        task_type="report_submission",
        applications=["ChatGPT"],
        steps_executed=[
            {"step_name": "open-chat", "application_name": "ChatGPT"},
            {"step_name": "submit-prompt", "application_name": "ChatGPT"},
        ],
        outcomes=["report drafted", "report submitted"],
        errors_encountered=[],
        recovery_actions_taken=[],
        total_duration_seconds=12.3,
        succeeded=True,
    )

    assert result.succeeded is True
    assert result.episode is not None
    assert result.episode.task_type == "report_submission"
    assert result.episode.succeeded is True


def test_episodic_memory_logger_indexes_by_task_application_and_outcome(tmp_path):
    logger = EpisodicMemoryLogger(storage_path=str(Path(tmp_path) / "episodes.json"))
    logger.log_episode(
        task_description="Draft invoice reply in Outlook.",
        task_type="email_reply",
        applications=["Outlook"],
        steps_executed=[{"step_name": "compose"}],
        outcomes=["drafted"],
        total_duration_seconds=5.0,
        succeeded=True,
    )
    logger.log_episode(
        task_description="Retry invoice reply in Outlook.",
        task_type="email_reply",
        applications=["Outlook"],
        steps_executed=[{"step_name": "compose"}],
        outcomes=["failed"],
        errors_encountered=["timeout"],
        recovery_actions_taken=["refresh"],
        total_duration_seconds=7.0,
        succeeded=False,
    )

    result = logger.get_episodes_by_index(
        task_type="email_reply",
        application="Outlook",
        succeeded=False,
    )

    assert result.succeeded is True
    assert len(result.episodes) == 1
    assert result.episodes[0].errors_encountered == ["timeout"]


def test_episodic_memory_logger_retrieves_relevant_episodes_by_similarity(tmp_path):
    logger = EpisodicMemoryLogger(storage_path=str(Path(tmp_path) / "episodes.json"))
    logger.log_episode(
        task_description="Process vendor invoice emails and extract totals.",
        task_type="invoice_processing",
        applications=["Outlook", "Excel"],
        steps_executed=[{"step_name": "read-invoice-email"}, {"step_name": "extract-total"}],
        outcomes=["invoice processed"],
        total_duration_seconds=11.0,
        succeeded=True,
    )
    logger.log_episode(
        task_description="Schedule a team meeting in Outlook calendar.",
        task_type="calendar_scheduling",
        applications=["Outlook"],
        steps_executed=[{"step_name": "open-calendar"}],
        outcomes=["meeting scheduled"],
        total_duration_seconds=4.0,
        succeeded=True,
    )

    result = logger.retrieve_relevant_episodes(
        "Handle a new invoice from email and capture the amount.",
    )

    assert result.succeeded is True
    assert result.matches
    assert result.matches[0].episode.task_type == "invoice_processing"


def test_episodic_memory_logger_prefers_more_recent_similar_episode(tmp_path):
    logger = EpisodicMemoryLogger(storage_path=str(Path(tmp_path) / "episodes.json"))
    first = logger.log_episode(
        task_description="Generate status report from dashboard.",
        task_type="status_report",
        applications=["Dashboard"],
        steps_executed=[{"step_name": "open-dashboard"}],
        outcomes=["report created"],
        total_duration_seconds=6.0,
        succeeded=True,
    ).episode
    second = logger.log_episode(
        task_description="Generate status report from dashboard.",
        task_type="status_report",
        applications=["Dashboard"],
        steps_executed=[{"step_name": "open-dashboard"}, {"step_name": "export-report"}],
        outcomes=["report created"],
        total_duration_seconds=5.5,
        succeeded=True,
    ).episode

    result = logger.retrieve_relevant_episodes("Generate a dashboard status report.", task_type="status_report")

    assert result.matches[0].episode.episode_id == second.episode_id
    assert result.matches[1].episode.episode_id == first.episode_id


def test_episodic_memory_logger_persists_and_lists_episodes(tmp_path):
    logger = EpisodicMemoryLogger(storage_path=str(Path(tmp_path) / "episodes.json"))
    logger.log_episode(
        task_description="Fix failed login and retry.",
        task_type="login_recovery",
        applications=["ChatGPT"],
        steps_executed=[{"step_name": "login"}],
        outcomes=["retry succeeded"],
        errors_encountered=["session expired"],
        recovery_actions_taken=["re-authenticate"],
        total_duration_seconds=9.0,
        succeeded=True,
    )

    episodes = logger.list_episodes()

    assert len(episodes) == 1
    assert episodes[0].recovery_actions_taken == ["re-authenticate"]
