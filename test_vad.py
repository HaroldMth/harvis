import os
import logging
from audio.vad import VoiceDetector
from audio.stt import speech_to_text

# Configure basic logging to see VAD transitions in the terminal
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def status_cb(state):
    print(f"\n>>> STATE CHANGED TO: {state.upper()} <<<")

def main():
    # Make sure we have a database to log provider status changes (stt uses update_provider_status)
    from db.store import init_db
    init_db()

    print("Initializing Voice Detector (loading Silero VAD ONNX model)...")
    try:
        detector = VoiceDetector()
    except Exception as e:
        print(f"Failed to initialize Voice Detector: {e}")
        return
        
    out_path = "vad_test.wav"
    print("\nListening... START SPEAKING when ready! The system will automatically cut when you stop speaking.")
    
    recorded_file = detector.listen_and_record(
        out_path=out_path,
        status_callback=status_cb,
        timeout_seconds=15
    )
    
    if recorded_file and os.path.exists(recorded_file) and os.path.getsize(recorded_file) > 0:
        print(f"\nRecording captured successfully! File size: {os.path.getsize(recorded_file)} bytes")
        print("Transcribing...")
        try:
            text = speech_to_text(recorded_file)
            print(f"\nResulting Transcription:\n>>> {text}")
        except Exception as e:
            print(f"Error during transcription: {e}")
        finally:
            if os.path.exists(recorded_file):
                os.remove(recorded_file)
    else:
        print("\nRecording finished but no speech was detected/saved.")

if __name__ == "__main__":
    main()
