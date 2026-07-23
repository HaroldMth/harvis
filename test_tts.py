import asyncio
import os
import subprocess

async def main():
    text = "Hello, this is a test of the Harvis Text to Speech system."
    out_path = "test_tts.mp3"
    
    print(f"Generating TTS for: '{text}'")
    try:
        import edge_tts
    except ImportError:
        print("Error: edge-tts is not installed yet in the virtual environment. Let's wait for dependencies to finish installing.")
        return

    voice = "en-US-GuyNeural"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_path)
    print(f"TTS saved to {out_path}")
    
    # Playing the audio
    print("Playing using mpv/paplay...")
    if os.path.exists(out_path):
        # Try mpv
        res_mpv = subprocess.run(["which", "mpv"], capture_output=True, text=True)
        if res_mpv.returncode == 0:
            print("Playing via mpv...")
            subprocess.run(["mpv", "--no-video", out_path])
        else:
            # Try paplay
            res_paplay = subprocess.run(["which", "paplay"], capture_output=True, text=True)
            if res_paplay.returncode == 0:
                print("Playing via paplay (converting to WAV first)...")
                wav_path = "test_tts.wav"
                subprocess.run(["ffmpeg", "-y", "-i", out_path, wav_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["paplay", wav_path])
                if os.path.exists(wav_path):
                    os.remove(wav_path)
            else:
                print("Error: Neither mpv nor paplay is installed on this system.")

if __name__ == "__main__":
    asyncio.run(main())
