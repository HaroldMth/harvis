"""ElevenLabs API TTS with fallback to local Kokoro.

Uses ElevenLabs default voice (Rachel - JBFqnCBsd6RMkjVDRZzb).
Model: eleven_flash_v2_5 - 75ms latency, cheapest tier
"""

import os
import re
import time
import threading
import logging
import subprocess
import tempfile

logger = logging.getLogger("harvis.tts")

# Configuration
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_MODEL = os.environ.get("HARVIS_TTS_MODEL", "eleven_flash_v2_5")
OUTPUT_FORMAT = "mp3_44100_128"

# Kokoro fallback config
KOKORO_MODEL = os.environ.get("HARVIS_KOKORO_MODEL", "kokoro-v1.0.onnx")
KOKORO_VOICES = os.environ.get("HARVIS_KOKORO_VOICES", "voices-v1.0.bin")
KOKORO_VOICE = os.environ.get("HARVIS_KOKORO_VOICE", "am_eric")
KOKORO_SPEED = float(os.environ.get("HARVIS_KOKORO_SPEED", "1.0"))
KOKORO_LANG = os.environ.get("HARVIS_KOKORO_LANG", "en-us")
KEEP_ALIVE_SECONDS = 60

# Kokoro lazy-load state
_kokoro = None
_kokoro_last_used = 0
_kokoro_lock = threading.Lock()
_kokoro_unload_timer = None


def _load_kokoro():
    """Lazy-load Kokoro as fallback."""
    global _kokoro, _kokoro_last_used
    with _kokoro_lock:
        if _kokoro is not None:
            _kokoro_last_used = time.time()
            return _kokoro
        logger.info("Loading kokoro-onnx fallback...")
        try:
            from kokoro_onnx import Kokoro
            _kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
            _kokoro_last_used = time.time()
            logger.info("Kokoro fallback loaded.")
            _schedule_kokoro_unload()
            return _kokoro
        except Exception as e:
            logger.error(f"Kokoro fallback failed: {e}")
            return None


def _schedule_kokoro_unload():
    global _kokoro_unload_timer
    if _kokoro_unload_timer:
        _kokoro_unload_timer.cancel()
    _kokoro_unload_timer = threading.Timer(KEEP_ALIVE_SECONDS, _unload_kokoro)
    _kokoro_unload_timer.daemon = True
    _kokoro_unload_timer.start()


def _unload_kokoro():
    global _kokoro, _kokoro_unload_timer
    with _kokoro_lock:
        if _kokoro and time.time() - _kokoro_last_used >= KEEP_ALIVE_SECONDS:
            logger.info("Unloading Kokoro fallback.")
            del _kokoro
            _kokoro = None


def clean_text_for_tts(text: str) -> str:
    """Strip markdown/icons/emojis before sending to API."""
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


def _elevenlabs_speak(text: str) -> bool:
    """Use ElevenLabs API to synthesize and play speech."""
    if not ELEVENLABS_API_KEY:
        logger.warning("No ELEVENLABS_API_KEY set, skipping API TTS.")
        return False
    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        logger.info(f"ElevenLabs TTS: voice=JBFqnCBsd6RMkjVDRZzb, model={ELEVENLABS_MODEL}")
        audio_stream = client.text_to_speech.convert(
            voice_id="JBFqnCBsd6RMkjVDRZzb",
            text=text,
            model_id=ELEVENLABS_MODEL,
            output_format=OUTPUT_FORMAT,
        )
        fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        with open(tmp_path, "wb") as f_out:
            for chunk in audio_stream:
                f_out.write(chunk)
        logger.info(f"Audio saved to {tmp_path}, playing...")
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", tmp_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        os.remove(tmp_path)
        return True
    except Exception as e:
        logger.error(f"ElevenLabs TTS failed: {e}")
        return False


def _kokoro_speak(text: str) -> bool:
    """Fallback to local Kokoro TTS."""
    kokoro = _load_kokoro()
    if kokoro is None:
        logger.error("Kokoro fallback not available.")
        return False
    try:
        import sounddevice as sd
        logger.info(f"Kokoro fallback TTS: voice={KOKORO_VOICE}")
        samples, sample_rate = kokoro.create(
            text,
            voice=KOKORO_VOICE,
            speed=KOKORO_SPEED,
            lang=KOKORO_LANG,
        )
        sd.play(samples, sample_rate)
        sd.wait()
        return True
    except Exception as e:
        logger.error(f"Kokoro fallback failed: {e}")
        return False


def text_to_speech(text: str, out_path: str = "reply.mp3") -> bool:
    """
    Main entry point: clean text, try ElevenLabs API, fallback to Kokoro.
    Returns True on success.
    """
    clean_text = clean_text_for_tts(text)
    if not clean_text:
        logger.warning("No speakable text after cleaning.")
        return False
    if _elevenlabs_speak(clean_text):
        return True
    logger.info("Falling back to Kokoro local TTS...")
    return _kokoro_speak(clean_text)


def unload():
    """Force unload Kokoro fallback immediately."""
    global _kokoro, _kokoro_unload_timer
    with _kokoro_lock:
        if _kokoro_unload_timer:
            _kokoro_unload_timer.cancel()
            _kokoro_unload_timer = None
        if _kokoro is not None:
            logger.info("Force-unloading Kokoro fallback.")
            del _kokoro
            _kokoro = None