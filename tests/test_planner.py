import os
import shutil
import pytest
from pathlib import Path
from unittest.mock import patch

from brain import planner
from brain.paths import REPO_ROOT


def test_sanitize_slug():
    assert planner.sanitize_slug("Add Voice Memo Pipeline!") == "add_voice_memo_pipeline"
    assert planner.sanitize_slug("2026-11-19_Refactor-Database") == "refactor_database"
    assert planner.sanitize_slug("20261119_build_system") == "build_system"
    assert planner.sanitize_slug("Clean   Repo & Open Source") == "clean_repo_open_source"
    assert planner.sanitize_slug("") == "plan"


def test_format_telegram_summary():
    plan = {
        "title": "Voice Pipeline",
        "plan_type": "BUILD_PLAN",
        "slug": "voice_pipeline",
        "summary": "🎯 **Objective:** Add voice support.\n⚡ **Phases:** 1. STT 2. TTS"
    }
    summary = planner.format_telegram_summary(plan)
    assert "BUILD_PLAN: Voice Pipeline" in summary
    assert ".harvis/plans/voice_pipeline/BUILD_PLAN.md" in summary
    assert "<b>Objective:</b>" in summary
    assert "Proceed" in summary


def test_generate_and_retrieve_plan(tmp_path):
    fake_plans_dir = tmp_path / ".harvis" / "plans"
    fake_plans_dir.mkdir(parents=True, exist_ok=True)

    fake_response = {
        "title": "Add Audio Bridge",
        "slug": "add_audio_bridge",
        "plan_type": "BUILD_PLAN",
        "summary": "🎯 Objective: Add audio bridge.",
        "checklist": ["Create bridge", "Test bridge"],
        "full_markdown": "# BUILD_PLAN: Add Audio Bridge\n\n- [ ] Step 1: Create bridge\n- [ ] Step 2: Test bridge"
    }

    with patch.object(planner, "PLANS_DIR", fake_plans_dir):
        with patch("brain.router.complete", return_value={"content": str(fake_response).replace("'", '"')}):
            plan = planner.generate_plan("build an audio bridge", active_dir=str(tmp_path))

            assert plan["slug"] == "add_audio_bridge"
            assert plan["plan_type"] == "BUILD_PLAN"
            assert os.path.exists(plan["plan_file"])
            assert os.path.exists(plan["plan_dir"])

            retrieved = planner.get_plan(plan["id"])
            assert retrieved is not None
            assert retrieved["title"] == "Add Audio Bridge"

            # Update status
            planner.update_plan_status(plan["id"], "approved")
            assert planner.get_plan(plan["id"])["status"] == "approved"
