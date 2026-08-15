import pygame

class SyncedScrollGroup:
    def __init__(self, max_width, speed=5, pause_duration=30):
        """
        Creates a synchronized scrolling marquee for multiple text surfaces.
        Shorter lines are rendered statically. Longer lines scroll together
        in a continuous wrap-around loop. Shorter scrolling lines will anchor
        in place while waiting for the longest line to finish.
        """
        self.max_width = max_width
        self.speed = speed
        self.pause_duration = pause_duration
        self.gap = 150  # Pixel padding between the end of text and the looped copy
        
        self.items = []
        self.max_text_width = 0
        self.needs_scroll = False
        
        # Shared global state (Frame-counting base)
        self.global_offset = 0.0
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
        
        self.items.append({
            'surface': surface,
            'width': width,
            'y': y_pos,
            'needs_scroll': item_needs_scroll,
            # Pre-calculate the item's individual cycle length
            'cycle_length': width + self.gap if item_needs_scroll else 0
        })
        
        # Only scale master constraints if the item actually overflows the viewport
        if item_needs_scroll:
            self.needs_scroll = True
            if width > self.max_text_width:
                self.max_text_width = width

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
            
        # The total distance to move before we seamlessly snap back to the start
        master_cycle_length = self.max_text_width + self.gap
            
        # State machine running on deterministic frame counts
        if self.state == 'START_PAUSE':
            self.pause_timer += 1
            if self.pause_timer >= self.pause_duration:
                self.state = 'SCROLLING'
                self.pause_timer = 0
                
        elif self.state == 'SCROLLING':
            self.global_offset -= self.speed
            # When the longest item completes a full cycle, snap back and pause!
            if -self.global_offset >= master_cycle_length:
                # Add master_cycle_length to preserve exact sub-pixel remainders
                self.global_offset += master_cycle_length
                self.state = 'START_PAUSE'

        if not self.items:
            return

        original_clip = screen.get_clip()
        min_y = min(item['y'] for item in self.items)
        max_y = max(item['y'] + item['surface'].get_height() for item in self.items)
        
        pad = 15
        clip_rect = pygame.Rect(int(x), int(min_y - pad), int(self.max_width), int((max_y - min_y) + (pad * 2)))
        clip_rect = clip_rect.clip(screen.get_rect())
        screen.set_clip(clip_rect)
        
        for item in self.items:
            if item['needs_scroll']:
                # The item anchors perfectly in place once it completes its own individual cycle!
                # By capping it against its own negative cycle length, the trailing copy stops exactly at x.
                item_offset = max(self.global_offset, -item['cycle_length'])
                
                # Draw the primary text moving left
                screen.blit(item['surface'], (int(x + item_offset), int(item['y'])))
                # Draw the trailing copy precisely one item cycle_length behind it
                screen.blit(item['surface'], (int(x + item_offset + item['cycle_length']), int(item['y'])))
            else:
                # Normal Text: Draw it statically at the original X coordinate
                screen.blit(item['surface'], (int(x), int(item['y'])))
                
        screen.set_clip(original_clip)

# =====================================================================
# STANDALONE INTEGRATION TEST
# Run `python text_scroller.py` to test the marquee behavior independently.
# =====================================================================
if __name__ == "__main__":
    def main_test():
        pygame.init()
        flags = pygame.FULLSCREEN | pygame.HWSURFACE | pygame.DOUBLEBUF
        
        # We use a hardware screen for output, and a software screen for flipping
        hardware_screen = pygame.display.set_mode((1280, 720), flags)
        software_screen = pygame.Surface((1280, 720)).convert()
        
        pygame.display.set_caption("Text Scroller Isolated Test")
        clock = pygame.time.Clock()

        font_title = pygame.font.SysFont('sans-serif', 64)
        font_meta = pygame.font.SysFont('sans-serif', 36)

        scroller = SyncedScrollGroup(max_width=400, speed=2, pause_duration=40)

        # 1. Short line (Should stay perfectly static)
        scroller.add_text(font_title, "Short Title", (255, 255, 255), 200)
        
        # 2. Very long line (The master string)
        scroller.add_text(font_meta, "This is a ridiculously long artist name to test the continuous marquee wrap-around effect", (200, 200, 200), 280)
        
        # 3. Medium long line (Will wrap around earlier, then wait for the artist name to finish)
        scroller.add_text(font_meta, "The Greatest Hits Album (Deluxe Edition)", (150, 150, 150), 330)

        # 4. Short line
        scroller.add_text(font_meta, "2024", (100, 100, 100), 380)

        scroller.build_cache()

        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False

            software_screen.fill((20, 20, 20))
            
            # Draw debug box
            pygame.draw.rect(software_screen, (50, 50, 50), (440, 180, 400, 250), 2)
            scroller.draw(software_screen, 440)

            # INVERT DISPLAY ONLY FOR ISOLATED TEST
            flipped_screen = pygame.transform.flip(software_screen, True, True)
            hardware_screen.blit(flipped_screen, (0, 0))

            pygame.display.flip()
            clock.tick(30) 

        pygame.quit()

    main_test()