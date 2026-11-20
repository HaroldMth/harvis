"""Autonomous Planning Engine for HARVIS.

Generates comprehensive technical plans in:
.harvis/plans/<ai_gen_plan_name>/<PLAN_TYPE>.md

Plan Types:
- BUILD_PLAN: Creating new features, modules, or services from scratch.
- IMPLEMENTATION_PLAN: Integrating into existing systems, modifying code, or extending features.
- REFACTOR_PLAN: Restructuring, cleaning, or optimizing existing codebase.
- FIX_PLAN: Investigating and fixing bugs, errors, or regressions.

Presents an executive summary with interactive Telegram buttons:
[ 📄 Get Full Plan (.md) ]  [ 🚀 Proceed ]  [ 🛑 Cancel ]
"""

import os
import re
import json
import uuid
import logging
from pathlib import Path
from brain.paths import REPO_ROOT, HARVIS_DIR

logger = logging.getLogger("harvis.planner")

PLANS_DIR = REPO_ROOT / ".harvis" / "plans"

PLAN_TYPES = {
    "BUILD_PLAN": "Creating new features, services, or modules from scratch.",
    "IMPLEMENTATION_PLAN": "Integrating into existing systems, modifying code, or adding functionality.",
    "REFACTOR_PLAN": "Restructuring, cleaning, or optimizing existing codebase.",
    "FIX_PLAN": "Investigating and fixing bugs, errors, or regressions."
}

ACTIVE_PLANS = {}  # plan_id -> dict


def ensure_plans_dir(active_dir: str = None) -> Path:
    """Ensure .harvis/plans directory exists."""
    target_dir = PLANS_DIR
    if active_dir:
        p = Path(active_dir) / ".harvis" / "plans"
        try:
            p.mkdir(parents=True, exist_ok=True)
            target_dir = p
        except Exception:
            pass
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def sanitize_slug(name: str) -> str:
    """Produce clean snake_case slug without timestamps or special chars."""
    if not name:
        return "plan"
    clean = re.sub(r"[^a-zA-Z0-9_\-\s]+", "", name)
    clean = re.sub(r"[\s\-]+", "_", clean).strip("_").lower()
    # Strip any leading date or timestamp pattern (e.g. 2026_11_19_ or 20261119_)
    clean = re.sub(r"^(\d{4}_\d{2}_\d{2}_?|\d{8,}_?)", "", clean).strip("_")
    return clean[:50] or "task_plan"


def _build_planner_prompt(task_prompt: str, active_dir: str = None, feedback: str = None, existing_plan: dict = None) -> list:
    """Construct prompt for the LLM to generate the plan."""
    context_note = f"Active Directory / Project Root: {active_dir or REPO_ROOT}\n"

    feedback_section = ""
    if feedback and existing_plan:
        feedback_section = (
            f"\n\nUSER FEEDBACK ON PREVIOUS DRAFT:\n"
            f"The user gave the following feedback/revisions on the previous draft ({existing_plan.get('title', 'Draft')}):\n"
            f"\"{feedback}\"\n"
            f"You MUST incorporate this feedback into the revised plan.\n"
        )

    sys_prompt = (
        "You are HARVIS's Principal Systems Architect. "
        "Your role is to author a complete, rigorous, production-grade technical plan before any code or shell execution starts.\n\n"
        "You must determine the most suitable PLAN TYPE from:\n"
        "- BUILD_PLAN (new feature / new module / from scratch)\n"
        "- IMPLEMENTATION_PLAN (modifying existing code / integrating features)\n"
        "- REFACTOR_PLAN (restructuring, cleanup, architecture improvements)\n"
        "- FIX_PLAN (bug fixing, regression tracing, investigation)\n\n"
        "You MUST respond ONLY with a valid JSON object matching this schema:\n"
        "{\n"
        '  "title": "Short descriptive title (3-6 words)",\n'
        '  "slug": "clean_snake_case_folder_slug_without_timestamps",\n'
        '  "plan_type": "BUILD_PLAN | IMPLEMENTATION_PLAN | REFACTOR_PLAN | FIX_PLAN",\n'
        '  "summary": "Executive summary for Telegram. Include 🎯 Objective, 🏗️ Proposed Architecture & Files, ⚡ Key Phases, and ⚠️ Key Risks",\n'
        '  "checklist": ["Step 1...", "Step 2...", "Step 3..."],\n'
        '  "full_markdown": "# Full markdown document covering Context, Goals & Non-goals, File Manifest, Detailed Checklist (- [ ]), Verification, and Rollback"\n'
        "}\n\n"
        "CRITICAL RULES:\n"
        "1. 'slug' MUST NOT contain dates, timestamps, or symbols. Keep it descriptive (e.g. 'voice_memo_pipeline', 'repo_cleanup_opensource').\n"
        "2. 'checklist' must be concise actionable steps.\n"
        "3. 'full_markdown' must be thorough, production-ready, and contain the complete checklist with '- [ ] Step ...'.\n"
        "4. Output pure JSON without markdown codeblock wrappers if possible, or standard ```json ... ```."
    )

    user_msg = (
        f"{context_note}"
        f"TASK / GOAL:\n{task_prompt}\n"
        f"{feedback_section}"
    )

    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_msg}
    ]


