import asyncio
import io
import wave
import os
import subprocess
import math
import numpy as np
from shazamio import Shazam
import logger_utils

# Import our separated utilities and ML Engine
import audio_utils
from music_detector import MusicDetector

# Guarantee console won't crash on foreign characters
audio_utils.force_utf8_console()

# Fetch our standardized native logger
logger = logger_utils.get_logger("Audio")

class NowPlayingRecognizer:
    def __init__(self, record_seconds=8, software_gain=1.8):
        """Initializes the ML Detector, Shazam client, and Native ALSA Hardware."""
        logger.info("Initializing NowPlayingRecognizer...")
        self.record_seconds = record_seconds
        
        # INCREASE THIS VALUE to digitally boost microphone gain (e.g., 2.0 = 200% volume)
        self.software_gain = software_gain
        
        # --- MIC DEBUGGING TOGGLE ---
        # Set to True to write the last-captured audio to 'debug_mic.wav'
        self.debug_mic = True
        
        self.channels = 1
        self.sample_width = 2  # 16-bit PCM is 2 bytes
        
        self.detector = MusicDetector()
        self.shazam = Shazam()
        
        # Completely bypass PyAudio. Hook directly into the OS kernel device strings.
        self.hw_device = self._auto_detect_alsa_mic()
        logger.success("Audio Engine subsystems loaded successfully.")

    def _auto_detect_alsa_mic(self):
        """Scans native Linux ALSA devices for a USB microphone."""
        try:
            output = subprocess.check_output(["arecord", "-l"], text=True)
            for line in output.split('\n'):
                # Look for USB or standard Microphones
                if "card" in line and ("USB" in line or "Microphone" in line or "Mic" in line):
                    try:
                        # Parse hardware string (e.g., "card 1..., device 0..." -> "plughw:1,0")
                        card = line.split("card ")[1].split(":")[0]
                        dev = line.split("device ")[1].split(":")[0]
                        hw_string = f"plughw:{card},{dev}"
                        
                        dev_name = line.split(":")[1].split(",")[0].strip()
                        logger.success(f"Auto-selected Native ALSA Microphone: {hw_string} ({dev_name})")
                        return hw_string
                    except IndexError:
                        continue
                        
            # Safe Fallback: Grab the very first capture card available if no USB matches
            for line in output.split('\n'):
                if "card" in line:
                    try:
                        card = line.split("card ")[1].split(":")[0]
                        dev = line.split("device ")[1].split(":")[0]
                        hw_string = f"plughw:{card},{dev}"
                        logger.success(f"Selected fallback ALSA Microphone: {hw_string}")
                        return hw_string
                    except IndexError:
                        continue
        except Exception as e:
            logger.warning(f"Could not scan ALSA devices: {e}")
        
        logger.warning("Falling back to ALSA 'default' capture device.")
        return "default"

    def _record_raw_audio(self, seconds, target_rate=48000):
        """Captures audio using a completely detached, GIL-free Linux OS process."""
        # Build the native C-level ALSA command
        cmd = [
            "arecord", 
            "-D", self.hw_device,           # The specific ALSA hardware device (e.g., plughw:1,0)
            "-c", str(self.channels),       # Channels (1 = Mono)
            "-r", str(target_rate),         # Sample Rate (48000)
            "-f", "S16_LE",                 # Format (16-bit Little Endian)
            "-t", "raw",                    # Output raw binary PCM bytes (no headers)
            "-d", str(int(math.ceil(seconds))), # Duration to record
            "-q"                            # Quiet mode (no text output)
        ]
        
        try:
            # Launch the OS process. 
            # Python completely yields control here and waits for the OS to finish the command.
            result = subprocess.run(cmd, capture_output=True, timeout=seconds + 2)
            raw_audio = result.stdout
            
        except subprocess.TimeoutExpired:
            logger.error("OS recording process timed out.")
            return b"", target_rate
        except Exception as e:
            logger.error(f"Failed to capture audio via native OS process: {e}")
            return b"", target_rate

        # =====================================================================
        # DSP PIPELINE: Pure Linear Gain Boost (No Soft-Clipping)
        # =====================================================================
        if self.software_gain != 1.0 and len(raw_audio) > 0:
            try:
                # Load as 16-bit ints, cast to float32 for safe math without overflow
                audio_data = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32)
                
                # Multiply by gain
                audio_data *= self.software_gain
                
                # Hard-clip at 16-bit boundaries so it doesn't wrap around and explode into static
                audio_data = np.clip(audio_data, -32768, 32767)
                
                # Pack safely back into bytes
                raw_audio = audio_data.astype(np.int16).tobytes()
            except Exception as e:
                logger.warning(f"Gain processing failed: {e}")

        # Automatically save a copy of the audio to disk for debugging
        if self.debug_mic and len(raw_audio) > 0:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            debug_path = os.path.join(base_dir, "debug_mic.wav")
            audio_utils.save_debug_wav(raw_audio, target_rate, debug_path)
            
        return raw_audio, target_rate

    def _convert_to_wav_bytes(self, raw_audio, rate=48000):
        """Wraps raw PCM audio bytes into a standard RIFF/WAV format byte container."""
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(self.sample_width)
            wav_file.setframerate(rate)
            wav_file.writeframes(raw_audio)
        return wav_buffer.getvalue()

    async def check_for_music(self):
        """Silently captures a short sample to locally check for music presence."""
        loop = asyncio.get_running_loop()
        
        # Offload blocking OS call to a background thread.
        raw_audio, actual_rate = await loop.run_in_executor(None, self._record_raw_audio, 4)
        
        if not raw_audio:
            return False, None, None
            
        # Call YAMNet model locally
        is_music = await loop.run_in_executor(None, self.detector.is_music_playing, raw_audio, actual_rate)
        return is_music, raw_audio, actual_rate

    async def recognize_song(self, max_retries=3, status_callback=None, pre_recorded_audio=None, pre_recorded_rate=None):
        """Runs the active Shazam cloud recognition procedure with on-demand fresh recording."""
        def update_status(msg, fade=True):
            if status_callback:
                status_callback(msg, fade=fade)

        retry_count = 0
        loop = asyncio.get_running_loop()
        
        while retry_count <= max_retries:
            try:
                if retry_count == 0:
                    if pre_recorded_audio is not None and pre_recorded_rate is not None:
                        logger.info("Double-dipping! Using ML audio for initial Shazam query...")
                        raw_audio = pre_recorded_audio
                        actual_rate = pre_recorded_rate
                    else:
                        logger.info("Recording initial segment for recognition...")
                        raw_audio, actual_rate = await loop.run_in_executor(None, self._record_raw_audio, self.record_seconds)
                else:
                    logger.info(f"Recording segment for recognition... Retry {retry_count}/{max_retries}")
                    raw_audio, actual_rate = await loop.run_in_executor(None, self._record_raw_audio, self.record_seconds)
                    
                if not raw_audio:
                    error_state = {'is_recognized': False, 'error': 'Mic Error'}
                    audio_utils.dump_metadata_json(error_state, filepath="now_playing.json")
                    
                    if retry_count >= max_retries:
                        logger.error(f"Microphone Error. Stopping after {max_retries} retries.")
                        update_status(f"Microphone Error. Stopping after {max_retries} retries.", fade=False)
                        return error_state
                        
                    retry_count += 1
                    logger.warning(f"Microphone Error. Retrying... ({retry_count}/{max_retries})")
                    update_status(f"Microphone Error. Retrying... ({retry_count}/{max_retries})", fade=False)
                    await asyncio.sleep(1)
                    continue

                # 2. Package raw PCM into WAV byte container
                wav_bytes = self._convert_to_wav_bytes(raw_audio, rate=actual_rate)

                # 3. Query Shazam APIs asynchronously
                logger.info("Querying Shazam Cloud...")
                result = await self.shazam.recognize(wav_bytes)
                
                audio_utils.dump_metadata_json(result, filepath="now_playing.json")
                parsed_result = audio_utils.parse_shazam_metadata(result)
                
                if parsed_result.get('is_recognized'):
                    logger.success("Shazam cloud match found!")
                    return parsed_result
                else:
                    if retry_count >= max_retries:
                        logger.warning(f"Nothing recognized after {max_retries} retries.")
                        update_status(f"Nothing recognized after {max_retries} retries.", fade=False)
                        return parsed_result
                    
                    retry_count += 1
                    logger.info(f"Nothing recognized, retrying... ({retry_count}/{max_retries})")
                    update_status(f"Nothing recognized, retrying... ({retry_count}/{max_retries})", fade=False)
            
            except asyncio.TimeoutError:
                error_state = {'is_recognized': False, 'error': 'Timeout'}
                audio_utils.dump_metadata_json(error_state, filepath="now_playing.json")
                
                if retry_count >= max_retries:
                    logger.error(f"Recognition timed out after {max_retries} retries.")
                    update_status(f"Recognition timed out after {max_retries} retries.", fade=False)
                    return error_state
                
                retry_count += 1
                logger.warning(f"Timeout! Retrying... ({retry_count}/{max_retries})")
                update_status(f"Timeout! Retrying... ({retry_count}/{max_retries})", fade=False)
                
            except Exception as e:
                error_state = {'is_recognized': False, 'error': str(e)}
                audio_utils.dump_metadata_json(error_state, filepath="now_playing.json")
                
                if retry_count >= max_retries:
                    logger.error(f"Hardware Error. Stopping after {max_retries} retries. ({e})")
                    update_status(f"Hardware Error. Stopping after {max_retries} retries.", fade=False)
                    return error_state
                
                retry_count += 1
                logger.error(f"Error during recognition request: {e}. Retrying... ({retry_count}/{max_retries})")
                update_status(f"Error! Retrying... ({retry_count}/{max_retries})", fade=False)
                await asyncio.sleep(1)

        return {'is_recognized': False, 'error': 'Max retries reached'}

    def close(self):
        """Cleanly shuts down the audio hardware."""
        logger.info("Audio engine shut down.")