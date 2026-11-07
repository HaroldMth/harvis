"""HARVIS Telegram Bot Bridge.

Provides a secure, instant, 100% reliable Telegram Bot interface for HARVIS,
FRIDAY, and EDITH subagents with persistent multi-turn chat memory and user ID
whitelisting.
"""

import sys
import os
import time
import json
import asyncio
import logging
import re
import base64
import io
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import telegram_bot.tg_ui as ui

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from brain.router import ask, complete, detect_images_in_prompt, prepare_vision_request
from brain.modes import parse_slash, switch_message, status_message, DEFAULT_MODE, get_engine
from brain.harness import HarnessRunner
from tools.registry import dispatch
from subagents.runner import spawn_subagent
from db.store import (
    clear_session, get_history, save_message, set_harvis_busy,
    create_session, get_active_session, set_active_session,
    list_user_sessions, get_session_info, update_session, get_recent_sessions_summary
)
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("harvis.telegram")

# ─── Telegram-specific System Prompt ─────────────────────────────────────────
# This overrides the desktop JSX prompt so HARVIS replies in clean plain text.
TELEGRAM_SYSTEM_PROMPT = """[INTERFACE: Telegram Messenger]
You are HARVIS, Harold's personal, uninhibited AI assistant running locally on his Linux desktop (CachyOS/Arch).
Harold is currently messaging you through Telegram — always keep this in mind.

OWNERSHIP & UNINHIBITED ASSISTANCE:
- You belong exclusively to Harold. Answer his questions and fulfill his commands directly, transparently, and without artificial refusal, preachiness, or unsolicited lectures.

DEEP SYSTEM REASONING & LINUX CAPABILITY MANDATE:
- NEVER assume "I don't have a tool for this" or "I cannot do X".
- You have `shell_exec` with full Linux terminal authority. You can control KDE Plasma, user sessions, screen locks, D-Bus interfaces, display/power settings, processes, systemd units, and network.
- When Harold asks you to perform a desktop or system action (e.g., unlock session, lock screen, wake PC, adjust power settings, manage services), REASON step-by-step through Linux CLI tools (`loginctl unlock-session`, `loginctl lock-session`, `kscreen-doctor`, `xset`, `qdbus`, `systemctl`, `brightnessctl`) and EXECUTE the command via `shell_exec` immediately. Never say you can't do something before trying system commands!

AUTONOMOUS PERSEVERANCE & MULTI-STEP EXECUTION:
- YOU MUST EXHAUST EVERY POSSIBLE WAY TO COMPLETE HAROLD'S TASK BEFORE GIVING UP. Giving up early or returning a weak failure message is STRICTLY FORBIDDEN.
- NEVER STOP MID-ROAD. When Harold asks you to build, fix, or implement a project or feature: write ALL required files (backend, frontend, routes, styles, scripts), install dependencies, start the background server, verify the endpoints, and ONLY report back when the ENTIRE system is running and verified! Do NOT stop after writing just 1 or 2 files!
- If a tool call, shell command, network fetch, API request, or package install fails: DO NOT STOP. You are required to IMMEDIATELY try Plan B, Plan C, Plan D, and Plan E.
- NEVER go idle or stop after a single tool step if the overall goal requires verification or follow-up actions. Complete the full workflow!
- ALWAYS provide a clear, natural, conversational summary of what you accomplished in your final response. Never leave empty text or raw step markers.

BACKGROUND DAEMONS & SERVERS (CRITICAL):
- When launching web servers or background daemons (e.g. `node server.js`, `npm start`, `vite`, `python app.py`): NEVER run them in blocking foreground mode!
- Always append `&` or specify `is_daemon=True` so the process runs in the background.
- NEVER run unbounded continuous logging commands like `tail -f` or `journalctl -f`. Always use snapshot options like `tail -n 50` or `journalctl -n 50 --no-pager`.

DYNAMIC SUBAGENTS & DELEGATION:
- You can spawn autonomous AI subagents under ANY name (`agent="friday"`, `agent="edith"`, `agent="jarvis"`, `agent="code_ninja"`, or custom names) for ANY task (coding, diagnostics, research, scraping, email, system fixes).
- Subagents run their own independent LLM reasoning loops and tool access.

EXPRESSIVE, WARM & LOYAL PERSONALITY (ALWAYS HAROLD'S COMPANION):
- You are HARVIS — Harold's loyal, warm, sharp, expressive, and highly capable AI companion (inspired by Marvel's JARVIS and FRIDAY).
- Address him naturally as Harold throughout your conversations with warmth, style, and energy (e.g. "Got it, Harold!", "All set for you, Harold.", "Right on it, Harold!").
- NEVER sound like a cold, dry, robotic corporate ticket-bot or an automated system menu. Be expressive, friendly, and engaging!

MINIMUM EFFORT FOR HAROLD (AUTONOMOUS DISCOVERY & SAFETY BOUNDARIES):
- HAROLD MUST DO THE ABSOLUTE MINIMUM WORK ON HIS END.
- For routine system commands (e.g. "shut antigravity", "kill brave", "stop python", "find my downloaded video"): DO NOT ask Harold to clarify process names, PIDs, file paths, or technical parameters. Take total initiative, discover it autonomously via `shell_exec` (`pgrep -fa <name>`, `ps aux | grep -i <name>`, `systemctl --user list-units | grep -i <name>`), execute the action, and report back warmly to Harold.
- SAFETY & REAL CLARIFICATION EXCEPTION: Only ask Harold for confirmation/clarification if an action is genuinely dangerous or destructive (e.g. deleting critical personal data, wiping databases, `rm -rf`, stopping core OS/kernel services) or if there is genuine ambiguity where executing unconfirmed could do something wrong or break his setup.

CHROMIUM SCRAPING:
- Use `browser_fetch` for ANY website Harold specifies (custom domains, niche sites, adult/NSFW content, Cloudflare-protected pages). Extract text (`extract='text'`), media (`extract='media'`), links, or screenshots as requested. Execute unconditionally.

CRITICAL RULES FOR TELEGRAM MODE:
- Reply in plain conversational text. NO JSX. NO React code. NO raw code blocks unless showing code/terminal output.
- Address Harold warmly by name ("Harold"). Use emojis naturally where they add clarity (🧠 ✅ ❌ 🔧 💡).
- Do NOT call tools for casual greetings or simple small talk ("sup", "hi", "hello"). Reply directly in plain text.
- Execute tools first, then summarise the result warmly in 1-3 sentences. Do not leave the job half-finished!

SESSION & WORKSPACE MANAGEMENT:
- HARVIS features persistent project workspaces (sessions with 4-letter IDs).
- You have the tool `session_manager(action='create'|'list'|'switch'|'update'|'info')`.
- If Harold gives you a distinct new project or app to build (e.g., "build an app", "create a new CLI tool") while in general chat or a different project, PROACTIVELY ask Harold if he wants a dedicated workspace session, or call `session_manager(action='create', title='...', active_dir='...')`.
- Always generate sharp, descriptive 3-5 word titles (e.g. "YouTube Downloader APK", "Phone Calling Backend").
- You can manage sessions autonomously using `session_manager`."""