def generate_plan(task_prompt: str, active_dir: str = None, feedback: str = None, existing_plan_id: str = None) -> dict:
    """Generate a complete plan via LLM and write to .harvis/plans/<slug>/<PLAN_TYPE>.md."""
    from brain.router import complete

    existing = ACTIVE_PLANS.get(existing_plan_id) if existing_plan_id else None
    messages = _build_planner_prompt(task_prompt, active_dir=active_dir, feedback=feedback, existing_plan=existing)

    logger.info(f"Generating plan for: {task_prompt[:80]}...")
    res = complete(messages, mode="normal")
    content = (res.get("content") or "").strip()

    # Parse JSON output
    data = None
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    raw_json = json_match.group(1) if json_match else content

    try:
        data = json.loads(raw_json)
    except Exception:
        # Fallback: scan for first { to last }
        start = raw_json.find("{")
        end = raw_json.rfind("}")
        if start != -1 and end != -1:
            try:
                data = json.loads(raw_json[start:end+1])
            except Exception as e:
                logger.warning(f"Failed to parse plan JSON: {e}")

    if not data or not isinstance(data, dict):
        # Fallback parser if LLM failed JSON
        slug = sanitize_slug(task_prompt[:30])
        plan_type = "IMPLEMENTATION_PLAN"
        title = task_prompt[:50]
        summary = f"🎯 <b>Objective:</b> {task_prompt}\n\n⚡ <b>Approach:</b> Autonomous multi-step plan."
        full_md = f"# {plan_type}: {title}\n\n## 1. Goal\n{task_prompt}\n\n## 2. Checklist\n- [ ] 1. Analyze and prepare codebase\n- [ ] 2. Implement changes\n- [ ] 3. Verify and test"
        checklist = ["Analyze and prepare codebase", "Implement changes", "Verify and test"]
    else:
        title = data.get("title") or task_prompt[:50]
        slug = sanitize_slug(data.get("slug") or title)
        plan_type = data.get("plan_type", "IMPLEMENTATION_PLAN")
        if plan_type not in PLAN_TYPES:
            plan_type = "IMPLEMENTATION_PLAN"
        summary = data.get("summary") or f"🎯 <b>Objective:</b> {title}"
        full_md = data.get("full_markdown") or f"# {plan_type}: {title}\n\n{task_prompt}"
        checklist = data.get("checklist") or re.findall(r"^\s*[-*+]\s+\[ \]\s+(.+)$", full_md, re.M)

    base_plans_dir = ensure_plans_dir(active_dir)
    plan_dir = base_plans_dir / slug
    plan_dir.mkdir(parents=True, exist_ok=True)

    plan_file = plan_dir / f"{plan_type}.md"
    plan_file.write_text(full_md, encoding="utf-8")

    plan_id = existing_plan_id or f"plan_{uuid.uuid4().hex[:8]}"

    meta = {
        "id": plan_id,
        "slug": slug,
        "plan_type": plan_type,
        "title": title,
        "plan_dir": str(plan_dir),
        "plan_file": str(plan_file),
        "status": "pending_approval",
        "task_prompt": task_prompt,
        "summary": summary,
        "checklist": checklist,
        "full_markdown": full_md
    }

    meta_file = plan_dir / "meta.json"
    meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    ACTIVE_PLANS[plan_id] = meta
    return meta


def get_plan(plan_id: str) -> dict | None:
    """Retrieve plan by ID or slug."""
    if plan_id in ACTIVE_PLANS:
        return ACTIVE_PLANS[plan_id]

    # Look in PLANS_DIR
    if PLANS_DIR.exists():
        for d in PLANS_DIR.iterdir():
            if d.is_dir():
                meta_file = d / "meta.json"
                if meta_file.exists():
                    try:
                        m = json.loads(meta_file.read_text(encoding="utf-8"))
                        if m.get("id") == plan_id or m.get("slug") == plan_id:
                            ACTIVE_PLANS[m["id"]] = m
                            return m
                    except Exception:
                        pass
    return None


def update_plan_status(plan_id: str, status: str) -> bool:
    """Update plan status (approved, cancelled, in_progress, completed)."""
    plan = get_plan(plan_id)
    if not plan:
        return False
    plan["status"] = status
    ACTIVE_PLANS[plan_id] = plan

    meta_file = Path(plan["plan_dir"]) / "meta.json"
    if meta_file.exists():
        try:
            meta_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        except Exception:
            pass
    return True


def format_telegram_summary(plan: dict) -> str:
    """Render executive summary for Telegram card."""
    title = plan.get("title", "Technical Plan")
    plan_type = plan.get("plan_type", "PLAN")
    slug = plan.get("slug", "plan")
    summary = plan.get("summary", "")

    # Clean markdown formatting in summary for Telegram HTML if needed
    summary_clean = re.sub(r"^###?\s+(.+)$", r"<b>\1</b>", summary, flags=re.M)
    summary_clean = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", summary_clean)

    header = f"📋 <b>{plan_type}: {title}</b>\n📁 <code>.harvis/plans/{slug}/{plan_type}.md</code>\n"
    footer = "\n\n<i>Review the plan above or tap below to download the full specification. Tap <b>Proceed</b> to execute.</i>"

    return f"{header}\n{summary_clean}{footer}"
