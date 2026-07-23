import os
import subprocess
from audio.stt import speech_to_text

def record_audio(out_path="test_voice.wav", duration=4):
    print(f"\nRecording audio for {duration} seconds... PLEASE SPEAK!")
    cmd = [
        "arecord",
        "-d", str(duration),
        "-f", "S16_LE",
        "-r", "16000",
        "-c", "1",
        out_path
    ]
    # Run the recording synchronously
    try:
        subprocess.run(cmd, check=True)
        print("Recording finished.")
    except Exception as e:
        print(f"Failed to record audio via arecord: {e}")

def main():
    # Make sure we have a database to log provider status changes (stt uses update_provider_status)
    from db.store import init_db
    init_db()

    out_path = "test_voice.wav"
    
    # 1. Record a voice sample
    record_audio(out_path, duration=4)
    
    # 2. Convert voice to text
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        print("\nTranscribing using STT chain (Pollinations -> Groq)...")
        try:
            text = speech_to_text(out_path)
            print(f"\nResulting Transcription:\n>>> {text}")
        except Exception as e:
            print(f"Error during transcription: {e}")
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)
    else:
        print("Error: No audio was recorded.")

if __name__ == "__main__":
    main()
