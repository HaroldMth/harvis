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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from brain.router import ask
from brain.modes import parse_slash, switch_message, status_message, DEFAULT_MODE, get_engine
from brain.harness import HarnessRunner
from tools.registry import dispatch
from subagents.runner import spawn_subagent
from db.store import clear_session
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
- Execute tools first, then summarise the result warmly in 1-3 sentences. Do not leave the job half-finished!"""

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
        f"🤖 *HARVIS — Telegram Control Interface*\n\n"
        f"Hey Harold! Your desktop assistant is live.\n\n"
        f"*Your ID:* `{user.id}`\n\n"
        f"*Quick Commands:*\n"
        f"• `/normal` `/think` `/coding` `/vision` — persist capable OmniRoute pool\n"
        f"• `/fast` `/cheap` — aliases of `/normal` (fast pools disabled)\n"
        f"• `/mode` — show current mode\n"
        f"• `/think <query>` — one-shot thinking without switching\n"
        f"• `/health` — System health snapshot\n"
        f"• `/friday <task>` — Spawn FRIDAY (System Doctor)\n"
        f"• `/edith <task>` — Spawn EDITH (Deep Diagnostics)\n"
        f"• Just *type anything* to command HARVIS!"
    )

    keyboard = [
        [
            InlineKeyboardButton("🛡️ FRIDAY Health", callback_data="cmd_friday_health"),
            InlineKeyboardButton("⚙️ EDITH Diagnostics", callback_data="cmd_edith_diag"),
        ],
        [InlineKeyboardButton("🖥️ System Metrics", callback_data="cmd_health")],
    ]

    await update.message.reply_text(
        welcome, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clears the current session memory — useful when HARVIS is stuck or confused."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return
    session_id = f"telegram_{user.id}"
    count = clear_session(session_id)
    await update.message.reply_text(
        f"🧹 Session cleared! Wiped {count} messages.\nHARVIS memory is fresh — say hi!"
    )


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return

    msg = await update.message.reply_text("⏳ Auditing system…")
    res = spawn_subagent("friday", "Audit CPU load, memory usage, disk space, and failed units. Reply in plain text.")
    output = clean_response(res.get("output", "Health audit completed.")) or "Health audit completed."
    await _safe_edit(msg, output)


async def friday_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return

    task = " ".join(context.args) if context.args else "Audit system health and journalctl error logs"
    msg = await update.message.reply_text(
        f"🛡️ *Spawning FRIDAY…*\nTask: `{task}`", parse_mode="Markdown"
    )
    res = spawn_subagent("friday", task)
    output = clean_response(res.get("output", "")) or f"FRIDAY finished task: {task}"
    await _safe_edit(msg, output)


async def edith_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return

    task = " ".join(context.args) if context.args else "Perform deep log traceback diagnostics on byteai.service"
    msg = await update.message.reply_text(
        f"⚙️ *Spawning EDITH…*\nTask: `{task}`", parse_mode="Markdown"
    )
    res = spawn_subagent("edith", task)
    output = clean_response(res.get("output", "")) or f"EDITH finished task: {task}"
    await _safe_edit(msg, output)


import threading

ACTIVE_CANCELS: dict[int, threading.Event] = {}

# ─── Mode System ──────────────────────────────────────────────────────────────
# Modes: "think" | "fast" | "normal"
USER_MODES: dict[int, str] = {}
USER_MODELS: dict[int, str] = {}

MODE_CONFIG = {
    "normal": {
        "emoji":  "🛡️",
        "label":  "Autonomous Master",
        "badge":  "🛡️ *Autonomous Engine (kr/claude-sonnet-4.5)…*",
        "desc":   "Flagship self-verifying engineering harness with silent failovers.",
    },
    "fast": {
        "emoji":  "⚡",
        "label":  "Fast Sprint",
        "badge":  "⚡ *Fast Sprint (kr/glm-5 ~1.8s)…*",
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
        await update.message.reply_text("🛑 *Sent stop signal to HARVIS!*", parse_mode="Markdown")
    else:
        await update.message.reply_text("ℹ️ No active generation is currently running.")


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    """Switches HARVIS to a new persistent mode for all future messages."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return

    query = " ".join(context.args) if context.args else ""
    target_mode = "fast" if mode == "fast" else "normal"

    if query:
        current_mode = USER_MODES.get(user.id, DEFAULT_MODE)
        await update.message.reply_text(
            f"⚡ One-shot mode: `{target_mode}`."
        )
        await process_chat_query(update, context, query, mode=target_mode)
    else:
        USER_MODES[user.id] = target_mode
        await update.message.reply_text(switch_message(target_mode), parse_mode="Markdown")


