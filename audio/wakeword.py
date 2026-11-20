"""HARVIS Ambient Wake Word Listener.

Layer 1 — openWakeWord (hey_jarvis / alexa / hey_mycroft):
           Ultra-fast ONNX classifier running on raw 80ms audio frames (1280 samples @ 16kHz).
           Near-instant ~50–100ms response time with 0 cloud/STT latency.
           Default triggers: "Hey Jarvis" or "Jarvis".

Layer 2 — Silero VAD (512-sample subchunks) + local faster-whisper STT:
           Catches custom phrases ("HARVIS", "Hey HARVIS", "FRIDAY", "EDITH", "Aviz").
           Runs offline on CPU without any cloud credits.
"""

import os
import re
import time
import logging
import tempfile
import threading

logger = logging.getLogger("harvis.wakeword")

WAKE_WORDS = [
    "harvis", "hey harvis", "hi harvis", "jarvis", "hey jarvis",
    "friday", "edith", "aviz", "alves", "arvis", "hervis", "harves",
    "harvest", "harvies", "haveris", "haris", "avis", "alvis", "harbies",
    "harve", "harbys", "harbiz",
]

OWW_THRESHOLD    = 0.50
TRIGGER_COOLDOWN = 1.5   # seconds between triggers


def _is_wake_word(text: str) -> tuple[bool, str]:
    t = text.strip().lower()
    for ww in WAKE_WORDS:
        if ww in t:
            return True, ww
    for w in t.split():
        w = re.sub(r"[^\w]", "", w)
        if len(w) >= 4 and w.startswith(("harv", "herv", "arv", "aviz", "alv", "harb", "haris")):
            return True, w
    return False, ""


class WakeWordListener:
    def __init__(self, callback, sample_rate: int = 16000):
        self.callback      = callback
        self.sample_rate   = sample_rate
        self.running       = False
        self._thread       = None
        self._last_trigger = 0.0

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info("Ambient Wake Word Listener started (hey_jarvis + HARVIS variants).")

    def stop(self):
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("Wake Word Listener stopped.")

    def _listen_loop(self):
        try:
            import numpy as np
            import sounddevice as sd
            from openwakeword.model import Model as OWWModel
            from audio.vad import SileroVAD, save_wav

            # Initialize openWakeWord (Layer 1)
            try:
                oww = OWWModel()
                logger.info(f"openWakeWord initialized with models: {list(oww.models.keys())}")
            except Exception as e:
                oww = None
                logger.warning(f"openWakeWord init failed ({e}) — running VAD+STT only.")

            # Load Silero VAD (Layer 2)
            vad_path = os.path.join(os.path.dirname(__file__), "silero_vad.onnx")
            vad = SileroVAD(vad_path, sample_rate=self.sample_rate) if os.path.exists(vad_path) else None

            chunk_samples  = 1280  # 80ms @ 16kHz for openWakeWord
            vad_sub_samples = 512  # Silero VAD strict chunk requirement (512 samples / 32ms)
            vad_buffer     = []
            speech_buffer  = []
            silence_chunks = 0
            in_speech      = False

            def audio_callback(indata, frames, time_info, status):
                nonlocal vad_buffer, speech_buffer, silence_chunks, in_speech
                if not self.running:
                    return

                chunk_1d = np.frombuffer(indata, dtype=np.int16)
                if len(chunk_1d) != chunk_samples:
                    return

                # ── Layer 1: openWakeWord (instant ~80ms) ─────────────────────
                if oww is not None:
                    try:
                        prediction = oww.predict(chunk_1d)
                        jarvis_score = float(prediction.get("hey_jarvis", 0.0))
                        if jarvis_score >= OWW_THRESHOLD:
                            now = time.time()
                            if now - self._last_trigger > TRIGGER_COOLDOWN:
                                self._last_trigger = now
                                logger.info(f"⚡ openWakeWord 'hey_jarvis' fired! score={jarvis_score:.2f}")
                                self._fire(clean_prompt="", raw_text="hey jarvis")
                            return
                    except Exception as e:
                        logger.debug(f"openWakeWord predict error: {e}")

                # ── Layer 2: Silero VAD + faster-whisper STT ──────────────────
                if vad is None:
                    return

                chunk_f32 = chunk_1d.astype(np.float32) / 32768.0
                vad_buffer.extend(chunk_f32)

                # Process 512-sample slices for Silero VAD
                while len(vad_buffer) >= vad_sub_samples:
                    v_chunk = np.array(vad_buffer[:vad_sub_samples], dtype=np.float32)
                    vad_buffer = vad_buffer[vad_sub_samples:]

                    try:
                        prob = vad(v_chunk)
                    except Exception:
                        continue

                    if prob > 0.5:
                        in_speech = True
                        speech_buffer.append(v_chunk)
                        silence_chunks = 0
                    elif in_speech:
                        speech_buffer.append(v_chunk)
                        silence_chunks += 1
                        if silence_chunks > 24:   # ~0.75s silence (24 * 32ms)
                            in_speech = False
                            audio = np.concatenate(speech_buffer)
                            speech_buffer = []
                            silence_chunks = 0
                            if len(audio) >= self.sample_rate * 0.4:
                                threading.Thread(
                                    target=self._stt_check,
                                    args=(audio,), daemon=True
                                ).start()

            with sd.RawInputStream(samplerate=self.sample_rate, channels=1,
                                   dtype="int16", blocksize=chunk_samples,
                                   callback=audio_callback):
                while self.running:
                    time.sleep(0.1)

        except Exception as e:
            logger.error(f"Wake word listener error: {e}")
            self.running = False

    def _stt_check(self, audio_samples):
        try:
            import numpy as np
            from audio.vad import save_wav
            from audio.stt import speech_to_text

            with tempfile.NamedTemporaryFile(prefix="wakeword-", suffix=".wav", delete=False) as f:
                tmp_path = f.name

            save_wav(tmp_path, audio_samples, sample_rate=self.sample_rate)
            text = speech_to_text(tmp_path).strip().lower()

            try:
                os.remove(tmp_path)
            except OSError:
                pass

            if not text:
                return

            logger.info(f"[WW-STT] '{text}'")
            matched, ww = _is_wake_word(text)
            if matched:
                now = time.time()
                if now - self._last_trigger > TRIGGER_COOLDOWN:
                    self._last_trigger = now
                    logger.info(f"⚡ HARVIS keyword: '{ww}' in '{text}'")
                    clean = text
                    for w in WAKE_WORDS:
                        clean = clean.replace(w, "").strip()
                    self._fire(clean_prompt=clean, raw_text=text)
        except Exception as e:
            logger.error(f"STT wake word check error: {e}")

    def _fire(self, clean_prompt: str, raw_text: str):
        try:
            self.callback(clean_prompt=clean_prompt, raw_text=raw_text)
        except Exception as e:
            logger.error(f"Wake word callback error: {e}")