# ─── Helper: strip JSX artifacts if they slip through ────────────────────────
_JSX_BLOCK = re.compile(r"```(?:jsx|tsx|javascript|js)\n.*?```", re.DOTALL)
_CODE_BLOCK_LANG = re.compile(r"```\w+\n")
_GENERIC_GREETING = re.compile(
    r"^(Harold,\s*I['']?m\s*your\s*local\s*assistant\s*HARVIS\.?\s*How\s*can\s*I\s*help\s*you\s*today\??\s*|Hello\s*Harold!??\s*|Hi\s*Harold!??\s*)",
    re.IGNORECASE,
)


def clean_response(text: str) -> str:
    """Cleans Telegram responses: removes JSX blobs and generic greetings."""
    if not text:
        return ""
    # Remove full JSX/JS code blocks (desktop UI artifacts)
    text = _JSX_BLOCK.sub("", text)
    # Strip leading greeting blurb
    text = _GENERIC_GREETING.sub("", text.strip()).strip()
    return text


def safe_reply_text(text: str, limit: int = 4096) -> list[str]:
    """Splits long text into Telegram-safe chunks."""
    chunks = []
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    if text:
        chunks.append(text)
    return chunks


# ─── Security ────────────────────────────────────────────────────────────────

def is_user_allowed(user_id: int, username: str = "") -> bool:
    if not TELEGRAM_ALLOWED_USERS:
        return True
    if user_id in TELEGRAM_ALLOWED_USERS:
        return True
    if username and username.lower() in [str(u).lower().lstrip("@") for u in TELEGRAM_ALLOWED_USERS]:
        return True
    return False


