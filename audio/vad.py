import os
import subprocess
import logging
import wave
from collections import deque

logger = logging.getLogger("harvis.vad")

# Check if numpy and onnxruntime are installed
try:
    import numpy as np
    import onnxruntime
except ImportError:
    np = None
    onnxruntime = None

def save_wav(file_path, float32_samples, sample_rate=16000):
    """
    Saves float32 PCM samples in range [-1.0, 1.0] to a 16-bit PCM WAV file.
    """
    if np is None:
        raise RuntimeError("numpy is not imported")
    int16_samples = (float32_samples * 32767.0).astype(np.int16)
    with wave.open(file_path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 2 bytes for 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(int16_samples.tobytes())

class SileroVAD:
    def __init__(self, model_path, sample_rate=16000):
        if onnxruntime is None:
            raise RuntimeError("onnxruntime is not installed. Please run: pip install onnxruntime numpy")
        self.session = onnxruntime.InferenceSession(model_path)
        self.sample_rate = sample_rate
        # Initialize recurrent state to zeros of shape (2, 1, 128)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        # sr input is scalar 16000
        self._sr = np.array(self.sample_rate, dtype=np.int64)
        # Initialize context size (64 samples for 16kHz, 32 for 8kHz)
        self.context_size = 64 if self.sample_rate == 16000 else 32
        self._context = np.zeros((1, self.context_size), dtype=np.float32)

    def reset_states(self):
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self.context_size), dtype=np.float32)

    def __call__(self, chunk):
        # chunk shape: [chunk_size]
        # input shape must be [1, chunk_size]
        x = chunk[np.newaxis, :]
        # Prepend context to the input chunk (e.g. making it shape [1, 576] for 16kHz)
        x_concat = np.concatenate([self._context, x], axis=1)
        
        inputs = {
            "input": x_concat,
            "state": self._state,
            "sr": self._sr
        }
        
        out, new_state = self.session.run(None, inputs)
        
        # Save updated recurrent state for the next call
        self._state = new_state
        # Save updated context
        self._context = x_concat[:, -self.context_size:]
        
        # Return probability (out is of shape [1, 1])
        return float(out[0, 0])

class VoiceDetector:
    def __init__(self, sample_rate=16000, chunk_size=512):
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size  # 512 samples is 32ms at 16kHz
        
        # Path to local onnx model
        self.model_path = os.path.join(os.path.dirname(__file__), "silero_vad.onnx")
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"VAD model not found at {self.model_path}. Please download it first.")
            
        self.vad = SileroVAD(self.model_path, self.sample_rate)
        self.arecord_proc = None

    def start_recording(self):
        """
        Starts the arecord subprocess.
        Format: Raw PCM, 16-bit Little Endian, 16000Hz, Mono
        """
        cmd = [
            "arecord",
            "-q",
            "-t", "raw",
            "-f", "S16_LE",
            "-r", str(self.sample_rate),
            "-c", "1"
        ]
        logger.info("Starting microphone recording via arecord...")
        self.arecord_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )

    def stop_recording(self):
        if self.arecord_proc:
            self.arecord_proc.terminate()
            self.arecord_proc.wait()
            self.arecord_proc = None
            logger.info("Microphone recording stopped.")

    def listen_and_record(self, out_path="recording.wav", status_callback=None, timeout_seconds=15):
        """
        Listens to the microphone stream continuously.
        Returns the path to the recorded WAV file, or None if no speech was detected.
        """
        self.start_recording()
        self.vad.reset_states()
        
        # Ring buffer for pre-speech audio (~320ms padding)
        pre_speech_buffer = deque(maxlen=10)
        # Active speech buffer for full recording
        speech_chunks = []
        is_speaking = False
        
        # Bytes per chunk: 512 samples * 2 bytes = 1024 bytes
        bytes_per_chunk = self.chunk_size * 2
        
        # Max chunks to prevent infinite recording
        max_chunks = int((timeout_seconds * self.sample_rate) / self.chunk_size)
        chunks_processed = 0
        
        # Threshold constants
        speech_threshold = 0.35
        # 1000ms silence count = 1000 / 32 = ~32 chunks
        max_silence_chunks = 32
        silence_chunks_count = 0
        
        if status_callback:
            status_callback("Listening")
            
        logger.info("Listening for speech...")
        
        try:
            while chunks_processed < max_chunks:
                # Read raw PCM chunk safely with local reference
                proc = self.arecord_proc
                if not proc:
                    break
                try:
                    data = proc.stdout.read(bytes_per_chunk)
                except (AttributeError, ValueError, OSError):
                    break
                if not data or len(data) < bytes_per_chunk:
                    break
                
                chunks_processed += 1
                
                # Convert bytes to float32 NumPy array
                audio_chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                
                # Run VAD state machine chunk by chunk
                prob = self.vad(audio_chunk)
                
                if not is_speaking:
                    if prob >= speech_threshold:
                        logger.info(f"Speech start detected (prob={prob:.2f})!")
                        is_speaking = True
                        if status_callback:
                            status_callback("Recording")
                        # Prepopulate with history padding
                        speech_chunks.extend(list(pre_speech_buffer))
                        pre_speech_buffer.clear()
                        speech_chunks.append(audio_chunk)
                    else:
                        pre_speech_buffer.append(audio_chunk)
                else:
                    speech_chunks.append(audio_chunk)
                    if prob < speech_threshold:
                        silence_chunks_count += 1
                        if silence_chunks_count >= max_silence_chunks:
                            logger.info("Silence duration exceeded threshold. Stopping recording.")
                            break
                    else:
                        silence_chunks_count = 0
                        
        except Exception as e:
            logger.error(f"Error in VAD loop: {e}")
        finally:
            self.stop_recording()
            
        if speech_chunks:
            # Concatenate all float32 chunks
            full_audio = np.concatenate(speech_chunks)
            save_wav(out_path, full_audio, self.sample_rate)
            logger.info(f"Speech saved to {out_path}")
            return out_path
            
        logger.info("No speech detected.")
        return None
