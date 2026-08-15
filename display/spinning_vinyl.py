import math
import pygame
import common.logger_utils as logger_utils
import display.display_utils as display_utils
from display.display_base import BaseNowPlayingDisplay

# Fetch our standardized native logger
logger = logger_utils.get_logger("Display")


class SpinningVinylDisplay(BaseNowPlayingDisplay):
    """Alternate engine: the album art becomes a large spinning picture-disc
    record, with a belt-drive motor and tonearm animation."""

    # ---------------------------------------------------------
    # ENGINE-SPECIFIC ART CONSTRUCTION
    # ---------------------------------------------------------

    def _build_art_surfaces(self, song_dict, raw_track, scaled_art, art_size):
        """Turns the album art into a circular picture-disc record + shadow."""
        self.vinyl_surface = self._create_vinyl_record(scaled_art, art_size)
        self.vinyl_shadow = display_utils.create_circular_shadow(art_size)

    def _reset_art_surfaces(self):
        """Clears the record surfaces when no art is available."""
        self.vinyl_surface = None
        self.vinyl_shadow = None

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
    # LEFT-SIDE GRAPHIC (called by the shared render loop)
    # ---------------------------------------------------------

    def _draw_artwork(self, art_x, art_y, art_size):
        """Draws the spinning picture-disc record with belt drive and tonearm."""
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