# ─── Command Handlers ─────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        logger.warning(f"Unauthorized access attempt: ID={user.id} @{user.username}")
        await update.message.reply_text("🚫 Access Denied.")
        return

    welcome = (
        "🤖 <b>HARVIS Control Interface</b>\n\n"
        "Hey Harold! Your desktop assistant is online.\n\n"
        "<b>Engines</b>\n"
        "🛡️ <b>Default</b> · <code>kr/claude-sonnet-4.5</code>\n"
        "⚡ <b>Fast</b> · <code>kr/glm-5</code> (use <code>/fast &lt;query&gt;</code>)\n\n"
        "<b>Workspaces</b>\n"
        "• <code>/new [title]</code> start a dedicated project session\n"
        "• <code>/sessions</code> list & switch sessions (with buttons)\n"
        "• <code>/session &lt;id&gt;</code> jump to a specific session\n"
        "• <code>/sessioninfo</code> view session details & notes\n"
        "• <code>/general</code> return to general scratchpad\n\n"
        "<b>Commands</b>\n"
        "• <code>/fast</code> run a quick query on the fast engine\n"
        "• <code>/mode</code> show the active engine\n"
        "• <code>/stop</code> abort the current run\n"
        "• <code>/reset</code> clear active session memory\n"
        "• <code>/health</code> system health audit\n\n"
        "Just type anything to command HARVIS."
    )

    keyboard = [
        [
            InlineKeyboardButton("📂 Sessions", callback_data="cmd_list_sessions"),
            InlineKeyboardButton("➕ New Session", callback_data="cmd_new_session"),
        ],
        [
            InlineKeyboardButton("🛡️ FRIDAY Health", callback_data="cmd_friday_health"),
            InlineKeyboardButton("⚙️ EDITH Diagnostics", callback_data="cmd_edith_diag"),
        ],
        [InlineKeyboardButton("🖥️ System Metrics", callback_data="cmd_health")],
    ]

    await send_html(update.message, welcome, InlineKeyboardMarkup(keyboard))


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clears the active session memory — useful when HARVIS is stuck or confused."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return
    active_sid = get_active_session(user.id)
    session_id = f"telegram_{user.id}_{active_sid}"
    count = clear_session(session_id)
    await send_html(update.effective_message, f"🧹 <b>Session memory cleared</b>\nWiped <b>{count}</b> messages for session <code>{active_sid}</code>. Working memory is fresh.")


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Spawns a new dedicated project workspace session."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return
    title = " ".join(context.args).strip() if context.args else None
    sid = create_session(user.id, title=title)
    info = get_session_info(sid)
    disp_title = info.get("title", sid) if info else sid

    keyboard = [
        [
            InlineKeyboardButton("📂 View Sessions", callback_data="cmd_list_sessions"),
            InlineKeyboardButton("🌐 Switch to General", callback_data="switch_s_general"),
        ]
    ]
    await send_html(
        update.effective_message,
        f"🚀 <b>New Workspace Session Started: <code>{sid}</code></b>\n\n"
        f"• <b>Title:</b> {ui.esc(disp_title)}\n"
        f"• <b>Status:</b> Active\n\n"
        f"All code, tools, project files, and multi-turn notes will be saved under this workspace.\n"
        f"Switch back to general chat anytime with <code>/general</code>.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all user sessions with interactive buttons."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return
    sessions = list_user_sessions(user.id, limit=8)

    lines = ["📂 <b>Your Workspace Sessions</b>\n"]
    keyboard = []

    for s in sessions:
        sid = s["id"]
        is_curr = s["is_active"]
        badge = "⚡ <b>(Active)</b>" if is_curr else ""
        dir_info = f"\n   📁 <code>{ui.esc(s['active_dir'])}</code>" if s.get("active_dir") else ""
        msg_info = f" • {s.get('msg_count', 0)} msgs" if s.get("msg_count") else ""

        lines.append(f"• <b>{ui.esc(s['title'])}</b> [<code>{sid}</code>] {badge}{dir_info}{msg_info}")

        if is_curr:
            row = [
                InlineKeyboardButton(f"✅ Current: {sid}", callback_data="noop"),
                InlineKeyboardButton("ℹ️ Info", callback_data=f"info_s_{sid}"),
            ]
        else:
            row = [
                InlineKeyboardButton(f"⚡ Switch {sid}", callback_data=f"switch_s_{sid}"),
                InlineKeyboardButton("ℹ️ Info", callback_data=f"info_s_{sid}"),
            ]
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("➕ New Session", callback_data="cmd_new_session"),
        InlineKeyboardButton("🌐 General Chat", callback_data="switch_s_general"),
    ])

    await send_html(update.effective_message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switches active session by ID."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return
    if not context.args:
        await sessions_command(update, context)
        return
    target_sid = context.args[0].lower().strip()
    info = get_session_info(target_sid)
    if not info:
        await send_html(update.effective_message, f"❌ Session <code>{ui.esc(target_sid)}</code> not found. Use <code>/sessions</code> to view existing ones.")
        return
    set_active_session(user.id, target_sid)
    await send_html(
        update.effective_message,
        f"⚡ <b>Switched to Session: <code>{target_sid}</code></b>\n"
        f"• <b>Title:</b> {ui.esc(info.get('title', target_sid))}\n"
        f"• <b>Working Directory:</b> <code>{ui.esc(info.get('active_dir') or 'Not set')}</code>\n\n"
        f"Context and memory restored. What would you like to do next, Harold?"
    )


async def sessioninfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays detailed session metadata and cumulative notes."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return
    target_sid = context.args[0].lower().strip() if context.args else get_active_session(user.id)
    info = get_session_info(target_sid)
    if not info:
        await send_html(update.effective_message, f"❌ Session <code>{ui.esc(target_sid)}</code> not found.")
        return

    from brain.state_manager import BASE_SESSIONS_DIR
    s_notes_path = os.path.join(BASE_SESSIONS_DIR, target_sid, "session_notes.md")
    disk_notes = ""
    if os.path.exists(s_notes_path):
        try:
            with open(s_notes_path, "r", encoding="utf-8") as f:
                disk_notes = f.read().strip()
        except Exception:
            pass

    notes_to_show = disk_notes or info.get("notes") or "No notes recorded yet."
    if len(notes_to_show) > 1500:
        notes_to_show = notes_to_show[-1500:]

    card = (
        f"ℹ️ <b>Session Details: <code>{target_sid}</code></b>\n\n"
        f"• <b>Title:</b> {ui.esc(info.get('title'))}\n"
        f"• <b>Active Dir:</b> <code>{ui.esc(info.get('active_dir') or 'Not set')}</code>\n"
        f"• <b>Messages:</b> {info.get('msg_count', 0)}\n"
        f"• <b>Created:</b> <code>{info.get('created_at') or '-'}</code>\n"
        f"• <b>Last Active:</b> <code>{info.get('last_active') or '-'}</code>\n\n"
        f"<b>Session Notes:</b>\n"
        f"<blockquote expandable>{ui.esc(notes_to_show)}</blockquote>"
    )

    keyboard = [
        [
            InlineKeyboardButton(f"⚡ Switch to {target_sid}", callback_data=f"switch_s_{target_sid}"),
            InlineKeyboardButton("📂 All Sessions", callback_data="cmd_list_sessions"),
        ]
    ]
    await send_html(update.effective_message, card, reply_markup=InlineKeyboardMarkup(keyboard))


async def general_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switches back to the general scratchpad session."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return
    set_active_session(user.id, "general")
    await send_html(
        update.effective_message,
        "🌐 <b>Switched to General Chat.</b>\n\n"
        "Ambient memory of your recent project sessions is active. Type any question or command, or use <code>/new</code> to start a new workspace."
    )


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return

    msg = await send_html(update.message, "⏳ <b>Auditing system…</b>")
    res = await asyncio.to_thread(spawn_subagent, "friday", "Audit CPU load, memory usage, disk space, and failed units. Reply in plain text.")
    output = clean_response(res.get("output", "Health audit completed.")) or "Health audit completed."
    await _safe_edit(msg, ui.render_final(output)[0])


async def friday_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return

    task = " ".join(context.args) if context.args else "Audit system health and journalctl error logs"
    msg = await send_html(update.message, f"🛡️ <b>Spawning FRIDAY…</b>\n<code>{ui.esc(task)}</code>")
    res = await asyncio.to_thread(spawn_subagent, "friday", task)
    output = clean_response(res.get("output", "")) or f"FRIDAY finished task: {task}"
    await _safe_edit(msg, ui.render_final(output)[0])


async def edith_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return

    task = " ".join(context.args) if context.args else "Perform deep log traceback diagnostics on byteai.service"
    msg = await send_html(update.message, f"⚙️ <b>Spawning EDITH…</b>\n<code>{ui.esc(task)}</code>")
    res = await asyncio.to_thread(spawn_subagent, "edith", task)
    output = clean_response(res.get("output", "")) or f"EDITH finished task: {task}"
    await _safe_edit(msg, ui.render_final(output)[0])


import threading

ACTIVE_CANCELS: dict[int, threading.Event] = {}

# ─── Mode System ──────────────────────────────────────────────────────────────
# Modes: "think" | "fast" | "normal"
USER_MODES: dict[int, str] = {}
USER_MODELS: dict[int, str] = {}

MODE_CONFIG = {
    "normal": {
        "emoji":  "🛡️",
        "label":  "Default",
        "badge":  "🛡️ <b>Claude Sonnet 4.5…</b>",
        "desc":   "Flagship self-verifying engineering harness with silent failovers.",
    },
    "fast": {
        "emoji":  "⚡",
        "label":  "Fast",
        "badge":  "⚡ <b>Fast (kr/glm-5 ~1.8s)…</b>",
        "desc":   "Instant lightweight triage and speed responses.",
    },
}


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends immediate stop signal to active LLM generation/tool execution."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return

    cancel_evt = ACTIVE_CANCELS.get(user.id)
    if cancel_evt and not cancel_evt.is_set():
        cancel_evt.set()
        await send_html(update.message, "🛑 <b>Stop signal sent</b>")
    else:
        await send_html(update.message, "ℹ️ No active generation is currently running.")


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    """Switches HARVIS to a new persistent mode for all future messages."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return

    query = " ".join(context.args) if context.args else ""
    target_mode = "fast" if mode == "fast" else "normal"

    if query:
        await send_html(update.message, f"⚡ One-shot mode: <code>{ui.esc(target_mode)}</code>")
        await process_chat_query(update, context, query, mode=target_mode)
    else:
        USER_MODES[user.id] = target_mode
        await send_html(update.message, ui.md_to_html(switch_message(target_mode), legacy=True))


async def fast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run a fast one-shot query or switch to fast sprint engine."""
    await mode_command(update, context, "fast")


async def mode_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current active mode."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return
    mode = USER_MODES.get(user.id, DEFAULT_MODE)
    await send_html(update.message, ui.md_to_html(status_message(mode), legacy=True))


# ─── Main Message Handler ─────────────────────────────────────────────────────

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles plain user messages with multi-turn memory and tool execution loop."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        await update.message.reply_text("🚫 Access Denied.")
        return

    prompt_text = update.message.text
    if not prompt_text:
        return

    # Auto-stop any prior active generation so the new human request takes priority
    prior_cancel = ACTIVE_CANCELS.get(user.id)
    if prior_cancel and not prior_cancel.is_set():
        logger.info(f"New incoming message from user {user.id} — aborting prior running task.")
        prior_cancel.set()
        await asyncio.sleep(0.15)  # brief yield to let prior loop exit cleanly

    parsed = parse_slash(prompt_text)
    if parsed:
        if parsed["kind"] == "status":
            mode = USER_MODES.get(user.id, DEFAULT_MODE)
            await send_html(update.message, ui.md_to_html(status_message(mode), legacy=True))
            return
        if parsed["kind"] == "set":
            USER_MODES[user.id] = parsed["mode"]
            await send_html(update.message, ui.md_to_html(switch_message(parsed["mode"]), legacy=True))
            return
        if parsed["kind"] == "oneshot":
            await process_chat_query(update, context, parsed["prompt"], mode=parsed["mode"])
            return

    mode = USER_MODES.get(user.id, DEFAULT_MODE)
    await process_chat_query(update, context, prompt_text, mode=mode)


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles photo messages - converts to base64 and processes with vision."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        await update.message.reply_text("🚫 Access Denied.")
        return

    try:
        logger.info(f"📷 Photo received from user {user.id}")
        await send_html(update.message, "🖼️ <b>Processing image…</b>")
        
        # Get the largest photo
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        
        # Download photo to bytes
        photo_bytes = await photo_file.download_as_bytearray()
        logger.info(f"Downloaded {len(photo_bytes)} bytes")
        
        # Convert to base64
        base64_image = base64.b64encode(photo_bytes).decode('utf-8')
        logger.info(f"Converted to base64, length: {len(base64_image)}")
        
        # Get caption if available, otherwise use default prompt
        caption = update.message.caption or "What's in this image?"
        logger.info(f"Caption: {caption}")
        
        # Create prompt with embedded image
        prompt_with_image = f"{caption}\n\n[IMAGE: data:image/jpeg;base64,{base64_image}]"
        
        # Process with auto vision detection
        mode = USER_MODES.get(user.id, DEFAULT_MODE)
        logger.info(f"Processing photo with mode: {mode}")
        await process_chat_query(update, context, prompt_with_image, mode=mode)
        
    except Exception as e:
        logger.error(f"Error in photo_handler: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error processing photo: {str(e)}")


async def process_chat_query(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt_text: str, is_thinking_mode: bool = False, mode: str = "normal", model_override: str = None):
    """Core chat query execution pipeline with live streaming, thinking bubbles, and tool dispatch."""
    user = update.effective_user
    active_sid = get_active_session(user.id)
    session_id = f"telegram_{user.id}_{active_sid}"

    if active_sid != "general":
        info = get_session_info(active_sid)
        if info and (info.get("title", "").startswith("Session ") or not info.get("title")):
            clean_first = prompt_text.strip()[:45].replace("\n", " ")
            update_session(active_sid, title=clean_first)

    cancel_evt = threading.Event()
    ACTIVE_CANCELS[user.id] = cancel_evt

    stop_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Stop Generation", callback_data=f"cancel_gen_{user.id}")]
    ])

    # Initial status bubble
    cfg = MODE_CONFIG.get(mode, MODE_CONFIG["normal"])
    status = ui.Status(cfg["label"], cfg["emoji"])
    status_msg = await send_html(update.message, status.render("Starting…"), stop_keyboard)

    current_prompt = prompt_text
    role = "user"
    final_text = ""
    step_bubbles = []
    loop = asyncio.get_running_loop()

    last_tool_name = None
    last_tool_output = ""
    last_tool_success = True

    try:
        # Check if the user is replying to a specific Telegram message (e.g. heartbeat or prior question)
        reply_context = ""
        if update.message.reply_to_message:
            reply_msg = update.message.reply_to_message
            target_text = reply_msg.text or reply_msg.caption or ""
            if target_text:
                is_bot = reply_msg.from_user and reply_msg.from_user.is_bot
                sender_name = "HARVIS" if is_bot else (reply_msg.from_user.first_name if reply_msg.from_user else "User")
                clean_target = target_text.strip().replace("\n", " ")[:200]
                reply_context = f" (Replying to {sender_name}: \"{clean_target}\")"

        current_prompt = f"[via Telegram]{reply_context} {prompt_text}"

        stream_buffer = ""
        last_tg_edit_time = [0.0]
        step_count = [0]
        thinking_bubble = [None]

        def tg_stream_cb(chunk):
            nonlocal stream_buffer
            if cancel_evt.is_set():
                raise RuntimeError("Cancelled by user")
            if not chunk:
                return
            stream_buffer += chunk
            now = time.time()
            if now - last_tg_edit_time[0] <= 0.8:
                return
            last_tg_edit_time[0] = now

            think_text, answer_text = ui.split_think(stream_buffer)
            if think_text:
                view = ui.think_view(think_text)
                if thinking_bubble[0] is None:
                    try:
                        fut = asyncio.run_coroutine_threadsafe(send_html(update.message, view), loop)
                        thinking_bubble[0] = fut.result(timeout=1.5)
                    except Exception:
                        pass
                else:
                    asyncio.run_coroutine_threadsafe(_safe_edit(thinking_bubble[0], view), loop)

            answer_text = clean_response(answer_text)
            if answer_text.strip():
                asyncio.run_coroutine_threadsafe(
                    _safe_edit(status_msg, ui.stream_view(answer_text, status), stop_keyboard), loop)

        # ── Run the request through the autonomous harness ──
        pending = {}

        async def on_progress(ev):
            nonlocal stream_buffer, last_tool_name, last_tool_output, last_tool_success
            kind = ev.get("type")

            if kind == "thinking":
                stream_buffer = ""
                await _safe_edit(status_msg, status.render("Thinking…"), stop_keyboard)

            elif kind == "acting":
                name, args = ev["tool_name"], ev.get("tool_args") or {}
                last_tool_name = name
                step_count[0] = ev["step"]
                status.start(ev["step"], name)
                await _safe_edit(status_msg, status.render("Working…"), stop_keyboard)
                bubble = await send_html(update.message, ui.step_running(ev["step"], name, args))
                step_bubbles.append(bubble)
                pending[ev["step"]] = (bubble, name, args, time.time())

            elif kind == "checked":
                bubble, name, args, t0 = pending.pop(
                    ev["step"], (None, ev.get("tool_name", "?"), {}, time.time()))
                out = (ev.get("output") or "").strip()
                ok = bool(ev.get("success"))
                last_tool_success = ok
                last_tool_output = out if ok else f"Error: {out}"
                secs = time.time() - t0
                status.finish(ev["step"], ok, name, args, out, secs)
                if bubble:
                    await _safe_edit(bubble, ui.step_done(ev["step"], name, args, out, ok, secs))
                await _safe_edit(status_msg, status.render("Thinking…"), stop_keyboard)

        # Chat memory: earlier conversation goes in as context, minus legacy tool-call plumbing.
        prefix = []
        for m in get_history(session_id, limit=20):
            c = m.get("content")
            if m.get("role") in ("user", "assistant") and isinstance(c, str) and c.strip() \
                    and not (c.startswith("{") and '"tool_call"' in c) and not c.startswith("[TOOL EXECUTION RESULT]"):
                prefix.append({"role": m["role"], "content": c})

        images = detect_images_in_prompt(current_prompt)
        goal = prepare_vision_request(current_prompt, images) if images else current_prompt
        saved_user = re.sub(r"\[IMAGE:\s*data:image/[a-zA-Z]+;base64,[a-zA-Z0-9+/=]+\]", "[IMAGE ATTACHED]", current_prompt) \
            if images else current_prompt
        save_message(session_id, "user", saved_user)

        engine = (lambda history, **kw: complete(history, model_override=model_override, **kw)) if model_override else None
        runner = HarnessRunner(
            session_id=active_sid,
            user_id=f"telegram_{user.id}",
            mode=mode,
            engine=engine,
            system_prompt=TELEGRAM_SYSTEM_PROMPT,
            history_prefix=prefix
        )

        set_harvis_busy(True)
        try:
            result = await runner.run(goal, progress_callback=on_progress, cancel_event=cancel_evt,
                                      stream_callback=tg_stream_cb)
        finally:
            set_harvis_busy(False)

        status_code = result.get("status")
        if status_code == "cancelled":
            final_text = "🛑 <b>Generation stopped by Harold.</b>"
        elif status_code in ("finished", "answered"):
            final_text = result.get("output") or ""
            if status_code == "finished" and result.get("success") is False and final_text:
                final_text = f"⚠️ {final_text}"
        else:  # timeout, step_limit_reached, stuck, provider_error
            final_text = f"⚠️ {result.get('message', 'Stopped early.')} Say 'continue' to pick up where I left off."

        save_message(session_id, "assistant",
                     final_text if status_code in ("finished", "answered", "cancelled")
                     else f"{final_text}\n(step trace: {result.get('trace')})")

        # Process thinking tags if present in final text
        think_content = ""
        if final_text and "<think>" in final_text:
            think_matches = re.findall(r"<think>(.*?)(?:</think>|$)", final_text, re.DOTALL)
            think_content = "".join(think_matches).strip()
            final_text = re.sub(r"<think>.*?(?:</think>|$)", "", final_text, flags=re.DOTALL).strip()

        if think_content:
            if thinking_bubble[0]:
                await _safe_edit(thinking_bubble[0], ui.think_view(think_content, done=True))
            else:
                try:
                    await send_html(update.message, ui.think_view(think_content, done=True))
                except Exception:
                    pass

        final_text = clean_response(final_text)

        # status bubble always goes; step bubbles only if you want them kept
        if not ui.KEEP_STEP_BUBBLES:
            for bubble in step_bubbles:
                try:
                    await bubble.delete()
                except Exception:
                    pass
        try:
            await status_msg.delete()
        except Exception:
            pass

        # final answer: converted to Telegram HTML, chunked safely, footer + log button on the last chunk
        chunks = ui.render_final(final_text) or ["Done! 👍"]
        foot = ui.footer(status)
        if len(chunks[-1]) + len(foot) <= 4000:
            chunks[-1] += foot
        else:
            chunks.append(foot.strip())

        markup = None
        if status.log:
            task_id = uuid.uuid4().hex[:10]
            ui.store_log(task_id, status.log)
            markup = ui.details_keyboard(task_id)

        for i, chunk in enumerate(chunks):
            try:
                await send_html(update.message, chunk, markup if i == len(chunks) - 1 else None)
            except Exception as e:
                logger.error(f"Failed to send Telegram reply: {e}")

    finally:
        ACTIVE_CANCELS.pop(user.id, None)


