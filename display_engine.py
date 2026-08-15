import os
import io
import time
import math
import asyncio
import requests
import pygame
from screensaver import ClockScreensaver
from text_scroller_wrap import SyncedScrollGroup
import logger_utils

# Import our refactored standalone utilities
import display_utils

# Force UTF-8 locale to prevent xkbcommon/SDL2 parsing errors on Raspberry Pi
os.environ['LC_ALL'] = 'C.UTF-8'
os.environ['LANG'] = 'C.UTF-8'

# Get the absolute path to the directory containing this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Fetch our standardized native logger
logger = logger_utils.get_logger("Display")

class NowPlayingDisplay:
    def __init__(self, width=1280, height=720, fullscreen=False):
        """Initializes the Pygame display engine."""
        logger.info("Initializing NowPlayingDisplay...")
        
        if not os.environ.get('DISPLAY'):
            logger.warning("No desktop environment detected (Headless/SSH). Setting kmsdrm...")
            os.environ['SDL_VIDEODRIVER'] = 'kmsdrm'
            try:
                pygame.display.init()
                logger.success("Video system initialized successfully using driver: kmsdrm")
            except pygame.error as e:
                logger.error("[FATAL ERROR] Could not initialize kmsdrm video driver.")
                raise Exception("kmsdrm video driver not available.")
        else:
            try:
                pygame.display.init()
                logger.success(f"Video system initialized. Using driver: {pygame.display.get_driver()}")
            except pygame.error as e:
                logger.error(f"Could not initialize video system: {e}")
                raise
                
        pygame.font.init()
        
        self.width = width
        self.height = height
        
        # Initialize sub-systems
        self._init_display(fullscreen)
        self._init_fonts()
        
        # Display State Management
        self.display_state = 'STARTUP'  # 'STARTUP', 'IDLE', 'PLAYING', 'CLOCK'
        self.status_message = ""
        self.pulse_progress = 0.0 
        
        # Refresh Spinner State
        self.is_refreshing = False
        self.spinner_angle = 0.0
        self.refresh_retry = ""
        
        # Cached UI Overlay & Glow
        self.glow_base = display_utils.generate_glow_base(self.width, self.height)
        self.ui_overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        self._render_startup_overlay()
        
        # Two-Phase Fade Animation Variables
        self.fade_state = 'NONE'  # 'NONE', 'OUT', 'IN'
        self.fade_alpha = 0
        self.fade_speed = 15       
        self.fade_overlay = pygame.Surface((self.width, self.height)).convert()
        self.fade_overlay.fill((20, 20, 20)) 
        self.fade_snapshot = None 
        
        # Pending State (Queued up while fading out)
        self.next_display_state = None
        self.next_status_message = ""
        self.next_song_data = None
        
        # Song State variables
        self.current_song_data = None
        self.art_surface = None
        self.bg_surface = None
        self.theme_colors = {}
        
        # Vinyl Animation Variables
        self.vinyl_surface = None
        self.vinyl_overlay = None  
        self.vinyl_shadow = None
        self.vinyl_rotation = 0.0
        
        # Scroller State Variables
        self.scroller_group = None
        self.text_x = 0
        
        # Background Animation variables
        self.bg_offset_x = 0.0
        self.bg_offset_y = 0.0
        self.bg_animation_progress = 0.0
        
        # Default Parsed UI Colors
        self.bg_color = (20, 20, 20)
        self.color_primary = (255, 255, 255)
        self.color_secondary = (220, 220, 220)
        self.color_tertiary = (180, 180, 180)
        self.color_quaternary = (150, 150, 150)
        self.text_color = (255, 255, 255)
        logger.success("Display Engine initialization complete.")

    def _init_display(self, fullscreen):
        """Configures the Pygame display window with SCALED hardware acceleration."""
        flags = pygame.FULLSCREEN | pygame.HWSURFACE | pygame.DOUBLEBUF | pygame.SCALED if fullscreen else 0
        
        if fullscreen:
            self.width = 1600
            self.height = 900
            
        self.hardware_screen = pygame.display.set_mode((self.width, self.height), flags)
        self.screen = pygame.Surface((self.width, self.height)).convert()
        pygame.mouse.set_visible(False)
        pygame.display.set_caption("Now Playing")

    def _init_fonts(self):
        """Loads and scales fonts dynamically based on current display resolution."""
        font_regular = os.path.join(BASE_DIR, 'resources', 'GoogleSans-VariableFont_GRAD,opsz,wght.ttf')
        font_italic = os.path.join(BASE_DIR, 'resources', 'GoogleSans-Italic-VariableFont_GRAD,opsz,wght.ttf')
        
        scale_factor = self.height / 720.0
        
        t_size = int(64 * scale_factor)
        a_size = int(48 * scale_factor)
        m_size = int(36 * scale_factor)
        
        try:
            self.font_title = pygame.font.Font(font_regular, t_size)
            self.font_artist = pygame.font.Font(font_regular, a_size)
            self.font_meta = pygame.font.Font(font_italic, m_size)
            logger.info(f"Fonts scaled to: Title={t_size}, Artist={a_size}, Meta={m_size}")
        except Exception as e:
            logger.warning(f"Error loading custom fonts: {e}. Falling back to system fonts.")
            self.font_title = pygame.font.SysFont('sans-serif', t_size)
            self.font_artist = pygame.font.SysFont('sans-serif', a_size)
            self.font_meta = pygame.font.SysFont('sans-serif', m_size)
            
        self.screensaver = ClockScreensaver(self.width, self.height, scale_factor, font_regular, font_italic)

    def set_refreshing(self, is_refreshing, retry_text=""):
        """Toggles the subtle top-right loading spinner for active background scans."""
        self.is_refreshing = is_refreshing
        if is_refreshing and retry_text:
            self.refresh_retry = retry_text
        elif not is_refreshing:
            self.refresh_retry = ""

    def _trigger_fade(self, next_state, next_msg="", next_song=None):
        """Takes a flat snapshot of the current screen to optimize the fade-out process."""
        logger.info(f"Initiating fade transition: {self.display_state} -> {next_state}")
        self.next_display_state = next_state
        self.next_status_message = next_msg
        self.next_song_data = next_song
        
        if self.fade_state != 'NONE':
            self._render_live_frame()
            
        self.fade_snapshot = self.screen.copy().convert()
        self.fade_state = 'OUT'
        self.fade_alpha = 0

    def set_clock(self):
        """Updates the state to the clock screensaver and triggers a fade."""
        if self.display_state != 'CLOCK':
            if hasattr(self.screensaver, 'refresh_cache'):
                self.screensaver.refresh_cache()
            self._trigger_fade('CLOCK')

    def set_status(self, message, fade=True):
        """Updates the idle status message and triggers a fade if changed."""
        if self.status_message != message or self.display_state != 'IDLE':
            if fade:
                self._trigger_fade('IDLE', next_msg=message)
            else:
                self.display_state = "IDLE"
                self.status_message = message
                self._render_idle_overlay()

    def update_song(self, song_dict):
        """Queues a fade to the new song."""
        if not song_dict or not song_dict.get('is_recognized'):
            return

        logger.info(f"Queueing UI update for song cards: {song_dict.get('title')}")
        self._trigger_fade('PLAYING', next_song=song_dict)
        
    def _render_startup_overlay(self):
        """Pre-renders the static startup screen to the cache."""
        self.ui_overlay.fill((0, 0, 0, 0))
        text = self.font_title.render("Now Playing", True, (255, 255, 255))
        text_rect = text.get_rect(center=(self.width//2, self.height//2))
        self.ui_overlay.blit(text, text_rect)

    def _render_idle_overlay(self):
        """Pre-renders the static idle/status screen to the cache."""
        self.ui_overlay.fill((0, 0, 0, 0))

        # Status may carry a second line (after \n) shown at the bottom center
        lines = self.status_message.split("\n")
        main_msg = lines[0]
        sub_msg = lines[1] if len(lines) > 1 else ""

        if main_msg == "Now Playing":
            text = self.font_title.render(main_msg, True, (255, 255, 255))
        else:
            text = self.font_artist.render(main_msg, True, (225, 225, 230))
        text_rect = text.get_rect(center=(self.width//2, self.height//2))
        self.ui_overlay.blit(text, text_rect)

        if sub_msg:
            sub_text = self.font_meta.render(sub_msg, True, (200, 120, 120))
            sub_rect = sub_text.get_rect(center=(self.width//2, self.height - 60))
            self.ui_overlay.blit(sub_text, sub_rect)

    def _apply_song_data(self, song_dict):
        """Processes the song payload and pre-renders the UI elements."""
        self.current_song_data = song_dict

        raw_track = song_dict.get('raw_data', {}).get('track', {})
        joecolor_str = raw_track.get('images', {}).get('joecolor', '')
        
        self.theme_colors = display_utils.parse_joecolor(joecolor_str)
        
        self.bg_color = self.theme_colors.get('b', (40, 40, 40))
        self.color_primary = self.theme_colors.get('p', (255, 255, 255))
        self.color_secondary = self.theme_colors.get('s', (220, 220, 220))
        self.color_tertiary = self.theme_colors.get('t', (180, 180, 180))
        self.color_quaternary = self.theme_colors.get('q', (150, 150, 150))

        self.text_color = display_utils.avg_color(self.bg_color, self.color_quaternary)
        
        self.bg_surface = display_utils.create_animated_gradient(self.width, self.height, self.theme_colors)
        self.bg_animation_progress = 0.0

        art_url = song_dict.get('cover_art_url') or song_dict.get('image_url')
        
        art_size = int(self.height * 0.55)
        if art_size % 2 != 0: 
            art_size -= 1
        
        if art_url:
            try:
                response = requests.get(art_url, timeout=5)
                if response.status_code == 200:
                    image_bytes = response.content
                    raw_art = pygame.image.load(io.BytesIO(image_bytes)).convert_alpha()
                    
                    square_art = display_utils.force_square_crop(raw_art)
                    scaled_art = pygame.transform.smoothscale(square_art, (art_size, art_size))
                    self.art_surface = display_utils.round_corners(scaled_art, radius=8)
                    
                    artist_str = song_dict.get('artist', 'Unknown')
                    album_str = song_dict.get('album', 'Unknown')
                    
                    if album_str and album_str != 'Unknown':
                        cache_key = f"{artist_str}_{album_str}"
                    else:
                        cache_key = raw_track.get('key', song_dict.get('title'))
                        
                    display_utils.cache_album_art(cache_key, self.art_surface, BASE_DIR)
                    
                    vinyl_size = int(art_size * 0.95)
                    if vinyl_size % 2 != 0: 
                        vinyl_size -= 1
                        
                    self.vinyl_surface = display_utils.create_spinning_vinyl_base(
                        vinyl_size, self.bg_color, self.color_primary, self.color_secondary
                    )
                    self.vinyl_overlay = display_utils.create_vinyl_overlay(vinyl_size)
                    self.vinyl_shadow = display_utils.create_circular_shadow(vinyl_size)
                    
                    self.vinyl_rotation = 0.0
            except Exception as e:
                logger.error(f"Error processing song image: {e}")
                self.art_surface = None
                self.vinyl_surface = None
                self.vinyl_overlay = None
                self.vinyl_shadow = None
        else:
            self.art_surface = None
            self.vinyl_surface = None
            self.vinyl_overlay = None
            self.vinyl_shadow = None
            
        self.ui_overlay.fill((0, 0, 0, 0))
        
        title = song_dict.get('title', 'Unknown Title')
        artist = song_dict.get('artist', 'Unknown Artist')
        album = song_dict.get('album', 'Unknown Album')
        year = song_dict.get('release_year', '')
        
        spacing = int(self.width * 0.04) 
        max_text_width = int(self.width * 0.45) 
        
        total_group_width = art_size + spacing + max_text_width
        start_x = (self.width - total_group_width) // 2
        art_x = start_x

        meta_parts = [p for p in [album, year] if p and p != 'Unknown']
        meta_string = " • ".join(meta_parts)
        
        split_meta = False
        if len(meta_parts) == 2:
            if self.font_meta.size(meta_string)[0] > max_text_width:
                split_meta = True

        title_h = self.font_title.get_height()
        artist_h = self.font_artist.get_height()
        meta_h = self.font_meta.get_height()
        
        title_artist_gap = 10
        artist_meta_gap = 25
        meta_year_gap = 5 
        
        total_text_height = title_h + title_artist_gap + artist_h + artist_meta_gap + meta_h
        
        if split_meta:
            total_text_height += (meta_year_gap + meta_h)
            
        self.text_x = art_x + art_size + spacing
        title_y = (self.height - total_text_height) // 2
        artist_y = title_y + title_h + title_artist_gap
        album_y = artist_y + artist_h + artist_meta_gap
        year_y = album_y + meta_h + meta_year_gap

        # Instantiate the frame-accurate text marquee group
        self.scroller_group = SyncedScrollGroup(max_text_width)
        self.scroller_group.add_text(self.font_title, title, self.text_color, title_y)
        self.scroller_group.add_text(self.font_artist, artist, self.text_color, artist_y)
        
        if split_meta:
            self.scroller_group.add_text(self.font_meta, album, self.text_color, album_y)
            self.scroller_group.add_text(self.font_meta, year, self.text_color, year_y)
        else:
            self.scroller_group.add_text(self.font_meta, meta_string, self.text_color, album_y)

        # Pre-compile into a static cache block if nothing needs scrolling animation
        self.scroller_group.build_cache()

    def _render_live_frame(self):
        """Handles the actual drawing of the active UI state to the screen."""
        if self.display_state == 'STARTUP':
            self.screen.fill((20, 20, 20))
            self.screen.blit(self.ui_overlay, (0, 0))

        elif self.display_state == 'CLOCK':
            self.screensaver.render(self.screen)

        elif self.display_state == 'IDLE':
            if self.status_message == "Now Playing":
                self.screen.fill((20, 20, 20)) 
                self.screen.blit(self.ui_overlay, (0, 0))
            else:
                self.pulse_progress += 0.05 
                pulse = (math.sin(self.pulse_progress) + 1) / 2
                self.screen.fill((10, 10, 12))
                
                min_scale = 0.8
                max_scale = 1.15
                current_scale = min_scale + (pulse * (max_scale - min_scale))
                
                scaled_w = int(self.glow_base.get_width() * current_scale)
                scaled_h = int(self.glow_base.get_height() * current_scale)
                
                current_glow = pygame.transform.scale(self.glow_base, (scaled_w, scaled_h))
                
                glow_alpha = int(30 + (pulse * 80)) 
                current_glow.set_alpha(glow_alpha)
                
                glow_rect = current_glow.get_rect(center=(self.width // 2, self.height // 2))
                self.screen.blit(current_glow, glow_rect)
                self.screen.blit(self.ui_overlay, (0, 0))

        elif self.display_state == 'PLAYING':
            if self.bg_surface:
                speed = 0.025 
                self.bg_animation_progress += speed
                oscillation = math.sin(self.bg_animation_progress)
                
                base_x, base_y = self.width * 0.1, self.height * 0.1
                amp_x, amp_y = self.width * 0.1, self.height * 0.1
                
                self.bg_offset_x = base_x + (oscillation * amp_x)
                self.bg_offset_y = base_y + (oscillation * amp_y)
                self.screen.blit(self.bg_surface, (-int(self.bg_offset_x), -int(self.bg_offset_y)))
            else:
                self.screen.fill((20, 20, 20))

            art_size = int(self.height * 0.55)
            if art_size % 2 != 0: art_size -= 1
            
            spacing = int(self.width * 0.04) 
            max_text_width = int(self.width * 0.45) 
            
            total_group_width = art_size + spacing + max_text_width
            start_x = (self.width - total_group_width) // 2
            art_y = (self.height - art_size) // 2
            art_x = start_x
            
            sleeve_center_x = art_x + (art_size // 2)
            
            if self.vinyl_surface and self.art_surface:
                vinyl_center_y = art_y + int(art_size * 0.25)
                
                if self.vinyl_shadow:
                    shadow_rect = self.vinyl_shadow.get_rect(center=(sleeve_center_x + 5, vinyl_center_y + 5))
                    self.screen.blit(self.vinyl_shadow, shadow_rect)
                
                self.vinyl_rotation = (self.vinyl_rotation - 0.9) % 360 
                rotated_vinyl = pygame.transform.rotozoom(self.vinyl_surface, self.vinyl_rotation, 1.0)
                vinyl_rect = rotated_vinyl.get_rect(center=(sleeve_center_x, vinyl_center_y))
                self.screen.blit(rotated_vinyl, vinyl_rect)
                
                if self.vinyl_overlay:
                    overlay_rect = self.vinyl_overlay.get_rect(center=(sleeve_center_x, vinyl_center_y))
                    self.screen.blit(self.vinyl_overlay, overlay_rect)

            if self.art_surface:
                sleeve_shadow = pygame.Surface((art_size, art_size), pygame.SRCALPHA)
                pygame.draw.rect(sleeve_shadow, (0, 0, 0, 120), (0, 0, art_size, art_size), border_radius=8)
                self.screen.blit(sleeve_shadow, (art_x + 8, art_y + 8))
                self.screen.blit(self.art_surface, (art_x, art_y))
                pygame.draw.line(self.screen, (255, 255, 255, 40), (art_x, art_y), (art_x + art_size, art_y), 1)
            
            # --- RENDER THE METADATA MARQUEE GROUP ON THE LIVE SCREEN ---
            if self.scroller_group:
                self.scroller_group.draw(self.screen, self.text_x)

            if self.is_refreshing:
                self.spinner_angle = (self.spinner_angle - 0.1) % (math.pi * 2)
                spinner_surf = display_utils.create_refresh_spinner(
                    self.spinner_angle, 
                    radius=16, 
                    text=self.refresh_retry, 
                    font=self.font_meta,
                    color = self.bg_color
                )
                self.screen.blit(spinner_surf, (self.width - 60, 20))

    def draw_frame(self):
        """Renders the UI elements based on current state."""
        if self.fade_state in ['OUT', 'IN'] and self.fade_snapshot:
            self.screen.blit(self.fade_snapshot, (0, 0))
        else:
            self._render_live_frame()

        if self.fade_state == 'OUT':
            self.fade_alpha += self.fade_speed
            if self.fade_alpha >= 255:
                self.fade_alpha = 255
                self.fade_state = 'IN'
                
                self.display_state = self.next_display_state
                if self.display_state == 'IDLE':
                    self.status_message = self.next_status_message
                    self.current_song_data = None
                    self._render_idle_overlay()
                elif self.display_state == 'PLAYING':
                    self._apply_song_data(self.next_song_data)
                elif self.display_state == 'CLOCK':
                    self.current_song_data = None
                
                self._render_live_frame()
                self.fade_snapshot = self.screen.copy().convert()
                    
            self.fade_overlay.set_alpha(int(self.fade_alpha))
            self.screen.blit(self.fade_overlay, (0, 0))
            
        elif self.fade_state == 'IN':
            self.fade_alpha -= self.fade_speed
            if self.fade_alpha <= 0:
                self.fade_alpha = 0
                self.fade_state = 'NONE'
                self.fade_snapshot = None 
            
            self.fade_overlay.set_alpha(int(self.fade_alpha))
            self.screen.blit(self.fade_overlay, (0, 0))

        # --- SOFTWARE 180 DEGREE FLIP HACK ---
        flipped_screen = pygame.transform.flip(self.screen, True, True)
        self.hardware_screen.blit(flipped_screen, (0, 0))

        pygame.display.flip()