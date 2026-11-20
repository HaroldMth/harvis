# HARVIS

A local AI agent for your Linux desktop. Talk to it from a desktop window, by voice, or from Telegram. It can run commands, read your screen, click things, browse the web, write code, and report back when the job is done.

Under the hood it's an agent loop with real tools: the model decides what to do, calls tools, looks at the results, and decides when it's finished.

> **Status:** early, built on a KDE Plasma / Wayland, Arch-based setup. Desktop vision is Linux-specific (see [Platform notes](#platform-notes)). The LLM provider layer is being reworked, so provider names and keys may change.

## Features

**Agent**
- Autonomous tool loop with step and time budgets, repeat-action detection, and a full `trace.jsonl` of every run
- The agent decides when it's done (`finish` tool) and keeps its own `task.md` checklist that survives context trimming and restarts
- Provider failover chain with cooldowns, so one dead API doesn't stop a task
- Persistent sessions and project workspaces, restored between conversations
- Planner that writes `BUILD`, `IMPLEMENTATION`, `REFACTOR` or `FIX` plans and waits for your approval in Telegram

**Interfaces**
- **Desktop GUI:** PyQt6 window with a React UI, voice input, and a global hotkey toggle
- **Telegram bot:** chat, run tasks, approve plans, open a live shell
- **Voice:** faster-whisper speech-to-text, Silero VAD, Edge TTS (Google TTS and espeak-ng as fallbacks)
- **Heartbeat daemon (optional):** checks your desktop context now and then and may message you on its own

**Tools** (about 40 built in)
- Shell and files: `shell_exec`, `file_op`, `list_dir`, `session_manager`
- Web: `web_search` (SearXNG), `fetch_url`, `browser_fetch` (headless Playwright, opens a visible window for CAPTCHAs), `open_url`
- Desktop vision and control: `screenshot`, `screenshot_and_analyze`, `analyze_image`, `desktop_look`, `desktop_find`, `desktop_locate_text`, `desktop_click*`, `desktop_type`, `desktop_press_keys`, `desktop_scroll`, `desktop_aim`
- System: `system_info`, `open_app`, `close_app`, `clipboard`, `notify`, `timer`, `volume_control`, `brightness_control`, `media_control`
- Integrations: `send_telegram`, `check_emails`, `send_email` (Gmail), `weather`, `play_song`
- Meta: `spawn_subagent`, `self_improvement_log`

**Subagents**
- **FRIDAY:** system health and log diagnostics
- **EDITH:** deeper analysis and maintenance
- Spawn your own by name. Each runs with an independent context and can consult the others.

## Quickstart

```bash
git clone https://github.com/HaroldMth/Harvis.git
cd Harvis
python -m venv .venv --system-site-packages   # exposes the distro `gi` module
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env    # then fill it in
```

**System packages** (Arch names):

```bash
sudo pacman -S tesseract tesseract-data-eng wl-clipboard python-gobject at-spi2-core spectacle
```

**Voice model** (needed for voice input):

```bash
curl -L -o audio/silero_vad.onnx \
  https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx
```

**Desktop UI** (built once, loaded by the GUI):

```bash
cd ui && npm install && npm run build && cd ..
```

**Puter speech-to-text fallback** (optional):

```bash
npm install
```

## Run

```bash
python main.py                      # desktop GUI + voice
python -m telegram_bot.bot          # Telegram bot
python -m brain.heartbeat           # proactive daemon (optional)
python -m brain.heartbeat --force   # fire a single heartbeat now
```

For a global hotkey, bind `scripts/trigger.sh` in your desktop's shortcut settings. It toggles the HARVIS window through `/tmp/harvis.sock`.

## Configuration

Copy `.env.example` to `.env`. Keys can also live in `~/.tokens` (same format), which takes priority over `.env`.

| Variable | Required | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | For Telegram | Token from @BotFather |
| `TELEGRAM_ALLOWED_USERS` | For Telegram | Comma-separated user IDs or usernames allowed to use the bot |
| `HARVIS_USER_NAME` | No | What the assistant calls you (default `User`) |
| `GROQ`, `GEMINI`, `POLLINATIONS`, `MISTRAL`, `OMNIROUTE`, ... | At least one provider | LLM provider keys (layer being reworked) |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD` | For Gmail tools | Use a Google App Password |
| `SEARXNG_URL` | For web search | Defaults to `http://localhost:8080` |
| `HARVIS_AUTO_APPROVE` | No | `1` auto-approves destructive commands in non-interactive mode |
| `HARVIS_SHELL_UNRESTRICTED` | No | `1` disables the safety filter in Telegram shell mode |

Runtime state (sessions, plans, logs, the chat database) lives in `~/.harvis/`, not in the repo. Agent limits (step budget, context sizes, timeouts) are constants in `brain/settings.py`.

## Telegram commands

| Command | What it does |
|---|---|
| `/new`, `/newsession` | Start a new project session |
| `/sessions`, `/session`, `/s`, `/sessioninfo` | List, switch to, or inspect sessions |
| `/general`, `/g` | General chat outside a project |
| `/plan` | Generate a plan and approve it with buttons |
| `/fast`, `/mode` | Switch or show the engine mode (`normal` or `fast`) |
| `/shell`, `/sh`, `/exit` | Open or close a persistent shell (every message is a bash command) |
| `/friday`, `/edith` | Hand a task to a subagent |
| `/health` | System health check |
| `/stop`, `/cancel` | Stop the running task |
| `/reset`, `/help` | Reset the conversation, show help |

## Safety model

HARVIS has real shell access on your machine. Treat it that way.

- Commands matching a destructive keyword list (`rm`, `dd`, `mkfs`, `shutdown`, `reboot`, ...) need confirmation. The GUI shows a prompt. In daemon or Telegram mode nobody can answer, so they are **denied** unless `HARVIS_AUTO_APPROVE=1`.
- That's a keyword filter, not a sandbox. Don't rely on it to stop a confused or hostile model.
- The Telegram bot fails closed: with `TELEGRAM_ALLOWED_USERS` empty, nobody gets in.
- Telegram shell mode runs commands as your user. `HARVIS_SHELL_UNRESTRICTED=1` removes its safety filter entirely.
- Run it under a normal user account, never as root.

## Writing a plugin

Drop a `.py` file into `tools/plugins/`. It's auto-discovered if it defines `PLUGIN_SCHEMA` and `handler`:

```python
# tools/plugins/hello.py
PLUGIN_SCHEMA = {
    "name": "hello",
    "description": "Greet someone by name.",
    "parameters": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Who to greet"}},
        "required": ["name"],
    },
    "examples": ["say hello to Sam"],
}

def handler(args) -> dict:
    return {"success": True, "output": f"Hello, {args.get('name', 'there')}!"}
```

## Project layout

```
main.py             desktop entry point (GUI, voice, hotkey IPC)
config.py           env loading and keys
gui.py, ui/         PyQt6 shell and React interface
brain/              agent harness, router, providers, planner, heartbeat, state
tools/              tool registry, shell, files, browser, desktop vision, plugins/
subagents/          FRIDAY, EDITH, runner (git-worktree isolation)
telegram_bot/       bot, shell mode, message formatting
audio/              STT, TTS, VAD, optional wake word
db/                 SQLite store (the database file lives under ~/.harvis)
scripts/            hotkey trigger
tests/              pytest suite
```

## Platform notes

- Desktop vision (screenshots, AT-SPI widget tree, click grounding, OCR) targets **KDE Plasma on Wayland**. Without it, the shell, web, Telegram and file tools still work.
- The wake word is off by default. Set `WAKEWORD_ENABLED = True` in `main.py` and `pip install openwakeword` to try it.

## Contributing

Issues and PRs are welcome. Keep secrets out of commits (`.env` is gitignored).

## License

[MIT](LICENSE)
