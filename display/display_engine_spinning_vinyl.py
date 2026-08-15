import os
import io
import common.logger_utils as logger_utils
import math
import asyncio
import requests
import pygame
from display.screensaver import ClockScreensaver
from display.text_scroller import SyncedScrollGroup
import common.paths as paths

# Force UTF-8 locale to prevent xkbcommon/SDL2 parsing errors on Raspberry Pi
os.environ['LC_ALL'] = 'C.UTF-8'
os.environ['LANG'] = 'C.UTF-8'
logger = logger_utils.get_logger("Display")

class NowPlayingDisplay:
    def __init__(self, width=1280, height=720, fullscreen=False):
        """Initializes the Pygame display engine."""
        
        # --- HARDWARE DISPLAY CONFIGURATION ---
        if not os.environ.get('DISPLAY'):
            logger.warning("No desktop environment detected (Headless/SSH). Setting kmsdrm...")
            os.environ['SDL_VIDEODRIVER'] = 'kmsdrm'
            try:
                pygame.display.init()
                logger.success("Video system initialized successfully using driver: kmsdrm")
            except pygame.error as e:
                logger.error("[FATAL ERROR] Could not initialize kmsdrm video driver.")
                logger.error("1. Ensure your user has hardware display permissions: sudo usermod -a -G video,render $USER")
                logger.error("2. The Pygame version from 'pip' might lack Pi hardware support.")
                logger.error("   Fix this by running: sudo apt-get install python3-pygame")
                logger.error(f"   Error details: %s\n",e)
                raise Exception("kmsdrm video driver not available.")
        else:
            try:
                pygame.display.init()
                logger.success(f"Video system initialized. Using driver: {pygame.display.get_driver()}")
            except pygame.error as e:
                logger.error(f"Could not initialize video system: {e}")
                raise
                
        pygame.init()
        
        self.width = width
        self.height = height
        
        # Initialize sub-systems
        self._init_display(fullscreen)
        self._init_fonts()
        
        # Display State Management
        self.display_state = 'STARTUP'  # 'STARTUP', 'IDLE', 'PLAYING', 'CLOCK'
        self.status_message = ""
        self.pulse_progress = 0.0 # Heartbeat animation counter
        
        # Refresh Spinner State
        self.is_refreshing = False
        self.spinner_angle = 0.0
        self.refresh_retry = ""
        
        # Cached UI Overlay & Glow (MASSIVE PERFORMANCE BOOST)
        self.glow_base = self._generate_glow_base()
        self.ui_overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        self._render_startup_overlay()
        
        # Two-Phase Fade Animation Variables
        self.fade_state = 'NONE'  # 'NONE', 'OUT', 'IN'
        self.fade_alpha = 0
        self.fade_speed = 15 
        self.fade_overlay = pygame.Surface((self.width, self.height)).convert()
        self.fade_overlay.fill((20, 20, 20)) # Dark gray to match the background
        self.fade_snapshot = None # Holds a flat image of the screen to optimize fading
        
        # Pending State (Queued up while fading out)
        self.next_display_state = None
        self.next_status_message = ""
        self.next_song_data = None
        
        # Song State variables
        self.current_song_data = None
        self.bg_surface = None
        self.theme_colors = {}
        
        # Vinyl Animation Variables
        self.vinyl_surface = None
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

    def _init_display(self, fullscreen):
        """Configures the Pygame display window with SCALED hardware acceleration."""
        
        # HWSURFACE, DOUBLEBUF, and SCALED provide extreme performance boosts.
        # SCALED allows us to render at 720p internally, and the GPU scales it to the TV size.
        flags = pygame.FULLSCREEN | pygame.HWSURFACE | pygame.DOUBLEBUF | pygame.SCALED if fullscreen else 0
        
        if fullscreen:
            # Force internal rendering resolution. DO NOT query native resolution.
            # This cuts the pixel processing workload by over 60%!
            self.width = 1600
            self.height = 900
            
        self.hardware_screen = pygame.display.set_mode((self.width, self.height), flags)
        self.screen = pygame.Surface((self.width, self.height)).convert()
        pygame.mouse.set_visible(False)
        pygame.display.set_caption("Now Playing")

    def _init_fonts(self):
        """Loads and scales fonts dynamically based on current display resolution."""
        font_regular = os.path.join(paths.RESOURCES_DIR, 'GoogleSans-VariableFont_GRAD,opsz,wght.ttf')
        font_italic = os.path.join(paths.RESOURCES_DIR, 'GoogleSans-Italic-VariableFont_GRAD,opsz,wght.ttf')
        
        # Calculate a normalized scale factor based on the current height
        scale_factor = self.height / 720.0
        
        # Define base sizes at 720p
        base_t_size = 64 
        base_a_size = 48
        base_m_size = 36
        
        # Calculate final sizes
        t_size = int(base_t_size * scale_factor)
        a_size = int(base_a_size * scale_factor)
        m_size = int(base_m_size * scale_factor)
        
        try:
            self.font_title = pygame.font.Font(font_regular, t_size)
            self.font_artist = pygame.font.Font(font_regular, a_size)
            self.font_meta = pygame.font.Font(font_italic, m_size)
            logger.info(f"Fonts scaled to: Title={t_size}, Artist={a_size}, Meta={m_size}")
        except Exception as e:
            logger.warning(f"Error loading fonts: {e}")
            self.font_title = pygame.font.SysFont('sans-serif', t_size)
            self.font_artist = pygame.font.SysFont('sans-serif', a_size)
            self.font_meta = pygame.font.SysFont('sans-serif', m_size)
            
        self.screensaver = ClockScreensaver(self.width, self.height, scale_factor, font_regular, font_italic)
            
    # ---------------------------------------------------------
    # STATE AND FADE MANAGEMENT
    # ---------------------------------------------------------

    def _generate_glow_base(self):
        """Pre-renders a massive radial gradient for the full-screen pulsing background."""
        radius = int(math.hypot(self.width // 2, self.height // 2))
        surface = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        
        for i in range(radius, 0, -3):
            falloff = 1 - (i / radius) ** 2.5
            alpha = int(255 * falloff)
            pygame.draw.circle(surface, (180, 190, 210, alpha), (radius, radius), i)
            
        return surface

    def _trigger_fade(self, next_state, next_msg="", next_song=None):
        """Takes a flat snapshot of the current screen to optimize the fade-out process."""
        self.next_display_state = next_state
        self.next_status_message = next_msg
        self.next_song_data = next_song

        if self.fade_state != 'NONE':
            self._render_live_frame()

        self.fade_snapshot = self.screen.copy().convert()
        self.fade_state = 'OUT'
        self.fade_alpha = 0
        
    def set_refreshing(self, is_refreshing, retry_text=""):
        """Toggles the subtle top-right loading spinner for active background scans."""
        self.is_refreshing = is_refreshing
        if is_refreshing and retry_text:
            self.refresh_retry = retry_text
        elif not is_refreshing:
            self.refresh_retry = ""

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

        logger.info(f"Queueing UI update for: {song_dict.get('title')}")
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

        # Extract the palette string from the raw JSON payload
        raw_track = song_dict.get('raw_data', {}).get('track', {})
        joecolor_str = raw_track.get('images', {}).get('joecolor', '')
        
        self.theme_colors = self._parse_joecolor(joecolor_str)
        
        # Map the extracted colors, with fallbacks
        self.bg_color = self.theme_colors.get('b', (40, 40, 40))
        self.color_primary = self.theme_colors.get('p', (255, 255, 255))
        self.color_secondary = self.theme_colors.get('s', (220, 220, 220))
        self.color_tertiary = self.theme_colors.get('t', (180, 180, 180))
        self.color_quaternary = self.theme_colors.get('q', (150, 150, 150))

        # Calculate text color as average of bg and quaternary colors
        self.text_color = self._avg_color(self.bg_color, self.color_quaternary)
        
        # Build the animated gradient background
        self.bg_surface = self._create_animated_gradient(self.theme_colors)
        self.bg_animation_progress = 0.0

        # Fetch and format Album Art into a Spinning Vinyl
        art_url = song_dict.get('cover_art_url') or song_dict.get('image_url')
        art_size = int(self.height * 0.55)
        
        if art_url:
            try:
                response = requests.get(art_url, timeout=5)
                if response.status_code == 200:
                    image_bytes = response.content
                    raw_art = pygame.image.load(io.BytesIO(image_bytes)).convert_alpha()
                    
                    # Crop square and scale
                    square_art = self._force_square_crop(raw_art)
                    scaled_art = pygame.transform.smoothscale(square_art, (art_size, art_size))
                    
                    # Create the picture-disc vinyl and its shadow
                    self.vinyl_surface = self._create_vinyl_record(scaled_art, art_size)
                    self.vinyl_shadow = self._create_circular_shadow(art_size)
                    self.vinyl_rotation = 0.0 # Reset rotation for new song
            except Exception as e:
                logger.error(f"Error processing image: {e}")
                self.vinyl_surface = None
                self.vinyl_shadow = None
        else:
            self.vinyl_surface = None
            self.vinyl_shadow = None
            
        # ---------------------------------------------------------
        # PRE-RENDER CACHE & SCROLLERS
        # ---------------------------------------------------------
        self.ui_overlay.fill((0, 0, 0, 0)) # Clear transparent
        
        title = song_dict.get('title', 'Unknown Title')
        artist = song_dict.get('artist', 'Unknown Artist')
        album = song_dict.get('album', 'Unknown Album')
        year = song_dict.get('release_year', '')
        
        spacing = int(self.width * 0.04) 
        max_text_width = int(self.width * 0.45) 
        
        total_group_width = art_size + spacing + max_text_width
        start_x = (self.width - total_group_width) // 2
        art_x = start_x

        # --- DYNAMIC META TEXT SPLITTING ---
        meta_parts = [p for p in [album, year] if p and p != 'Unknown']
        meta_string = " • ".join(meta_parts)
        
        split_meta = False
        if len(meta_parts) == 2:
            # If the combined string exceeds max width, split them!
            if self.font_meta.size(meta_string)[0] > max_text_width:
                split_meta = True

        # Calculate heights to determine exact y-coordinates for the scroller group
        title_h = self.font_title.get_height()
        artist_h = self.font_artist.get_height()
        meta_h = self.font_meta.get_height()
        
        title_artist_gap = 10
        artist_meta_gap = 25
        meta_year_gap = 5 # Small gap if year is placed on a new line
        
        total_text_height = title_h + title_artist_gap + artist_h + artist_meta_gap + meta_h
        
        # Adjust total height if we are rendering 4 lines instead of 3
        if split_meta:
            total_text_height += (meta_year_gap + meta_h)
            
        self.text_x = art_x + art_size + spacing
        title_y = (self.height - total_text_height) // 2
        artist_y = title_y + title_h + title_artist_gap
        album_y = artist_y + artist_h + artist_meta_gap
        year_y = album_y + meta_h + meta_year_gap

        # Initialize the Synced Scroller Group
        self.scroller_group = SyncedScrollGroup(max_text_width)
        self.scroller_group.add_text(self.font_title, title, self.text_color, title_y)
        self.scroller_group.add_text(self.font_artist, artist, self.text_color, artist_y)
        
        if split_meta:
            self.scroller_group.add_text(self.font_meta, album, self.text_color, album_y)
            self.scroller_group.add_text(self.font_meta, year, self.text_color, year_y)
        else:
            self.scroller_group.add_text(self.font_meta, meta_string, self.text_color, album_y)

    # ---------------------------------------------------------
    # VINYL RECORD GENERATION
    # ---------------------------------------------------------

    def _create_vinyl_record(self, surface, size):
        """Creates a circular picture-disc vinyl from the album art."""
        vinyl = pygame.Surface((size, size), pygame.SRCALPHA)
        center = (size // 2, size // 2)
        radius = size // 2
        
        # 1. Create a circular mask and apply it to the album art
        mask = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.circle(mask, (255, 255, 255, 255), center, radius)
        circular_art = surface.copy().convert_alpha()
        circular_art.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        
        # 2. Blit the circular art onto our final vinyl surface
        vinyl.blit(circular_art, (0, 0))
        
        # 3. Draw the thick black outer lip of the record
        pygame.draw.circle(vinyl, (15, 15, 15, 255), center, radius, width=6)
        pygame.draw.circle(vinyl, (40, 40, 40, 255), center, radius - 6, width=1)
        
        # 4. Draw very faint concentric grooves over the art
        # for r in range(int(size * 0.1), int(size * 0.48), 16):
        #     pygame.draw.circle(vinyl, (0, 0, 0, 0), center, r, width=1)
            
        # 5. Draw the center hole (Spindle hole) - more realistic, but I'm skipping this for now
        # pygame.draw.circle(vinyl, (20, 20, 20, 255), center, int(size * 0.04))
        # pygame.draw.circle(vinyl, (0, 0, 0, 255), center, int(size * 0.04), width=2)
        
        return vinyl

    def _create_circular_shadow(self, size):
        """Creates a soft circular drop shadow for the spinning record."""
        shadow_size = size + 30
        shadow = pygame.Surface((shadow_size, shadow_size), pygame.SRCALPHA)
        center = (shadow_size // 2, shadow_size // 2)
        
        # Draw concentric fading circles to simulate a soft blur
        for i in range(15):
            alpha = max(0, 80 - (i * 6))
            pygame.draw.circle(shadow, (0, 0, 0, alpha), center, (size // 2) + i)
            
        return shadow

    def _draw_belt_drive(self, center_x, center_y, art_size):
        """Draws a motor pulley at the upper-left with a drive belt wrapping the
        record and pulley. Belt ticks travel to imply motion, synced to the
        record's clockwise spin."""
        record_r = art_size // 2

        # --- Motor pulley position (upper-left of the record, in free space) ---
        pulley_r = max(10, int(art_size * 0.07))
        pulley_x = center_x - int(art_size * 0.45)
        pulley_y = center_y - int(art_size * 0.47)

        # --- Belt geometry: wrap both the record and the pulley ---
        base_angle = math.atan2(pulley_y - center_y, pulley_x - center_x)  # record -> pulley

        # Contact points on the RECORD (fanned around the direction toward the pulley)
        wrap_spread = math.radians(55)
        a_top = base_angle + wrap_spread
        a_bot = base_angle - wrap_spread
        r_top = (center_x + math.cos(a_top) * record_r, center_y + math.sin(a_top) * record_r)
        r_bot = (center_x + math.cos(a_bot) * record_r, center_y + math.sin(a_bot) * record_r)

        # Contact points on the PULLEY: fan the two strands around the pulley edge
        # (same approach as the record) so they're tangent and wrap the pulley's arc.
        pa = math.atan2(pulley_y - center_y, pulley_x - center_x)  # record -> pulley (same dir as base_angle)
        pulley_wrap = math.radians(80)  # how far apart the two pulley contacts sit
        pp_top = pa + pulley_wrap
        pp_bot = pa - pulley_wrap
        p_top = (pulley_x + math.cos(pp_top) * pulley_r, pulley_y + math.sin(pp_top) * pulley_r)
        p_bot = (pulley_x + math.cos(pp_bot) * pulley_r, pulley_y + math.sin(pp_bot) * pulley_r)

        # --- Draw the two belt strands (dark, slightly emphasized) ---
        pygame.draw.line(self.screen, (25, 25, 28), p_top, r_top, 4)
        pygame.draw.line(self.screen, (25, 25, 28), p_bot, r_bot, 4)

        # --- Draw the belt wrapping around the far arc of the pulley ---
        belt_box = pygame.Rect(0, 0, pulley_r * 2, pulley_r * 2)
        belt_box.center = (pulley_x, pulley_y)
        # Arc spans the far side of the pulley between the two contact points.
        # pygame.draw.arc goes counterclockwise from start to end (y is flipped).
        pygame.draw.arc(self.screen, (25, 25, 28), belt_box, pp_top, pp_bot + 2 * math.pi, 5)

        # --- Traveling belt ticks (motion cue), synced to record spin ---
        phase = (-self.vinyl_rotation * 0.05) % 1.0
        num_ticks = 6
        for strand_a, strand_b in ((p_top, r_top), (r_bot, p_bot)):
            ax, ay = strand_a
            bx, by = strand_b
            for i in range(num_ticks):
                t = ((i / num_ticks) + phase) % 1.0
                tx = ax + (bx - ax) * t
                ty = ay + (by - ay) * t
                pygame.draw.circle(self.screen, (70, 70, 75), (int(tx), int(ty)), 2)

        # --- Draw the pulley on top (small dark cylinder with a spinning mark) ---
        pygame.draw.circle(self.screen, (15, 15, 17), (pulley_x, pulley_y), pulley_r)
        pygame.draw.circle(self.screen, (55, 55, 60), (pulley_x, pulley_y), pulley_r, 2)
        spin = math.radians(-self.vinyl_rotation * 3.0)
        mark_x = pulley_x + math.cos(spin) * (pulley_r * 0.5)
        mark_y = pulley_y + math.sin(spin) * (pulley_r * 0.5)
        pygame.draw.circle(self.screen, (120, 120, 125), (int(mark_x), int(mark_y)), 2)

    def _draw_tone_arm(self, center_x, center_y, art_size):
        """Draws a stylized metallic tone arm resting on the record."""
        # Pivot point (Top Right of the record)
        pivot_x = center_x + (art_size // 2) + 20
        pivot_y = center_y - (art_size // 2) + 20
        
        # Needle resting point (Mid-Right side of the record)
        needle_x = center_x + int(art_size * 0.25)
        needle_y = center_y + int(art_size * 0.15)
        
        # Draw the arm rod (Thick silver line with a bright highlight)
        pygame.draw.line(self.screen, (160, 160, 160), (pivot_x, pivot_y), (needle_x, needle_y), 8)
        pygame.draw.line(self.screen, (230, 230, 230), (pivot_x, pivot_y), (needle_x, needle_y), 3)
        
        # Draw the pivot base
        pygame.draw.circle(self.screen, (30, 30, 30), (pivot_x, pivot_y), 32)
        pygame.draw.circle(self.screen, (100, 100, 100), (pivot_x, pivot_y), 20)
        pygame.draw.circle(self.screen, (20, 20, 20), (pivot_x, pivot_y), 6)
        
        # Calculate angle for the headshell (cartridge)
        angle = math.atan2(needle_y - pivot_y, needle_x - pivot_x)
        hw, hh = 14, 28  # Half-width, half-height of the headshell polygon
        
        dx, dy = math.cos(angle), math.sin(angle)
        px, py = -math.sin(angle), math.cos(angle)
        
        # Calculate the 4 corners of the rotated headshell
        p1 = (needle_x - px * hw - dx * hh, needle_y - py * hw - dy * hh)
        p2 = (needle_x + px * hw - dx * hh, needle_y + py * hw - dy * hh)
        p3 = (needle_x + px * hw + dx * hh, needle_y + py * hw + dy * hh)
        p4 = (needle_x - px * hw + dx * hh, needle_y - py * hw + dy * hh)
        
        # Draw headshell and stylus tip
        pygame.draw.polygon(self.screen, (25, 25, 25), [p1, p2, p3, p4])
        pygame.draw.circle(self.screen, (220, 40, 40), (int(p3[0] + p4[0]) // 2, int(p3[1] + p4[1]) // 2), 4)

        # --- Counterweight: short rod extending BEHIND the pivot, opposite the needle ---
        back_len = int(art_size * 0.14)
        cw_x = pivot_x - dx * back_len   # dx/dy point pivot->needle, so -dx goes behind
        cw_y = pivot_y - dy * back_len
        # Back rod
        pygame.draw.line(self.screen, (150, 150, 150), (pivot_x, pivot_y), (cw_x, cw_y), 7)
        pygame.draw.line(self.screen, (220, 220, 220), (pivot_x, pivot_y), (cw_x, cw_y), 2)
        # Counterweight cylinder (dark, slightly larger than the rod)
        cw_r = max(9, int(art_size * 0.05))
        pygame.draw.circle(self.screen, (20, 20, 22), (int(cw_x), int(cw_y)), cw_r)
        pygame.draw.circle(self.screen, (70, 70, 75), (int(cw_x), int(cw_y)), cw_r, 2)
        # Small highlight to imply a rounded metal weight
        pygame.draw.circle(self.screen, (110, 110, 115), (int(cw_x - cw_r * 0.3), int(cw_y - cw_r * 0.3)), max(2, cw_r // 4))

    # ---------------------------------------------------------
    # UTILITY METHODS
    # ---------------------------------------------------------

    @staticmethod
    def _hex_to_rgb(hx):
        """Converts a 6-character hex string to an RGB tuple."""
        return tuple(int(hx[i:i+2], 16) for i in (0, 2, 4))

    @staticmethod
    def _avg_color(c1, c2):
        """Calculates the average between two RGB tuples."""
        return tuple((a + b) // 2 for a, b in zip(c1, c2))

    @staticmethod
    def _parse_joecolor(joecolor_str):
        """Extracts hex colors using simple string splitting."""
        colors = {}
        if not joecolor_str:
            return colors
            
        try:
            parts = joecolor_str.split(':')
            if len(parts) >= 6:
                colors['b'] = NowPlayingDisplay._hex_to_rgb(parts[1][:6]) # Background
                colors['p'] = NowPlayingDisplay._hex_to_rgb(parts[2][:6]) # Primary Text
                colors['s'] = NowPlayingDisplay._hex_to_rgb(parts[3][:6]) # Secondary Text
                colors['t'] = NowPlayingDisplay._hex_to_rgb(parts[4][:6]) # Tertiary Text
                colors['q'] = NowPlayingDisplay._hex_to_rgb(parts[5][:6]) # Quaternary Text
        except Exception as e:
            logger.error(f"Error parsing joecolor string: {e}")
            
        return colors

    @staticmethod
    def _force_square_crop(surface):
        """Forces an image to be a perfect 1:1 square by center-cropping."""
        w, h = surface.get_size()
        min_dim = min(w, h)
        x_offset = (w - min_dim) // 2
        y_offset = (h - min_dim) // 2
        square_rect = pygame.Rect(x_offset, y_offset, min_dim, min_dim)
        return surface.subsurface(square_rect).copy()

    # ---------------------------------------------------------
    # CORE LOGIC
    # ---------------------------------------------------------

    def _create_animated_gradient(self, theme_colors):
        """Builds a slightly coarser gradient mapped from bg -> primary -> secondary -> tertiary for bouncing."""
        c_bg = theme_colors.get('b', (20, 20, 20))
        c_prim = theme_colors.get('p', (100, 100, 100))
        c_sec = theme_colors.get('s', (50, 50, 50))
        c_tert = theme_colors.get('t', (20, 20, 20))

        base_size = 48
        grad = pygame.Surface((base_size, base_size))
        max_dist = (base_size - 1) * 2.0
        
        for y in range(base_size):
            for x in range(base_size):
                ratio = (x + y) / max_dist
                if ratio < 0.3333:
                    t = ratio * 3.0
                    r = int(c_bg[0] + (c_prim[0] - c_bg[0]) * t)
                    g = int(c_bg[1] + (c_prim[1] - c_bg[1]) * t)
                    b = int(c_bg[2] + (c_prim[2] - c_bg[2]) * t)
                elif ratio < 0.6666:
                    t = (ratio - 0.3333) * 3.0
                    r = int(c_prim[0] + (c_sec[0] - c_prim[0]) * t)
                    g = int(c_prim[1] + (c_sec[1] - c_prim[1]) * t)
                    b = int(c_prim[2] + (c_sec[2] - c_prim[2]) * t)
                else:
                    t = (ratio - 0.6666) * 3.0
                    r = int(c_sec[0] + (c_tert[0] - c_sec[0]) * t)
                    g = int(c_sec[1] + (c_tert[1] - c_sec[1]) * t)
                    b = int(c_sec[2] + (c_tert[2] - c_sec[2]) * t)
                
                # Clamp values safely between 0-255
                r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
                grad.set_at((x, y), (r, g, b))
        
        return pygame.transform.smoothscale(grad, (int(self.width * 1.2), int(self.height * 1.2)))

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
            # 1. Background Gradient Animation
            if self.bg_surface:
                speed = 0.05
                self.bg_animation_progress += speed
                oscillation = math.sin(self.bg_animation_progress)
                
                base_x, base_y = self.width * 0.1, self.height * 0.1
                amp_x, amp_y = self.width * 0.1, self.height * 0.1
                
                self.bg_offset_x = base_x + (oscillation * amp_x)
                self.bg_offset_y = base_y + (oscillation * amp_y)
                self.screen.blit(self.bg_surface, (-int(self.bg_offset_x), -int(self.bg_offset_y)))
            else:
                self.screen.fill((20, 20, 20))

            # 2. Vinyl Animation
            art_size = int(self.height * 0.55)
            spacing = int(self.width * 0.04) 
            max_text_width = int(self.width * 0.45) 
            
            total_group_width = art_size + spacing + max_text_width
            start_x = (self.width - total_group_width) // 2
            art_y = (self.height - art_size) // 2
            art_x = start_x
            
            center_x = art_x + (art_size // 2)
            center_y = art_y + (art_size // 2)

            if self.vinyl_surface:
                # Rotate record smoothly (Negative value = clockwise spin)
                self.vinyl_rotation = (self.vinyl_rotation - 2.5) % 360
                
                # Draw the static shadow behind the spinning record
                if self.vinyl_shadow:
                    shadow_rect = self.vinyl_shadow.get_rect(center=(center_x + 8, center_y + 8))
                    self.screen.blit(self.vinyl_shadow, shadow_rect)
                
                # Spin it and center it perfectly
                rotated_vinyl = pygame.transform.rotozoom(self.vinyl_surface, self.vinyl_rotation, 1.0)
                vinyl_rect = rotated_vinyl.get_rect(center=(center_x, center_y))
                self.screen.blit(rotated_vinyl, vinyl_rect)
                
                # Draw the belt drive (motor pulley + belt) on top of the record edge
                self._draw_belt_drive(center_x, center_y, art_size)
                
                # Draw the tone arm resting on the record
                self._draw_tone_arm(center_x, center_y, art_size)
            else:
                # Fallback if no image is available
                pygame.draw.circle(self.screen, (30, 30, 30), (center_x, center_y), art_size // 2)
            
            # 3. Draw Synchronized Scrolling Text
            if self.scroller_group:
                self.scroller_group.draw(self.screen, self.text_x)

    def draw_frame(self):
        """Renders the UI elements based on current state."""
        
        # 1. Base Layer Rendering
        if self.fade_state in ['OUT', 'IN'] and self.fade_snapshot:
            self.screen.blit(self.fade_snapshot, (0, 0))
        else:
            self._render_live_frame()

        # ---------------------------------------------------------
        # 2. APPLY TWO-PHASE FADE OVERLAY
        # ---------------------------------------------------------
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

        flipped_screen = pygame.transform.flip(self.screen, True, True)
        self.hardware_screen.blit(flipped_screen, (0, 0))

        pygame.display.flip()
