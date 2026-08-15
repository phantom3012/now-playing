import os
import time
import random
import math
import pygame
import display_utils as du
from datetime import datetime


class ActiveAlbum:
    """State machine for a single animating album cover on the collage."""
    def __init__(self, image_dict, x, y, max_size, lifespan, screen_w, screen_h):
        self.base_image = image_dict['surface']
        self.image_path = image_dict['path'] 
        self.image_hash = image_dict.get('hash')
        self.x = float(x)
        self.y = float(y)
        self.max_size = max_size
        self.lifespan = lifespan
        
        self.screen_w = screen_w
        self.screen_h = screen_h
        
        self.age = 0.0
        self.state = 'INFLATING'  # INFLATING, HOLDING, DEFLATING, DEAD
        self.scale = 0.0
        self.fade_time = 1.5      # Time in seconds to grow/shrink
        
        # Determine randomized gentle translation speed and angle
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(8.0, 18.0) # Pixels per second (gentle drifting speed)
        self.vx = math.cos(angle) * speed
        self.vy = math.sin(angle) * speed
        
    def update(self, dt):
        """Updates internal scale, translates coordinates, and manages bounds during HOLDING."""
        self.age += dt
        
        if self.state == 'INFLATING':
            self.scale += dt / self.fade_time
            if self.scale >= 1.0:
                self.scale = 1.0
                self.state = 'HOLDING'
                
        elif self.state == 'HOLDING':
            # Translate position gently
            new_x = self.x + self.vx * dt
            new_y = self.y + self.vy * dt
            
            # Boundary guard: Keep within screensaver margins (15px padding)
            bounce_x = False
            bounce_y = False
            
            if new_x < 15 or new_x > self.screen_w - self.max_size - 15:
                bounce_x = True
            if new_y < 15 or new_y > self.screen_h - self.max_size - 15:
                bounce_y = True
                
            # Anti-Overlap Clock Guard: Keep away from the bottom-right clock area
            clock_rect = pygame.Rect(self.screen_w - 470, self.screen_h - 270, 470, 270)
            next_rect = pygame.Rect(new_x, new_y, self.max_size, self.max_size)
            if next_rect.colliderect(clock_rect):
                # Reverse both directions if heading into clock space
                bounce_x = True
                bounce_y = True
            
            # Apply translations or reverse vectors on bounce
            if bounce_x:
                self.vx = -self.vx
                self.x += self.vx * dt
            else:
                self.x = new_x
                
            if bounce_y:
                self.vy = -self.vy
                self.y += self.vy * dt
            else:
                self.y = new_y

            # Check if it's time to begin deflating (aging out)
            if self.age >= (self.lifespan - self.fade_time):
                self.state = 'DEFLATING'
                
        elif self.state == 'DEFLATING':
            self.scale -= dt / self.fade_time
            if self.scale <= 0.0:
                self.scale = 0.0
                self.state = 'DEAD'

    def draw(self, surface):
        """Calculates animations and renders the square."""
        if self.scale <= 0: 
            return
            
        # Smoothstep algorithm for organic easing (3x^2 - 2x^3)
        s = max(0.0, min(1.0, self.scale))
        smooth_s = s * s * (3 - 2 * s)
            
        current_size = int(self.max_size * smooth_s)
        if current_size <= 0: 
            return
            
        # Center the coordinate adjustments so it grows from the middle
        offset = (self.max_size - current_size) // 2
        draw_x = int(self.x) + offset
        draw_y = int(self.y) + offset
        
        try:
            scaled_img = pygame.transform.smoothscale(self.base_image, (current_size, current_size))
            # Set alpha smoothly for fade effects
            scaled_img.set_alpha(int(255 * max(0.0, min(1.0, smooth_s)))) 
            surface.blit(scaled_img, (draw_x, draw_y))
        except Exception:
            pass


