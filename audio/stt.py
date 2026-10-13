"""STT using local faster-whisper (base.en, INT8, CPU).

Model is lazy-loaded on first use and kept in memory for 120s,
then auto-unloaded to free RAM. Falls back to Puter API if model fails.
"""

import os
import time
import logging
import threading

logger = logging.getLogger("harvis.stt")

MODEL_SIZE      = os.environ.get("HARVIS_STT_MODEL",   "base.en")
DEVICE          = os.environ.get("HARVIS_STT_DEVICE",  "cpu")
COMPUTE_TYPE    = os.environ.get("HARVIS_STT_COMPUTE", "int8")
KEEP_ALIVE_SECS = 120

_model        = None
_last_used    = 0.0
_lock         = threading.Lock()
_unload_timer = None


# ── Model lifecycle ───────────────────────────────────────────────────────────

def _load_model():
    global _model, _last_used, _unload_timer
    with _lock:
        if _model is not None:
            _last_used = time.time()
            return _model
        logger.info(f"Loading faster-whisper: {MODEL_SIZE} ({DEVICE}/{COMPUTE_TYPE})")
        from faster_whisper import WhisperModel
        _model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE,
                              local_files_only=True)
        _last_used = time.time()
        logger.info("STT model ready.")
        _reset_unload_timer()
        return _model


def _reset_unload_timer():
    global _unload_timer
    if _unload_timer:
        _unload_timer.cancel()
    _unload_timer = threading.Timer(KEEP_ALIVE_SECS, _unload_model)
    _unload_timer.daemon = True
    _unload_timer.start()


def _unload_model():
    global _model, _unload_timer
    with _lock:
        if _model is None:
            return
        elapsed = time.time() - _last_used
        if elapsed >= KEEP_ALIVE_SECS:
            logger.info("Unloading STT model to free RAM.")
            del _model
            _model = None
        else:
            _unload_timer = threading.Timer(KEEP_ALIVE_SECS - elapsed, _unload_model)
            _unload_timer.daemon = True
            _unload_timer.start()


def unload():
    """Force-unload the model immediately."""
    global _model, _unload_timer
    with _lock:
        if _unload_timer:
            _unload_timer.cancel()
            _unload_timer = None
        if _model is not None:
            logger.info("Force-unloading STT model.")
            del _model
            _model = None


import requests


def _groq_whisper_transcribe(audio_path: str) -> str:
    """Transcribe audio via Groq Whisper API (whisper-large-v3-turbo)."""
    try:
        from config import GROQ_API_KEY
        if not GROQ_API_KEY:
            return ""
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
        with open(audio_path, "rb") as f:
            files = {"file": (os.path.basename(audio_path), f)}
            data = {"model": "whisper-large-v3-turbo", "language": "en"}
            resp = requests.post(url, headers=headers, files=files, data=data, timeout=15)
        if resp.status_code == 200:
            text = resp.json().get("text", "").strip()
            if text:
                logger.info(f"Groq Whisper STT transcribed: '{text}'")
                return text
        logger.warning(f"Groq Whisper STT status {resp.status_code}: {resp.text}")
        return ""
    except Exception as e:
        logger.warning(f"Groq Whisper STT failed: {e}")
        return ""


# ── Public API ────────────────────────────────────────────────────────────────

def speech_to_text(audio_path: str = None) -> str:
    """Transcribe an audio file to text using Groq Whisper (ultra-fast) with local faster-whisper fallback."""
    if not audio_path or not os.path.exists(audio_path):
        logger.error("speech_to_text: no valid audio_path provided.")
        return ""

    # 1. Try Groq Whisper (blazing fast, ~300ms)
    groq_text = _groq_whisper_transcribe(audio_path)
    if groq_text:
        return groq_text

    # 2. Fallback to local faster-whisper
    try:
        model = _load_model()
        logger.info(f"Transcribing locally via faster-whisper: {audio_path}")
        segments, _ = model.transcribe(
            audio_path,
            beam_size=5,
            vad_filter=True,
            language="en",
        )
        text = " ".join(s.text for s in segments).strip()
        logger.info(f"Transcribed locally: '{text}'")
        _reset_unload_timer()
        return text
    except Exception as e:
        logger.error(f"faster-whisper transcription failed: {e}")
        return _puter_fallback(audio_path)


def _puter_fallback(audio_path: str) -> str:
    """Fallback to Puter.js API if local model is unavailable."""
    token = os.environ.get("PUTER_AUTH_TOKEN")
    if not token:
        logger.warning("No PUTER_AUTH_TOKEN — cannot use Puter fallback.")
        return ""
    import subprocess
    script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "puter_stt.mjs")
    if not os.path.exists(script):
        return ""
    try:
        env = os.environ.copy()
        env["PUTER_AUTH_TOKEN"] = token
        result = subprocess.run(
            ["node", script, audio_path],
            env=env, capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            text = result.stdout.strip()
            logger.info(f"Puter fallback transcribed: '{text}'")
            return text
        else:
            logger.error(f"Puter fallback failed: {result.stderr.strip()}")
            return ""
    except Exception as e:
        logger.error(f"Puter fallback error: {e}")
        return ""
