import os

# common/ is one level below the project root, so go up two from this file
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESOURCES_DIR = os.path.join(PROJECT_ROOT, 'resources')
ML_MODEL_DIR = os.path.join(PROJECT_ROOT, 'ml-model')
ALBUM_CACHE_DIR = os.path.join(PROJECT_ROOT, 'album_cache')
NOW_PLAYING_JSON = os.path.join(PROJECT_ROOT, 'now_playing.json')
DEBUG_MIC_WAV = os.path.join(PROJECT_ROOT,'debug_mic.wav')