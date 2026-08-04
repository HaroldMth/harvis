import sounddevice as sd
from kokoro_onnx import Kokoro

# If you get no sound out of your speakers, you might need to force your PipeWire output device.
# (Uncomment the line below and change '11' if needed based on your sounddevice output list)
# sd.default.device = (None, 11) 

print("⏳ Loading Kokoro v1.0 TTS Model...")
kokoro = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")

text = "Hello Harold! Speech to text is working, and now text to speech is fully operational."

print(f"🧠 Synthesizing speech for: '{text}'")
samples, sample_rate = kokoro.create(
    text,
    voice="af_sarah", # You can change this to "am_adam" for a male voice or "af_heart"
    speed=1.0,
    lang="en-us"
)

print("🔊 Playing audio...")
sd.play(samples, sample_rate)
sd.wait()
print("✅ TTS Test Complete!")