async def ultra_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await mode_command(update, context, "normal")

async def think_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await mode_command(update, context, "normal")

async def fast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await mode_command(update, context, "fast")

async def normal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await mode_command(update, context, "normal")

async def coding_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await mode_command(update, context, "normal")

async def cheap_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await mode_command(update, context, "fast")

async def vision_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await mode_command(update, context, "normal")

async def use_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Explains the 2-tier engine system."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return
    await update.message.reply_text(
        "🤖 *Unified 2-Tier Engine Architecture*\n\n"
        "• **Default Engine:** `kr/claude-sonnet-4.5` (Autonomous Master with silent failovers)\n"
        "• **/fast:** `kr/glm-5` (1.8s Sprint Engine)\n\n"
        "Manual `/use` pinning is deprecated. Every standard request automatically runs on the master engine.",
        parse_mode="Markdown"
    )


async def mode_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current active mode."""
    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return
    mode = USER_MODES.get(user.id, DEFAULT_MODE)
    await update.message.reply_text(status_message(mode), parse_mode="Markdown")


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

    parsed = parse_slash(prompt_text)
    if parsed:
        if parsed["kind"] == "status":
            mode = USER_MODES.get(user.id, DEFAULT_MODE)
            await update.message.reply_text(status_message(mode), parse_mode="Markdown")
            return
        if parsed["kind"] == "set":
            USER_MODES[user.id] = parsed["mode"]
            await update.message.reply_text(switch_message(parsed["mode"]), parse_mode="Markdown")
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
        await update.message.reply_text("🖼️ *Processing image...*", parse_mode="Markdown")
        
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
    session_id = f"telegram_{user.id}"

    cancel_evt = threading.Event()
    ACTIVE_CANCELS[user.id] = cancel_evt

    stop_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Stop Generation", callback_data=f"cancel_gen_{user.id}")]
    ])

    # Initial status bubble
    cfg = MODE_CONFIG.get(mode, MODE_CONFIG["normal"])
    initial_badge = cfg["badge"]
    status_msg = await update.message.reply_text(initial_badge, parse_mode="Markdown", reply_markup=stop_keyboard)

    max_turns = 35
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
            if chunk:
                stream_buffer += chunk
                now = time.time()
                if now - last_tg_edit_time[0] > 0.8:
                    last_tg_edit_time[0] = now
                    
                    if "<think>" in stream_buffer:
                        think_matches = re.findall(r"<think>(.*?)(?:</think>|$)", stream_buffer, re.DOTALL)
                        think_text = "".join(think_matches).strip()
                        answer_text = re.sub(r"<think>.*?(?:</think>|$)", "", stream_buffer, flags=re.DOTALL).strip()

                        if think_text:
                            think_display = f"🧠 *Thinking Process…*\n```\n{think_text[:1500]}\n```"
                            if thinking_bubble[0] is None:
                                try:
                                    future = asyncio.run_coroutine_threadsafe(
                                        update.message.reply_text(think_display, parse_mode="Markdown"),
                                        loop
                                    )
                                    thinking_bubble[0] = future.result(timeout=1.5)
                                except Exception:
                                    pass
                            else:
                                asyncio.run_coroutine_threadsafe(
                                    _safe_edit(thinking_bubble[0], think_display),
                                    loop
                                )

                        txt_snapshot = clean_response(answer_text)
                        if txt_snapshot:
                            asyncio.run_coroutine_threadsafe(
                                _safe_edit(status_msg, txt_snapshot),
                                loop
                            )
                    else:
                        txt_snapshot = clean_response(stream_buffer)
                        if txt_snapshot:
                            asyncio.run_coroutine_threadsafe(
                                _safe_edit(status_msg, txt_snapshot),
                                loop
                            )

        for turn in range(max_turns):
            if cancel_evt.is_set():
                final_text = "🛑 *Generation stopped by Harold.*"
                break
            stream_buffer = ""
            response = await asyncio.to_thread(
                ask,
                session_id,
                current_prompt,
                role=role,
                system_prompt=TELEGRAM_SYSTEM_PROMPT,
                stream_callback=tg_stream_cb,
                thinking_mode=False,
                mode=mode,
                model_override=model_override,
            )

            if response.get("type") == "text":
                content = response.get("content", "")
                continuation_signals = [
                    "now i will", "now i'll", "next, i will", "next i'll", "moving on to",
                    "next step", "proceeding to", "i will now", "continuing with",
                    "now creating", "now writing", "now implementing", "let's now",
                    "now let's", "next up"
                ]
                content_lower = content.lower()
                is_intermediate = any(sig in content_lower for sig in continuation_signals)
                if is_intermediate and turn < max_turns - 1:
                    logger.info(f"Intermediate thought detected: '{content[:80]}'. Continuing tool loop...")
                    await _safe_edit(status_msg, f"⚙️ {content[:160]}…")
                    current_prompt = "Understood. Proceed immediately with executing the next tool action. Do not stop until the entire task is verified and complete."
                    role = "user"
                    continue
                else:
                    final_text = content
                    break

            elif response.get("type") == "tool_call":
                tool_name = response["tool_name"]
                tool_args = response.get("tool_args", {}) or {}
                last_tool_name = tool_name

                TOOL_EMOJI = {
                    "shell_exec": "💻", "system_info": "🖥️", "send_telegram": "📨",
                    "check_emails": "📬", "send_email": "📧", "screenshot": "📸",
                    "open_app": "🚀", "play_song": "🎵", "notify": "🔔",
                    "volume_control": "🔊", "brightness_control": "💡", "timer": "⏱️",
                    "media_control": "⏯️", "weather": "🌤️", "clipboard": "📋",
                    "spawn_subagent": "🤖", "web_search": "🔍", "close_app": "🛑",
                }
                emoji = TOOL_EMOJI.get(tool_name, "🛠️")

                if tool_args:
                    args_preview = ", ".join(
                        f"{k}={repr(v)[:40]}" for k, v in list(tool_args.items())[:3]
                    )
                else:
                    args_preview = "no args"

                step_count[0] += 1
                n = step_count[0]

                # Update main progress bubble
                await _safe_edit(status_msg, f"⚙️ Step {n}: `{tool_name}`…")

                # New per-tool bubble (running state)
                step_bubble = await update.message.reply_text(
                    f"{emoji} `{tool_name}({args_preview})`",
                    parse_mode="Markdown"
                )
                step_bubbles.append(step_bubble)

                tool_res = await asyncio.to_thread(dispatch, tool_name, tool_args)

                if tool_res.get("success"):
                    last_tool_success = True
                    result_text = tool_res.get("output", "")
                    last_tool_output = result_text
                    short_result = result_text[:300] + ("…" if len(result_text) > 300 else "")
                    await _safe_edit(
                        step_bubble,
                        f"{emoji} `{tool_name}` ✅\n```\n{short_result}\n```"
                    )
                    await _safe_edit(status_msg, f"✅ Step {n} done: `{tool_name}`")
                else:
                    last_tool_success = False
                    result_text = tool_res.get("error", "Unknown error")
                    last_tool_output = f"Error: {result_text}"
                    await _safe_edit(
                        step_bubble,
                        f"{emoji} `{tool_name}` ❌\n`{result_text[:200]}`"
                    )
                    await _safe_edit(status_msg, f"❌ Step {n} failed: `{tool_name}`")

                current_prompt = json.dumps({
                    "tool_name": tool_name,
                    "result": result_text,
                    "status": "success" if last_tool_success else "failed"
                })
                role = "tool"
            else:
                break

        # Process thinking tags if present in final text
        think_content = ""
        if final_text and "<think>" in final_text:
            think_matches = re.findall(r"<think>(.*?)(?:</think>|$)", final_text, re.DOTALL)
            think_content = "".join(think_matches).strip()
            final_text = re.sub(r"<think>.*?(?:</think>|$)", "", final_text, flags=re.DOTALL).strip()

            if think_content:
                if thinking_bubble[0]:
                    await _safe_edit(thinking_bubble[0], f"🧠 *Thinking Process (Complete) ✅*\n```\n{think_content[:1500]}\n```")
                else:
                    try:
                        await update.message.reply_text(f"🧠 *Thinking Process (Complete) ✅*\n```\n{think_content[:1500]}\n```", parse_mode="Markdown")
                    except Exception:
                        pass

        if not final_text:
            if think_content:
                final_text = think_content
            elif step_count[0] >= max_turns:
                final_text = f"⚠️ Reached the turn limit ({max_turns} steps) while building. Say 'continue' to resume where I left off!"
            elif last_tool_name:
                output_snippet = (last_tool_output or "").strip()
                if last_tool_success:
                    if output_snippet:
                        final_text = f"Done! {output_snippet}"
                    else:
                        final_text = f"Done — executed `{last_tool_name}` successfully. 👍"
                else:
                    final_text = f"Couldn't finish `{last_tool_name}`: {output_snippet or 'Execution error'}"
            else:
                final_text = "Done! 👍"

        final_text = clean_response(final_text)

        # Clean up temporary progress step bubbles
        for bubble in step_bubbles:
            try:
                await bubble.delete()
            except Exception:
                pass

        # Delete status bubble so temporary progress badge never stays stuck
        try:
            await status_msg.delete()
        except Exception:
            pass

        # Guaranteed delivery of final response as fresh Telegram message(s)
        chunks = safe_reply_text(final_text)
        if not chunks:
            chunks = ["Done! 👍"]

        for chunk in chunks:
            try:
                await update.message.reply_text(chunk, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Markdown reply failed ({e}), sending plain text: {chunk[:100]}")
                try:
                    await update.message.reply_text(chunk)
                except Exception as e2:
                    logger.error(f"Failed to send Telegram reply: {e2}")

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

    await query.answer()
    loading = await query.message.reply_text("⏳ Working…")

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

    await _safe_edit(loading, output)


# ─── Utility ──────────────────────────────────────────────────────────────────

async def _safe_edit(msg, text: str):
    """Edit a Telegram message with Markdown, falling back to plain text."""
    if not msg or not text:
        return
    try:
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        err_str = str(e)
        if "Message is not modified" in err_str:
            return
        if "RetryAfter" in err_str or "Too Many Requests" in err_str:
            return
        try:
            await msg.edit_text(text)
        except Exception as e2:
            if "Message is not modified" in str(e2):
                return
            logger.warning(f"Failed to edit message: {e2}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"❌ *System Error:* `{str(context.error)[:200]}`",
                parse_mode="Markdown"
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
    app = ApplicationBuilder().token(token).build()
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("ultra", ultra_command))
    app.add_handler(CommandHandler("pro", ultra_command))
    app.add_handler(CommandHandler("architect", ultra_command))
    app.add_handler(CommandHandler("god", ultra_command))
    app.add_handler(CommandHandler("thinkcode", ultra_command))
    app.add_handler(CommandHandler("think", think_command))
    app.add_handler(CommandHandler("fast", fast_command))
    app.add_handler(CommandHandler("normal", normal_command))
    app.add_handler(CommandHandler("coding", coding_command))
    app.add_handler(CommandHandler("cheap", cheap_command))
    app.add_handler(CommandHandler("vision", vision_command))
    app.add_handler(CommandHandler("use", use_command))
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
