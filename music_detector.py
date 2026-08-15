import os
import csv
import warnings
import numpy as np
import logger_utils

# 1. Permanently mute NumPy's ARM architecture subnormal warnings process-wide!
warnings.filterwarnings("ignore", message=".*smallest subnormal.*")
warnings.filterwarnings("ignore", module="numpy.core.getlimits")

# Fetch our standardized native logger
logger = logger_utils.get_logger("ML")

# Use the Pi-optimized tflite_runtime if available, otherwise fallback to full tensorflow
try:
    import tflite_runtime.interpreter as tflite
except ImportError:
    import tensorflow.lite.python.interpreter as tflite

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class MusicDetector:
    def __init__(self):
        """Initializes the YAMNet TensorFlow Lite model for local audio classification."""
        # Paths to the ML model and class map
        model_path = os.path.join(BASE_DIR, 'ml-model', '1.tflite')
        class_map_path = os.path.join(BASE_DIR, 'ml-model', 'yamnet_class_map.csv')
        
        self.model_loaded = False
        if not os.path.exists(model_path):
            logger.warning("YAMNet model not found in /ml-model/. ML detection bypassed.")
            return

        try:
            self.interpreter = tflite.Interpreter(model_path=model_path)
            self.interpreter.allocate_tensors()
            self.input_details = self.interpreter.get_input_details()
            self.output_details = self.interpreter.get_output_details()
            
            self.music_classes = self._load_music_classes(class_map_path)
            self.model_loaded = True
            logger.success(f"YAMNet model loaded successfully. Tracking {len(self.music_classes)} music classes.")
        except Exception as e:
            logger.error(f"Failed to load YAMNet interpreter: {e}")
            self.model_loaded = False

    def _load_music_classes(self, csv_path):
        """Loads and filters target music-related class IDs from YAMNet metadata."""
        music_indices = set()
        self.class_names = {}
        if not os.path.exists(csv_path):
            logger.warning("Class map file missing! Defaulting class filters.")
            return {132, 133, 134, 135, 136, 137}
            
        try:
            with open(csv_path, mode='r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader) # Skip headers
                for row in reader:
                    if len(row) >= 3:
                        index = int(row[0])
                        # Keep the original-cased name for readable logging
                        self.class_names[index] = row[2]
                        display_name = row[2].lower()
                        if 'music' in display_name or 'instrument' in display_name or 'song' in display_name or 'singing' in display_name:
                            music_indices.add(index)
        except Exception as e:
            logger.error(f"Error parsing class map CSV: {e}")
            
        return music_indices

    def is_music_playing(self, raw_audio_bytes, sample_rate=48000):
        """Runs feed-forward inference on 1D PCM float32 arrays to spot target frequencies."""
        if not self.model_loaded:
            logger.warning("ML Engine bypassed: model parameters failed to load.")
            return True 

        try:
            # 1. Parse raw 16-bit PCM buffer to normalized float32 array (-1.0 to 1.0)
            audio_data = np.frombuffer(raw_audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            
            # 2. Resample cleanly to 16000 Hz using blistering fast NumPy block-averaging
            target_rate = 16000
            if sample_rate != target_rate:
                if sample_rate % target_rate == 0:
                    # Perfect integer downsampling (e.g. 48000 -> 16000 is exactly factor 3)
                    factor = int(sample_rate / target_rate)
                    # Truncate array to be a multiple of the factor
                    keep_len = (len(audio_data) // factor) * factor
                    # Reshape and average adjacent chunks (blazing fast box filter downsampler)
                    audio_data = audio_data[:keep_len].reshape(-1, factor).mean(axis=1)
                else:
                    # Fallback for non-integer rates: Fast 1D linear interpolation
                    duration = len(audio_data) / sample_rate
                    num_target_samples = int(duration * target_rate)
                    audio_data = np.interp(
                        np.linspace(0, len(audio_data) - 1, num_target_samples),
                        np.arange(len(audio_data)),
                        audio_data
                    )

            if len(audio_data) == 0:
                logger.warning("Empty audio block passed for inference.")
                return False

            input_shape = self.input_details[0]['shape']
            expected_dims = len(input_shape)
            
            # 3. Shape the array to match exactly what the TFLite interpreter expects
            if expected_dims == 2:
                audio_data = np.expand_dims(audio_data, axis=0) 
                
            try:
                self.interpreter.resize_tensor_input(self.input_details[0]['index'], audio_data.shape)
                self.interpreter.allocate_tensors()
            except RuntimeError:
                fixed_size = input_shape[-1]
                audio_data = audio_data.flatten()
                
                if len(audio_data) > fixed_size:
                    start = (len(audio_data) - fixed_size) // 2
                    audio_data = audio_data[start:start + fixed_size]
                elif len(audio_data) < fixed_size:
                    audio_data = np.pad(audio_data, (0, fixed_size - len(audio_data)))
                    
                audio_data = audio_data.reshape(input_shape)

            # 4. Queue data inside input tensor buffers
            self.interpreter.set_tensor(self.input_details[0]['index'], audio_data)
            self.interpreter.invoke()

            # 5. Read output logits containing prediction percentages
            predictions = self.interpreter.get_tensor(self.output_details[0]['index'])
            
            mean_predictions = np.squeeze(predictions)
            if len(mean_predictions.shape) == 2:
                mean_predictions = np.mean(mean_predictions, axis=0)
                
            top_class_index = int(np.argmax(mean_predictions))
            top_score = float(mean_predictions[top_class_index])
            
            # 6. Match top index against loaded music categories
            if top_class_index in self.music_classes and top_score > 0.15:
                category_name = getattr(self, 'class_names', {}).get(top_class_index, "Unknown")
                logger.success(f"ML Match Detected! Category: {category_name} ({top_class_index}) (Confidence: {top_score:.2f})")
                return True
                
        except Exception as e:
            logger.error(f"Inference pipeline exception: {e}")
            
        return False