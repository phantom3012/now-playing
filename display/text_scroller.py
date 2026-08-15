import pygame

class SyncedScrollGroup:
    def __init__(self, max_width, speed=3, pause_duration=40):
        """
        Creates a synchronized scrolling marquee for multiple text surfaces.
        Shorter lines are rendered statically. Longer lines scroll together,
        waiting for the longest line to finish before reversing.
        """
        self.max_width = max_width
        self.speed = speed
        self.pause_duration = pause_duration
        
        self.items = []
        self.max_scroll_range = 0
        self.needs_scroll = False
        
        # Shared global state (Frame-counting base)
        self.global_offset = 0
        self.state = 'START_PAUSE'
        self.pause_timer = 0
        
        # Caching variables
        self.cached_surface = None
        self.min_y = 0

    def add_text(self, font, text, color, y_pos):
        """Renders text and determines if it needs scrolling."""
        surface = font.render(text if text else "", True, color)
        width = surface.get_width()
        
        item_needs_scroll = width > self.max_width
        max_offset = max(0, width - self.max_width)
        
        self.items.append({
            'surface': surface,
            'width': width,
            'y': y_pos,
            'max_offset': max_offset,
            'needs_scroll': item_needs_scroll
        })
        
        # Only scale master constraints if the item actually overflows the viewport
        if item_needs_scroll:
            self.needs_scroll = True
            if max_offset > self.max_scroll_range:
                self.max_scroll_range = max_offset

    def build_cache(self):
        """Pre-renders static text layouts into a single optimized surface if no scrolling is required."""
        if self.needs_scroll or not self.items:
            return
        
        self.min_y = min(item['y'] for item in self.items)
        max_y = max(item['y'] + item['surface'].get_height() for item in self.items)
        height = max_y - self.min_y
        
        self.cached_surface = pygame.Surface((self.max_width, height), pygame.SRCALPHA)
        for item in self.items:
            self.cached_surface.blit(item['surface'], (0, item['y'] - self.min_y))

    def draw(self, screen, x):
        """Advances the state machine framework and draws the elements."""
        if self.cached_surface:
            screen.blit(self.cached_surface, (x, self.min_y))
            return
            
        # State machine running on deterministic frame counts
        if self.state == 'START_PAUSE':
            self.pause_timer += 1
            if self.pause_timer >= self.pause_duration:
                self.state = 'SCROLLING_FWD'
                self.pause_timer = 0
                
        elif self.state == 'SCROLLING_FWD':
            self.global_offset -= self.speed
            if -self.global_offset >= self.max_scroll_range:
                self.global_offset = -self.max_scroll_range
                self.state = 'END_PAUSE'
                
        elif self.state == 'END_PAUSE':
            self.pause_timer += 1
            if self.pause_timer >= self.pause_duration:
                self.state = 'SCROLLING_REV'
                self.pause_timer = 0
                
        elif self.state == 'SCROLLING_REV':
            self.global_offset += self.speed
            if self.global_offset >= 0:
                self.global_offset = 0
                self.state = 'START_PAUSE'

        if not self.items:
            return

        original_clip = screen.get_clip()
        min_y = min(item['y'] for item in self.items)
        max_y = max(item['y'] + item['surface'].get_height() for item in self.items)
        
        # Add small layout clipping padding for text safety
        pad = 8
        clip_rect = pygame.Rect(x, min_y - pad, self.max_width, (max_y - min_y) + (pad * 2))
        clip_rect = clip_rect.clip(screen.get_rect())
        screen.set_clip(clip_rect)
        
        for item in self.items:
            if item['needs_scroll']:
                # Synchronized alignment formula: Wait for global sweep window
                item_offset = max(-item['max_offset'], self.global_offset)
                screen.blit(item['surface'], (x + int(item_offset), item['y']))
            else:
                screen.blit(item['surface'], (x, item['y']))
                
        screen.set_clip(original_clip)