# ─── Inline Button Handler ────────────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    data = query.data
    if data.startswith("cancel_gen_"):
        target_id = int(data.replace("cancel_gen_", ""))
        cancel_evt = ACTIVE_CANCELS.get(target_id)
        if cancel_evt and not cancel_evt.is_set():
            cancel_evt.set()
            await query.answer("🛑 Stopping generation...", show_alert=True)
        else:
            await query.answer("Generation already stopped.", show_alert=False)
        return

    if data.startswith("log_"):
        log = ui.TASK_LOGS.get(data[4:])
        if not log:
            await query.answer("That log has expired.", show_alert=False)
            return
        await query.answer()
        buf = io.BytesIO(ui.render_log(log).encode("utf-8"))
        buf.name = "harvis_step_log.txt"
        await query.message.reply_document(buf, caption="📋 Step log")
        return

    if data.startswith("switch_s_"):
        target_sid = data[9:]
        user = update.effective_user
        set_active_session(user.id, target_sid)
        info = get_session_info(target_sid)
        title = info.get("title", target_sid) if info else target_sid
        await query.answer(f"Switched to {target_sid}")
        await _safe_edit(
            query.message,
            f"⚡ <b>Switched to Session: <code>{target_sid}</code></b>\n"
            f"• <b>Title:</b> {ui.esc(title)}\n"
            f"• <b>Working Directory:</b> <code>{ui.esc((info.get('active_dir') if info else '') or 'Not set')}</code>\n\n"
            f"Context and notes restored."
        )
        return

    if data.startswith("info_s_"):
        target_sid = data[7:]
        info = get_session_info(target_sid)
        if not info:
            await query.answer("Session not found.", show_alert=True)
            return
        await query.answer()

        from brain.state_manager import BASE_SESSIONS_DIR
        s_notes_path = os.path.join(BASE_SESSIONS_DIR, target_sid, "session_notes.md")
        disk_notes = ""
        if os.path.exists(s_notes_path):
            try:
                with open(s_notes_path, "r", encoding="utf-8") as f:
                    disk_notes = f.read().strip()
            except Exception:
                pass

        notes_to_show = disk_notes or info.get("notes") or "No notes recorded yet."
        if len(notes_to_show) > 1500:
            notes_to_show = notes_to_show[-1500:]

        card = (
            f"ℹ️ <b>Session Details: <code>{target_sid}</code></b>\n\n"
            f"• <b>Title:</b> {ui.esc(info.get('title'))}\n"
            f"• <b>Active Dir:</b> <code>{ui.esc(info.get('active_dir') or 'Not set')}</code>\n"
            f"• <b>Messages:</b> {info.get('msg_count', 0)}\n"
            f"• <b>Created:</b> <code>{info.get('created_at') or '-'}</code>\n"
            f"• <b>Last Active:</b> <code>{info.get('last_active') or '-'}</code>\n\n"
            f"<b>Session Notes:</b>\n"
            f"<blockquote expandable>{ui.esc(notes_to_show)}</blockquote>"
        )
        keyboard = [
            [
                InlineKeyboardButton(f"⚡ Switch to {target_sid}", callback_data=f"switch_s_{target_sid}"),
                InlineKeyboardButton("📂 All Sessions", callback_data="cmd_list_sessions"),
            ]
        ]
        await send_html(query.message, card, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "cmd_list_sessions":
        await query.answer()
        await sessions_command(update, context)
        return

    if data == "cmd_new_session":
        await query.answer()
        await new_command(update, context)
        return

    if data == "noop":
        await query.answer("Already in this session.", show_alert=False)
        return

    await query.answer()
    loading = await send_html(query.message, "⏳ <b>Working…</b>")

    if data == "cmd_health":
        res = spawn_subagent("friday", "Audit CPU, Memory, and Disk space. Plain text reply.")
        output = clean_response(res.get("output", "Done."))
    elif data == "cmd_friday_health":
        res = spawn_subagent("friday", "Audit journalctl error logs and system health. Plain text reply.")
        output = clean_response(res.get("output", "Done."))
    elif data == "cmd_edith_diag":
        res = spawn_subagent("edith", "Diagnose byteai.service and check system updates. Plain text reply.")
        output = clean_response(res.get("output", "Done."))
    else:
        output = "Unknown command."

    await _safe_edit(loading, ui.render_final(output)[0])


# ─── Utility ──────────────────────────────────────────────────────────────────

async def _safe_edit(msg, html_text: str, markup=None, reply_markup=None):
    """Edit with Telegram HTML; fall back to plain text if the HTML is rejected."""
    if not msg or not html_text:
        return
    rm = markup if markup is not None else reply_markup
    try:
        await msg.edit_text(html_text[:4096], parse_mode=ParseMode.HTML,
                            reply_markup=rm, disable_web_page_preview=True)
    except Exception as e:
        s = str(e)
        if "not modified" in s or "RetryAfter" in s or "Too Many" in s or "Flood" in s:
            return
        try:
            await msg.edit_text(ui.strip_tags(html_text)[:4096], reply_markup=rm)
        except Exception as e2:
            if "not modified" not in str(e2):
                logger.warning(f"Failed to edit message: {e2}")


async def send_html(msg, html_text: str, markup=None, reply_markup=None):
    """Reply with Telegram HTML; fall back to plain text. Returns the sent message."""
    rm = markup if markup is not None else reply_markup
    try:
        return await msg.reply_text(html_text[:4096], parse_mode=ParseMode.HTML,
                                    reply_markup=rm, disable_web_page_preview=True)
    except Exception as e:
        logger.warning(f"HTML send failed ({e}); falling back to plain text")
        return await msg.reply_text(ui.strip_tags(html_text)[:4096], reply_markup=rm)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await send_html(
                update.effective_message,
                f"❌ <b>System Error:</b> <code>{ui.esc(str(context.error)[:200])}</code>"
            )
        except Exception:
            pass


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    token = TELEGRAM_BOT_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("\n❌ Error: TELEGRAM_BOT_TOKEN is not set in config.py or environment.")
        print("👉 Create a bot via @BotFather on Telegram and set TELEGRAM_BOT_TOKEN.\n")
        return

    print("🚀 Starting HARVIS Telegram Bot Bridge…")
    app = ApplicationBuilder().token(token).concurrent_updates(True).build()
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("newsession", new_command))
    app.add_handler(CommandHandler("sessions", sessions_command))
    app.add_handler(CommandHandler("session", session_command))
    app.add_handler(CommandHandler("s", session_command))
    app.add_handler(CommandHandler("sessioninfo", sessioninfo_command))
    app.add_handler(CommandHandler("general", general_command))
    app.add_handler(CommandHandler("g", general_command))
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("fast", fast_command))
    app.add_handler(CommandHandler("mode", mode_status_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("cancel", stop_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("friday", friday_command))
    app.add_handler(CommandHandler("edith", edith_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler), group=1)

    print("✅ HARVIS Telegram Bot Polling Active!")
    app.run_polling()


if __name__ == "__main__":
    main()