class ClockScreensaver:
    def __init__(self, width, height, scale_factor, font_regular, font_italic):
        self.width = width
        self.height = height
        self.cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'album_cache')
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Font safely setup
        try:
            self.clock_font = pygame.font.Font(font_regular, int(80 * scale_factor))
            self.date_font = pygame.font.Font(font_regular, int(30 * scale_factor))
        except Exception:
            self.clock_font = pygame.font.SysFont('sans-serif', int(80 * scale_factor))
            self.date_font = pygame.font.SysFont('sans-serif', int(30 * scale_factor))
            
        self.active_albums = []
        self.cached_images = []
        
        # Grid parameters
        self.album_size = int(self.height * 0.25) 
        self.last_spawn_time = 0
        self.spawn_interval = 2.0 
        
        self.last_update_time = time.time()
        self.refresh_cache()
    
    def refresh_cache(self):
        """Scans the history directory and builds the surface pool."""
        self.cached_images = []
        if not os.path.exists(self.cache_dir): 
            return
            
        files = [os.path.join(self.cache_dir, f) for f in os.listdir(self.cache_dir) if f.endswith('.png')]
        for f in files:
            try:
                img = pygame.image.load(f).convert_alpha()
                # Pre-scale to maximum intended size for massive performance gains
                img = pygame.transform.smoothscale(img, (self.album_size, self.album_size))
                img_hash = du.get_surface_signature(img)
                # Store path along with the surface
                self.cached_images.append({'path': f, 'surface': img, 'hash': img_hash})
            except Exception:
                pass

    def _get_valid_position(self, size):
        """Rejection sampling to find an overlapping-free coordinate on the screen."""
        # Bounding box for the bottom-right clock (extended to prevent spawns in clock margin)
        clock_rect = pygame.Rect(self.width - 470, self.height - 270, 470, 270)
        
        for _ in range(50):  # Give up after 50 random attempts to save CPU
            x = random.randint(20, self.width - size - 20)
            y = random.randint(20, self.height - size - 20)
            test_rect = pygame.Rect(x, y, size, size)
            
            # Avoid Clock
            if test_rect.colliderect(clock_rect):
                continue
                
            # Avoid other active covers
            overlap = False
            for active in self.active_albums:
                active_rect = pygame.Rect(int(active.x), int(active.y), size, size)
                # Enforce a small padding gap on spawn
                if test_rect.colliderect(active_rect.inflate(30, 30)):
                    overlap = True
                    break
                    
            if not overlap:
                return x, y
                
        return None

    def _try_spawn(self):
        """Attempts to add a new image to the collage."""
        if len(self.active_albums) >= 8: 
            return
            
        active_hashes = {album.image_hash for album in self.active_albums if album.image_hash is not None}
        available_images = [img for img in self.cached_images if img.get('hash') not in active_hashes]
        
        if not available_images:
            return
            
        pos = self._get_valid_position(self.album_size)
        if pos:
            x, y = pos
            img_dict = random.choice(available_images)
            lifespan = random.uniform(11.0, 16.0) 
            self.active_albums.append(ActiveAlbum(img_dict, x, y, self.album_size, lifespan, self.width, self.height))

    def _draw_clock(self, surface, now):
        """Renders the date and time with a slow, subtle anti-burn-in jiggle."""
        jiggle_x = math.sin(now * 0.4) * 10
        jiggle_y = math.cos(now * 0.25) * 6
        
        base_x = self.width - 40
        base_y = self.height - 40
        
        time_str = datetime.now().strftime("%H:%M")
        date_str = datetime.now().strftime("%A, %b %d")
        
        time_color = (210, 210, 215)
        date_color = (160, 160, 165)
        
        time_surf = self.clock_font.render(time_str, True, time_color)
        date_surf = self.date_font.render(date_str, True, date_color)
        
        time_rect = time_surf.get_rect(bottomright=(base_x + jiggle_x, base_y - 45 + jiggle_y))
        date_rect = date_surf.get_rect(topright=(base_x + jiggle_x, time_rect.bottom + 5))
        
        # Render drop shadows behind text
        time_shadow = self.clock_font.render(time_str, True, (0, 0, 0))
        time_shadow.set_alpha(120)
        surface.blit(time_shadow, time_rect.move(3, 3))
        
        date_shadow = self.date_font.render(date_str, True, (0, 0, 0))
        date_shadow.set_alpha(120)
        surface.blit(date_shadow, date_rect.move(2, 2))
        
        surface.blit(time_surf, time_rect)
        surface.blit(date_surf, date_rect)

    def render(self, surface):
        """Main tick function called by the display engine."""
        now = time.time()
        dt = now - self.last_update_time
        self.last_update_time = now
        
        # Clean, dark background
        surface.fill((12, 12, 15)) 
        
        # Update and prune albums
        for album in self.active_albums[:]:
            album.update(dt)
            if album.state == 'DEAD':
                self.active_albums.remove(album)
                
        # Spawning mechanism to keep background active
        if self.cached_images:
            while len(self.active_albums) < 5:
                active_hashes = {album.image_hash for album in self.active_albums if album.image_hash is not None}
                available_images = [img for img in self.cached_images if img.get('hash') not in active_hashes]
                
                if not available_images:
                    break
                    
                pos = self._get_valid_position(self.album_size)
                if not pos:
                    break
                    
                x, y = pos
                img_dict = random.choice(available_images)
                lifespan = random.uniform(11.0, 16.0) 
                self.active_albums.append(ActiveAlbum(img_dict, x, y, self.album_size, lifespan, self.width, self.height))

            if len(self.active_albums) < 8 and (now - self.last_spawn_time > self.spawn_interval):
                self.spawn_interval = random.uniform(1.0, 3.5)
                self.last_spawn_time = now
                self._try_spawn()

        # Render active floating albums
        for album in self.active_albums:
            album.draw(surface)
                
        self._draw_clock(surface, now)
