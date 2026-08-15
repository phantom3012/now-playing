import sys
import os
import json
from contextlib import contextmanager
import ctypes
import common.logger_utils as logger_utils

# Fetch our standardized native logger
logger = logger_utils.get_logger("AudioUtils")

def silence_alsa_errors():
    """Globally silences all C-level ALSA/PortAudio warning logs (underruns, JACK server errors, etc.)."""
    try:
        # Load the ALSA C-library directly from the OS
        asound = ctypes.CDLL('libasound.so.2')
        
        # Define the custom error handler signature (matching C's snd_lib_error_handler_t)
        # typedef void (*snd_lib_error_handler_t)(const char *file, int line, const char *function, int err, const char *fmt, ...)
        ERROR_HANDLER_FUNC = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
        
        # Define an empty callback that silently discards all ALSA prints
        def py_error_handler(filename, line, function, err, fmt):
            pass
            
        # Store a global reference to the callback to prevent Python's garbage collector from erasing it
        global _c_error_handler
        _c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
        
        # Redirect ALSA's default error printer to our silent handler
        asound.snd_lib_error_set_handler(_c_error_handler)
        logger.success("ALSA C-level error logs successfully silenced.")
    except Exception:
        # Silently fail if the Pi lacks libasound.so or loading fails
        pass

def force_utf8_console():
    """Forces the console to use UTF-8 and silences underlying ALSA errors."""
    # Silence ALSA before doing anything else
    silence_alsa_errors()
    
    if sys.stdout.encoding.lower() != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except AttributeError:
            import codecs
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
            sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

@contextmanager
def ignore_stderr():
    """Temporarily mutes stderr to suppress ALSA/PyAudio hardware warnings."""
    devnull = os.open(os.devnull, os.O_WRONLY)
    old_stderr = os.dup(2)
    sys.stderr.flush()
    os.dup2(devnull, 2)
    os.close(devnull)
    try:
        yield
    finally:
        os.dup2(old_stderr, 2)
        os.close(old_stderr)

def parse_shazam_metadata(shazam_dict):
    """Extracts critical UI strings, image URLs, and the raw payload for joe_colors."""
    if not shazam_dict or 'track' not in shazam_dict:
        logger.warning("Shazam dictionary empty or missing 'track' data. Not recognized.")
        return {'is_recognized': False}
        
    track = shazam_dict['track']
    
    title = track.get('title', 'Unknown Title')
    artist = track.get('subtitle', 'Unknown Artist')
    
    image_url = None
    images = track.get('images', {})
    if 'coverarthq' in images:
        image_url = images['coverarthq']
    elif 'coverart' in images:
        image_url = images['coverart']
        
    album = ""
    release_year = ""
    for section in track.get('sections', []):
        if section.get('type') == 'SONG':
            for meta in section.get('metadata', []):
                if meta.get('title') == 'Album':
                    album = meta.get('text', '')
                elif meta.get('title') == 'Released':
                    release_year = meta.get('text', '')
                    
    logger.info(f"Successfully parsed metadata for: {title} by {artist}")
                    
    return {
        'is_recognized': True,
        'title': title,
        'artist': artist,
        'album': album,
        'release_year': release_year,
        'image_url': image_url,
        'raw_data': shazam_dict  # <-- This passes the joecolor string to the display engine!
    }

def dump_metadata_json(song_dict, filepath="now_playing.json"):
    """Saves the JSON output for caching or external debugging."""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(song_dict, f, indent=4, ensure_ascii=False)
        logger.info(f"Successfully dumped metadata to {filepath}")
    except Exception as e:
        logger.error(f"Failed to dump metadata to {filepath}: {e}")

def save_debug_wav(raw_audio, rate, filepath):
    """Saves raw PCM bytes as a standard WAV file for mic debugging."""
    try:
        import wave
        with wave.open(filepath, 'wb') as wav_file:
            wav_file.setnchannels(1) # Mono
            wav_file.setsampwidth(2) # 16-bit
            wav_file.setframerate(rate)
            wav_file.writeframes(raw_audio)
        logger.info(f"Saved live mic debug dump to {filepath}")
    except Exception as e:
        logger.error(f"Failed to save debug WAV: {e}")