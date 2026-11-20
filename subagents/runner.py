"""Universal Pure LLM Subagent Runner for FRIDAY, EDITH, and Custom Subagents.

Includes Git Worktree Isolation for parallel autonomous subagents:
- Invariant 5: Subagents are isolated in dedicated git worktrees.
- Single bounded deliverable.
- Merge-or-discard guarantees.
"""

import sys
import os
import uuid
import subprocess
import logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from subagents.pure_llm import query_subagent_llm
from brain.verifier import verify_command

logger = logging.getLogger("harvis.subagents.runner")

WORKTREES_DIR = os.path.join(PROJECT_ROOT, ".harvis", "worktrees")


def spawn_subagent(agent_name: str, task: str = "health", system_prompt: str = None) -> dict:
    """Spawns an independent pure LLM subagent (FRIDAY, EDITH, or custom) to execute tasks."""
    try:
        res = query_subagent_llm(
            agent_name=agent_name,
            task_prompt=task,
            custom_system_prompt=system_prompt
        )
        return res
    except Exception as e:
        logger.error(f"Error spawning pure LLM subagent {agent_name}: {e}")
        return {"success": False, "agent": agent_name.upper(), "error": str(e)}


def spawn_isolated_subagent(agent_name: str, task: str, verification_cmd: str = None) -> dict:
    """Spawns a subagent inside an isolated git worktree with merge-or-discard safety."""
    uid = uuid.uuid4().hex[:6]
    branch_name = f"subagent/{agent_name}_{uid}"
    worktree_path = os.path.join(WORKTREES_DIR, f"{agent_name}_{uid}")
    os.makedirs(WORKTREES_DIR, exist_ok=True)

    # 1. Create Git Worktree
    try:
        create_res = subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, worktree_path, "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True
        )
        if create_res.returncode != 0:
            logger.warning(f"Could not create git worktree ({create_res.stderr}). Falling back to in-place run.")
            return spawn_subagent(agent_name, task)
    except Exception as e:
        logger.warning(f"Git worktree creation exception: {e}. Falling back to in-place run.")
        return spawn_subagent(agent_name, task)

    logger.info(f"Subagent {agent_name} running in isolated worktree: {worktree_path}")

    # 2. Execute Subagent
    try:
        # Prompt subagent with awareness of isolated worktree
        task_with_dir = f"{task}\n(Working directory is isolated at: {worktree_path})"
        result = query_subagent_llm(agent_name=agent_name, task_prompt=task_with_dir)

        # 3. Automated Verification Gate
        passed = result.get("success", False)
        if verification_cmd:
            v_res = verify_command(f"cd {worktree_path} && {verification_cmd}")
            passed = v_res.get("passed", False)
            result["verification"] = v_res

        # 4. Merge or Discard
        if passed:
            logger.info(f"Subagent {agent_name} passed verification. Merging branch {branch_name}...")
            # Commit any changes in worktree
            subprocess.run(["git", "add", "."], cwd=worktree_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"subagent({agent_name}): {task[:60]}"], cwd=worktree_path, capture_output=True)
            # Merge into main branch
            merge_res = subprocess.run(["git", "merge", branch_name, "--no-edit"], cwd=PROJECT_ROOT, capture_output=True, text=True)
            result["merged"] = (merge_res.returncode == 0)
        else:
            logger.warning(f"Subagent {agent_name} failed verification or errored. Discarding worktree.")
            result["merged"] = False

        return result

    finally:
        # Cleanup worktree & branch
        try:
            subprocess.run(["git", "worktree", "remove", "--force", worktree_path], cwd=PROJECT_ROOT, capture_output=True)
            subprocess.run(["git", "branch", "-D", branch_name], cwd=PROJECT_ROOT, capture_output=True)
            logger.info(f"Cleaned up worktree {worktree_path} and branch {branch_name}.")
        except Exception as e:
            logger.error(f"Error cleaning up subagent worktree: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        ag = sys.argv[1]
        t = sys.argv[2] if len(sys.argv) > 2 else "audit system health"
        res = spawn_subagent(ag, t)
        print(res["output"] if res["success"] else f"Error: {res['error']}")
    else:
        print("Usage: python -m subagents.runner <agent_name> <task>")
