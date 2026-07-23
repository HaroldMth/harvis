import os
import subprocess
import numpy as np
import onnxruntime
import time

def main():
    model_path = "/home/harold/HARVIS/audio/silero_vad.onnx"
    if not os.path.exists(model_path):
        print("Model not found!")
        return

    print("Loading VAD model...")
    session = onnxruntime.InferenceSession(model_path)
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((1, 64), dtype=np.float32)
    sr = np.array(16000, dtype=np.int64)

    # Spawn arecord
    cmd = [
        "arecord",
        "-q",
        "-t", "raw",
        "-f", "S16_LE",
        "-r", "16000",
        "-c", "1"
    ]
    
    print("Starting arecord... Speak into the microphone!")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    
    chunk_size = 512
    bytes_per_chunk = chunk_size * 2
    
    start_time = time.time()
    chunks_read = 0
    
    try:
        # Read for 5 seconds
        while time.time() - start_time < 5:
            data = proc.stdout.read(bytes_per_chunk)
            if not data or len(data) < bytes_per_chunk:
                print("No data or EOF")
                break
                
            chunks_read += 1
            audio_chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            
            # Run VAD
            input_data = audio_chunk[np.newaxis, :]
            input_concat = np.concatenate([context, input_data], axis=1)
            inputs = {
                "input": input_concat,
                "state": state,
                "sr": sr
            }
            out, new_state = session.run(None, inputs)
            state = new_state
            context = input_concat[:, -64:]
            prob = float(out[0, 0])
            
            # Print if probability is above a tiny threshold or every 20 chunks
            max_val = np.abs(audio_chunk).max()
            if prob > 0.05 or chunks_read % 20 == 0:
                print(f"Chunk {chunks_read:03d} | Prob: {prob:.4f} | Max Amp: {max_val:.4f}")
                
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        proc.wait()
        print(f"\nFinished. Total chunks read: {chunks_read}")

if __name__ == "__main__":
    main()
