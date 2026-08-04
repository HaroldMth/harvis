"""Local STT using faster-whisper with lazy loading and keep-alive.

Model: base.en (74M params, ~142MB FP16, ~71MB INT8)
Loaded on first use, kept alive for 60s after last use, then unloaded.
"""

import os
import time
import threading
import logging
import tempfile
import sounddevice as sd
import numpy as np
from faster_whisper import WhisperModel

logger = logging.getLogger("harvis.stt")

# Configuration
MODEL_SIZE = os.environ.get("HARVIS_STT_MODEL", "base.en")
DEVICE = os.environ.get("HARVIS_STT_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("HARVIS_STT_COMPUTE", "int8")
SAMPLE_RATE = 16000
RECORD_DURATION = 5  # seconds per voice capture
KEEP_ALIVE_SECONDS = 60

# Global state
_model = None
_last_used = 0
_lock = threading.Lock()
_unload_timer = None


def _load_model():
    """Lazy-load the Whisper model."""
    global _model, _last_used
    with _lock:
        if _model is not None:
            _last_used = time.time()
            return _model

        logger.info(f"Loading faster-whisper model: {MODEL_SIZE} ({DEVICE}, {COMPUTE_TYPE})")
        try:
            _model = WhisperModel(
                MODEL_SIZE,
                device=DEVICE,
                compute_type=COMPUTE_TYPE,
                local_files_only=True,
            )
            _last_used = time.time()
            logger.info("STT model loaded successfully.")
            _schedule_unload()
            return _model
        except Exception as e:
            logger.error(f"Failed to load STT model: {e}")
            raise


def _schedule_unload():
    """Schedule model unload after keep-alive timeout."""
    global _unload_timer
    if _unload_timer is not None:
        _unload_timer.cancel()
    _unload_timer = threading.Timer(KEEP_ALIVE_SECONDS, _unload_model)
    _unload_timer.daemon = True
    _unload_timer.start()


def _unload_model():
    """Unload model to free RAM."""
    global _model, _unload_timer
    with _lock:
        if _model is not None:
            elapsed = time.time() - _last_used
            if elapsed >= KEEP_ALIVE_SECONDS:
                logger.info("Unloading STT model to free RAM.")
                del _model
                _model = None
            else:
                # Reschedule
                _unload_timer = threading.Timer(
                    KEEP_ALIVE_SECONDS - elapsed, _unload_model
                )
                _unload_timer.daemon = True
                _unload_timer.start()


def record_audio(duration=RECORD_DURATION, samplerate=SAMPLE_RATE):
    """Record audio from microphone."""
    logger.info(f"Recording {duration}s of audio...")
    try:
        audio = sd.rec(
            int(duration * samplerate),
            samplerate=samplerate,
            channels=1,
            dtype="float32",
        )
        sd.wait()
        audio = np.squeeze(audio)
        rms = np.sqrt(np.mean(audio**2))
        logger.info(f"Recording complete. RMS level: {rms:.6f}")
        return audio
    except Exception as e:
        logger.error(f"Recording failed: {e}")
        raise


def transcribe_audio(audio_data: np.ndarray, samplerate=SAMPLE_RATE) -> str:
    """Transcribe audio data to text."""
    model = _load_model()
    logger.info("Transcribing...")
    try:
        segments, _ = model.transcribe(
            audio_data,
            beam_size=5,
            vad_filter=False,
            language="en",
            sample_rate=samplerate,
        )
        text = " ".join([s.text for s in segments]).strip()
        logger.info(f"Transcribed: '{text}'")
        return text
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise


def speech_to_text(audio_path: str = None) -> str:
    """
    Main entry point. If audio_path is given, transcribe from file.
    Otherwise, record from microphone and transcribe.
    """
    if audio_path and os.path.exists(audio_path):
        logger.info(f"Transcribing file: {audio_path}")
        model = _load_model()
        segments, _ = model.transcribe(
            audio_path,
            beam_size=5,
            vad_filter=False,
            language="en",
        )
        return " ".join([s.text for s in segments]).strip()

    # Record + transcribe
    audio = record_audio()
    return transcribe_audio(audio)


def unload():
    """Force unload the model immediately."""
    global _model, _unload_timer
    with _lock:
        if _unload_timer:
            _unload_timer.cancel()
            _unload_timer = None
        if _model is not None:
            logger.info("Force-unloading STT model.")
            del _model
            _model = None
