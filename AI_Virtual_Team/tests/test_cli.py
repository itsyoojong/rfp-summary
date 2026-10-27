import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("llm_mode", ["groq", "codex"])
def test_demo_twice_in_fresh_processes_is_offline_and_persistent(tmp_path, llm_mode):
    db = tmp_path / "demo.sqlite3"
    env = dict(os.environ, LLM_MODE=llm_mode, GROQ_API_KEY="not-a-real-key", CODEX_BINARY="must-not-start",
               GITHUB_ENABLED="true", NOTION_TOKEN="not-a-real-key")
    command = [sys.executable, "-m", "ai_virtual_team", "demo", "--db", str(db)]
    first = subprocess.run(command, capture_output=True, text=True, env=env, timeout=10)
    second = subprocess.run(command, capture_output=True, text=True, env=env, timeout=10)
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    data = json.loads(second.stdout)
    assert data["mode"] == "mock"
    assert data["external_calls"] == 0
    assert data["messages"] == 5
    assert len(data["tasks"]) == 3
    assert data["same_day_duplicate_prevented"] is True
    assert data["restart_persistence_verified"] is True
    assert "not-a-real-key" not in first.stdout + second.stdout + first.stderr + second.stderr


def test_missing_groq_key_is_safe_cli_error(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in {"GROQ_API_KEY", "NOTION_TOKEN"}}
    env["LLM_MODE"] = "groq"
    result = subprocess.run([sys.executable, "-m", "ai_virtual_team", "projects", "--db", str(tmp_path / "x.db")],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert "ValidationError" in result.stderr


def test_runtime_lock_rejects_second_process(tmp_path):
    from ai_virtual_team.runtime import runtime_lock

    db = tmp_path / "locked.sqlite3"
    with runtime_lock(db):
        result = subprocess.run([sys.executable, "-m", "ai_virtual_team", "demo", "--db", str(db)],
                                capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert "RuntimeError" in result.stderr


def test_demo_refuses_real_project_database_without_modifying_it(tmp_path):
    from ai_virtual_team.database import Store
    from ai_virtual_team.models import Meeting, Project

    db = tmp_path / "real.sqlite3"
    store = Store(db)
    project = Project(name="Real project", goal="keep this", created_by=123, guild_id=456, channel_id=789)
    meeting = Meeting(project_id=project.id, topic="Running", requested_by=123, base_plan_version=0)
    with store.transaction():
        store.put("project", project)
        store.put("meeting", meeting)
    result = subprocess.run([sys.executable, "-m", "ai_virtual_team", "demo", "--db", str(db)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert store.get("meeting", meeting.id).status == "Running"
    assert len(store.list("project")) == 1
    store.close()
