import pygame
import common.logger_utils as logger_utils
import common.paths as paths
import display.display_utils as display_utils
from display.display_base import BaseNowPlayingDisplay

# Fetch our standardized native logger
logger = logger_utils.get_logger("Display")


class AlbumSleeveDisplay(BaseNowPlayingDisplay):
    """Default engine: the album art is shown as a flat sleeve with a spinning
    record peeking out from behind its top edge."""

    def _init_engine_state(self):
        """Album-sleeve-specific surfaces not present in the base."""
        self.art_surface = None
        self.vinyl_overlay = None

    def _build_art_surfaces(self, song_dict, raw_track, scaled_art, art_size):
        """Builds the rounded album-art sleeve plus the smaller peeking record."""
        self.art_surface = display_utils.round_corners(scaled_art, radius=8)

        artist_str = song_dict.get('artist', 'Unknown')
        album_str = song_dict.get('album', 'Unknown')
        if album_str and album_str != 'Unknown':
            cache_key = f"{artist_str}_{album_str}"
        else:
            cache_key = raw_track.get('key', song_dict.get('title'))
        display_utils.cache_album_art(cache_key, self.art_surface, paths.PROJECT_ROOT)

        vinyl_size = int(art_size * 0.95)
        self.vinyl_surface = display_utils.create_spinning_vinyl_base(
            vinyl_size, self.bg_color, self.color_primary, self.color_secondary
        )
        self.vinyl_overlay = display_utils.create_vinyl_overlay(vinyl_size)
        self.vinyl_shadow = display_utils.create_circular_shadow(vinyl_size)

    def _reset_art_surfaces(self):
        """Clears all sleeve/record surfaces when no art is available."""
        self.art_surface = None
        self.vinyl_surface = None
        self.vinyl_overlay = None
        self.vinyl_shadow = None

    def _draw_artwork(self, art_x, art_y, art_size):
        """Draws the peeking spinning record behind the flat album-art sleeve."""
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