import sounddevice as sd
import numpy as np
from faster_whisper import WhisperModel

# Force PipeWire (Device 11) or Pulse (Device 12)
sd.default.device = (11, None)

print("⏳ Loading Faster-Whisper model...")
model = WhisperModel("base.en", device="cpu", compute_type="int8", local_files_only=True)

DURATION = 5  # Seconds to record
SAMPLE_RATE = 16000  # 16kHz for Whisper

print(f"\n🎤 Recording for {DURATION} seconds... Speak clearly now!")
audio_data = sd.rec(int(DURATION * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype='float32')
sd.wait()

# 1. Flatten 2D array (samples, 1) -> 1D array (samples,)
audio_data = np.squeeze(audio_data)

# 2. Check recorded signal volume
volume = np.sqrt(np.mean(audio_data**2))
print(f"✅ Recording complete. Captured Audio Level (RMS): {volume:.6f}")

print("Transcribing...")
# 3. vad_filter=False prevents Silero VAD from discarding quiet audio as silence
segments, _ = model.transcribe(
    audio_data, 
    beam_size=5, 
    vad_filter=False, 
    language="en"
)

transcript = " ".join([s.text for s in segments]).strip()

print(f"\n📝 Recognized Speech: \"{transcript}\"\n")
