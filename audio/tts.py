"""HARVIS High-Reliability Multi-Tier TTS Engine.

Tier 1: Microsoft Edge Neural AI Voice (Studio Quality, natural human voice)
Tier 2: Google Official TTS API (High Speed, 100% Uptime fallback)
Tier 3: Local espeak-ng (Instant Offline fallback)
"""

import os
import re
import sys
import shutil
import time
import urllib.parse
import logging
import subprocess
import tempfile
import threading
import requests

logger = logging.getLogger("harvis.tts")

_active_player = None
_player_lock = threading.Lock()
_HAS_ESPEAK = shutil.which("espeak-ng") is not None
_HAS_EDGE_TTS = False

try:
    import asyncio
    import edge_tts
    _HAS_EDGE_TTS = True
except ImportError:
    _HAS_EDGE_TTS = False


def clean_text_for_tts(text: str) -> str:
    """Strip markdown/code blocks/emojis before sending to TTS."""
    if not text:
        return ""
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r':[a-zA-Z0-9_]+:', '', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*---+\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = text.replace('|', ' ')
    text = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251]+', '', text)
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def stop_tts():
    """Immediately stop active audio playback."""
    global _active_player
    with _player_lock:
        if _active_player:
            try:
                logger.info("Stopping active TTS player...")
                _active_player.terminate()
                _active_player.wait(timeout=1.0)
            except Exception as e:
                logger.warning(f"Error stopping TTS: {e}")
            finally:
                _active_player = None


def _play_audio_file(file_path: str) -> bool:
    """Play an audio file via ffplay/mpv/paplay."""
    global _active_player

    player_cmd = None
    if shutil.which("ffplay"):
        player_cmd = ["ffplay", "-nodisp", "-autoexit", file_path]
    elif shutil.which("mpv"):
        player_cmd = ["mpv", "--no-video", file_path]
    elif shutil.which("paplay"):
        player_cmd = ["paplay", file_path]
    else:
        logger.error("No audio player found (ffplay/mpv/paplay missing).")
        return False

    try:
        proc = subprocess.Popen(
            player_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        with _player_lock:
            _active_player = proc

        while proc and proc.poll() is None:
            time.sleep(0.05)

        with _player_lock:
            if _active_player is proc:
                _active_player = None
        return True
    except Exception as e:
        logger.error(f"Playback failed: {e}")
        return False


def _play_edge_tts(text: str, voice: str = "en-US-GuyNeural") -> bool:
    """Tier 1: Microsoft Edge Neural AI Voice."""
    if not _HAS_EDGE_TTS:
        return False
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)

        async def _gen():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(tmp_path)

        asyncio.run(_gen())

        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 100:
            logger.info(f"Playing Edge Neural TTS ({voice})...")
            res = _play_audio_file(tmp_path)
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return res
        return False
    except Exception as e:
        logger.warning(f"Edge TTS failed: {e}")
        return False


def _play_google_tts(text: str) -> bool:
    """Tier 2: Google Official TTS API."""
    try:
        encoded = urllib.parse.quote(text[:300])  # Google limit ~200-300 chars per chunk
        url = f"https://translate.google.com/translate_tts?ie=UTF-8&q={encoded}&tl=en&client=tw-ob"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200 and len(r.content) > 500:
            fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            with open(tmp_path, "wb") as f:
                f.write(r.content)

            logger.info("Playing Google Official TTS...")
            res = _play_audio_file(tmp_path)
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return res
        return False
    except Exception as e:
        logger.warning(f"Google TTS failed: {e}")
        return False


def _play_espeak(text: str) -> bool:
    """Tier 3: Local espeak-ng offline fallback."""
    if not _HAS_ESPEAK:
        return False
    try:
        logger.info("Playing espeak-ng local fallback...")
        proc = subprocess.Popen(
            ["espeak-ng", "-v", "en-us", "-s", "160", "-p", "50", text],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        with _player_lock:
            _active_player = proc
        while proc and proc.poll() is None:
            time.sleep(0.05)
        with _player_lock:
            if _active_player is proc:
                _active_player = None
        return True
    except Exception as e:
        logger.warning(f"espeak-ng failed: {e}")
        return False


def text_to_speech(text: str, out_path: str = "reply.mp3") -> bool:
    """
    Multi-Tier TTS Engine:
    1. Microsoft Edge Neural AI (en-US-GuyNeural)
    2. Google Official TTS
    3. espeak-ng local fallback
    """
    stop_tts()

    clean_text = clean_text_for_tts(text)
    if not clean_text:
        return False

    # Truncate extremely long responses for TTS (max 400 chars spoken)
    spoken_text = clean_text[:400]

    # Try Tier 1: Microsoft Edge Neural TTS
    if _play_edge_tts(spoken_text):
        return True

    # Try Tier 2: Google Official TTS
    if _play_google_tts(spoken_text):
        return True

    # Try Tier 3: Local espeak-ng
    return _play_espeak(spoken_text)


def unload():
    stop_tts()
