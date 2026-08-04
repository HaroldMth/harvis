import sounddevice as sd
import numpy as np
import ollama
from faster_whisper import WhisperModel
from kokoro_onnx import Kokoro

# --- CONFIGURATION ---
MIC_DEVICE = 11  # Pipewire for Arch Linux
STT_MODEL = "base.en"
TTS_MODEL = "kokoro-v1.0.onnx"
TTS_VOICES = "voices-v1.0.bin"
HARVIS_VOICE = "am_eric"
LLM_MODEL = "llama3.2:3b"

# Force sounddevice to use your PipeWire mic
sd.default.device = (MIC_DEVICE, None)

print("⏳ Booting HARVIS subsystems...")
print("1/3 Loading Speech-to-Text (Faster-Whisper)...")
stt = WhisperModel(STT_MODEL, device="cpu", compute_type="int8", local_files_only=True)

print("2/3 Loading Text-to-Speech (Kokoro)...")
tts = Kokoro(TTS_MODEL, TTS_VOICES)

print(f"3/3 LLM Engine connected (Ollama: {LLM_MODEL})")
print("\n✅ HARVIS IS ONLINE.")
print("="*40)

def listen(duration=5, sample_rate=16000):
    """Records audio from the microphone."""
    print(f"\n🎤 Listening... ({duration}s)")
    audio = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=1, dtype='float32')
    sd.wait()
    return np.squeeze(audio)

def talk(text):
    """Generates and plays TTS."""
    print(f"🗣️ HARVIS: {text}")
    samples, sample_rate = tts.create(text, voice=HARVIS_VOICE, speed=1.0, lang="en-us")
    sd.play(samples, sample_rate)
    sd.wait()

def main_loop():
    # Greet the user on boot
    talk("System online. Good evening, Harold. I am ready.")
    
    while True:
        # 1. Listen
        audio_data = listen(duration=5)
        
        # Check volume to ensure we aren't processing pure silence
        volume = np.sqrt(np.mean(audio_data**2))
        if volume < 0.005:
            continue
            
        print("🧠 Processing audio...")
        
        # 2. Transcribe
        segments, _ = stt.transcribe(audio_data, beam_size=5, vad_filter=False, language="en")
        user_text = " ".join([s.text for s in segments]).strip()
        
        if not user_text:
            continue
            
        print(f"👤 Harold: {user_text}")
        
        # Exit command
        if user_text.lower().strip('.!?,') in ["stop", "exit", "quit", "goodbye", "shut down"]:
            talk("Shutting down subsystems. Goodbye, Harold.")
            break

        # 3. Think (Ollama)
        print("🤖 Thinking...")
        # Add a system prompt to keep HARVIS concise and in character
        messages = [
            {'role': 'system', 'content': 'You are HARVIS, a highly efficient, witty, and concise AI assistant built by Harold. Keep your answers under 2 sentences.'},
            {'role': 'user', 'content': user_text}
        ]
        
        response = ollama.chat(model=LLM_MODEL, messages=messages)
        reply_text = response['message']['content']
        
        # 4. Speak
        talk(reply_text)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\n\n🛑 Force quit detected. HARVIS offline.")
