import os
import time
import requests
import io
import math
import pygame
import logger_utils

# Fetch our standardized native logger
logger = logger_utils.get_logger("DisplayUtils")

def get_surface_signature(surface):
    """
    Generates a fast, robust visual signature of a Pygame surface.
    Downscales to 8x8 to compare visual layout regardless of size, 
    format, or file name.
    """
    try:
        small = pygame.transform.smoothscale(surface, (8, 8))
        try:
            pixel_bytes = pygame.image.tobytes(small, "RGB")
        except AttributeError:
            pixel_bytes = pygame.image.tostring(small, "RGB")
        return hash(pixel_bytes)
    except Exception:
        return None

def download_image(url):
    """Downloads an image from a URL and returns raw bytes."""
    if not url:
        return None
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        return response.content
    except Exception as e:
        logger.error(f"Error downloading image: {e}")
        return None

def load_image_from_bytes(image_bytes):
    """Converts raw bytes into a Pygame Surface."""
    try:
        image_file = io.BytesIO(image_bytes)
        return pygame.image.load(image_file).convert_alpha()
    except Exception as e:
        logger.error(f"Error loading image from bytes: {e}")
        return None

def extract_dominant_color(surface):
    """Averages the colors of an image to find the dominant color."""
    if not surface:
        return (50, 50, 50)
    try:
        avg_surface = pygame.transform.smoothscale(surface, (1, 1))
        color = avg_surface.get_at((0, 0))
        return (color.r, color.g, color.b)
    except Exception:
        return (50, 50, 50)

def blur_surface(surface, amt):
    """A simple and fast box blur for Pygame surfaces."""
    if amt < 1:
        return surface
    scale = 1.0 / float(amt)
    try:
        surf_size = surface.get_size()
        small_surf = pygame.transform.smoothscale(surface, (max(1, int(surf_size[0] * scale)), max(1, int(surf_size[1] * scale))))
        return pygame.transform.smoothscale(small_surf, surf_size)
    except Exception as e:
        logger.error(f"Error blurring surface: {e}")
        return surface

def hex_to_rgb(hx):
    """Converts hex color string to RGB tuple."""
    return tuple(int(hx[i:i+2], 16) for i in (0, 2, 4))

def avg_color(c1, c2):
    """Computes the average of two RGB colors."""
    return tuple((a + b) // 2 for a, b in zip(c1, c2))

def parse_joecolor(joecolor_str):
    """Parses Apple Music joecolor palette string."""
    colors = {}
    if not joecolor_str: 
        return colors
    try:
        parts = joecolor_str.split(':')
        if len(parts) >= 6:
            colors['b'] = hex_to_rgb(parts[1][:6])
            colors['p'] = hex_to_rgb(parts[2][:6])
            colors['s'] = hex_to_rgb(parts[3][:6])
            colors['t'] = hex_to_rgb(parts[4][:6])
            colors['q'] = hex_to_rgb(parts[5][:6])
    except Exception:
        pass
    return colors

def force_square_crop(surface):
    """Forces a square crop of a Pygame surface."""
    w, h = surface.get_size()
    min_dim = min(w, h)
    x_offset = (w - min_dim) // 2
    y_offset = (h - min_dim) // 2
    square_rect = pygame.Rect(x_offset, y_offset, min_dim, min_dim)
    return surface.subsurface(square_rect).copy()

def round_corners(surface, radius):
    """Applies a smooth corner radius mask to a Pygame surface."""
    size = surface.get_size()
    mask = pygame.Surface(size, pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, *size), border_radius=radius)
    rounded_surface = surface.copy().convert_alpha()
    rounded_surface.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return rounded_surface

def generate_glow_base(width, height):
    """Pre-renders a massive radial gradient for the full-screen pulsing background."""
    radius = int(math.hypot(width // 2, height // 2))
    surface = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
    
    for i in range(radius, 0, -3):
        falloff = 1 - (i / radius) ** 2.5
        alpha = int(255 * falloff)
        pygame.draw.circle(surface, (180, 190, 210, alpha), (radius, radius), i)
        
    return surface

def create_spinning_vinyl_base(size, bg_color, color_primary, color_secondary):
    """Creates the rotating colored pie-chart layer of the record."""
    vinyl = pygame.Surface((size, size), pygame.SRCALPHA)
    center = (size // 2, size // 2)
    radius = size // 2
    
    num_peaks = 8
    for angle in range(360):
        rad = math.radians(angle * num_peaks)
        t = (math.sin(rad) + 1.0) / 2.0
        
        r = int(bg_color[0] * (1-t) + color_primary[0] * t)
        g = int(bg_color[1] * (1-t) + color_primary[1] * t)
        b = int(bg_color[2] * (1-t) + color_primary[2] * t)
        
        rad1 = math.radians(angle - 90)
        rad2 = math.radians(angle - 90 + 1.5)
        
        p1 = center
        p2 = (center[0] + radius * math.cos(rad1), center[1] + radius * math.sin(rad1))
        p3 = (center[0] + radius * math.cos(rad2), center[1] + radius * math.sin(rad2))
        
        pygame.draw.polygon(vinyl, (r, g, b, 255), [p1, p2, p3])

    mask = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.circle(mask, (255, 255, 255, 255), center, radius)
    vinyl.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    pygame.draw.circle(vinyl, (0, 0, 0, 150), center, radius, width=2)
        
    label_radius = int(radius * 0.35)
    label_surface = pygame.Surface((label_radius * 2, label_radius * 2), pygame.SRCALPHA)
    lbl_center = (label_radius, label_radius)
    
    pygame.draw.circle(label_surface, color_secondary[:3] + (255,), lbl_center, label_radius)
    bottom_rect = pygame.Rect(0, label_radius, label_radius * 2, label_radius)
    pygame.draw.rect(label_surface, bg_color[:3] + (255,), bottom_rect)
    
    stripe_height = int(label_radius * 0.3)
    stripe_rect = pygame.Rect(0, label_radius - (stripe_height // 2), label_radius * 2, stripe_height)
    pygame.draw.rect(label_surface, color_primary[:3] + (255,), stripe_rect)
    
    mask_lbl = pygame.Surface((label_radius * 2, label_radius * 2), pygame.SRCALPHA)
    pygame.draw.circle(mask_lbl, (255, 255, 255, 255), lbl_center, label_radius)
    label_surface.blit(mask_lbl, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    
    vinyl.blit(label_surface, (center[0] - label_radius, center[1] - label_radius))
    pygame.draw.circle(vinyl, (0, 0, 0, 150), center, label_radius - 2, width=2)
    
    return vinyl

def create_vinyl_overlay(size):
    """Creates the static overlay (just grooves)."""
    overlay = pygame.Surface((size, size), pygame.SRCALPHA)
    center = (size // 2, size // 2)
    radius = size // 2
    
    for r in range(int(radius * 0.35), int(radius * 0.95), 8):
        pygame.draw.circle(overlay, (0, 0, 0, 60), center, r, width=2)
        pygame.draw.circle(overlay, (255, 255, 255, 15), center, r-1, width=1)
        
    pygame.draw.circle(overlay, (0, 0, 0, 80), center, int(radius * 0.8), width=3)
    pygame.draw.circle(overlay, (0, 0, 0, 80), center, int(radius * 0.5), width=2)
    
    mask = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.circle(mask, (255, 255, 255, 255), center, radius)
    overlay.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    
    pygame.draw.circle(overlay, (20, 20, 20, 255), center, int(radius * 0.05))
    pygame.draw.circle(overlay, (0, 0, 0, 255), center, int(radius * 0.05), width=2)
    
    return overlay

def create_circular_shadow(size):
    """Creates a soft circular drop shadow for the spinning record."""
    shadow_size = size + 30
    shadow = pygame.Surface((shadow_size, shadow_size), pygame.SRCALPHA)
    center = (shadow_size // 2, shadow_size // 2)
    
    for i in range(15):
        alpha = max(0, 80 - (i * 6))
        pygame.draw.circle(shadow, (0, 0, 0, alpha), center, (size // 2) + i)
        
    return shadow

def create_animated_gradient(width, height, theme_colors):
    """Creates a low-res gradient surface stretched smoothly across coordinates."""
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
            
            r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
            grad.set_at((x, y), (r, g, b))
    
    return pygame.transform.smoothscale(grad, (int(width * 1.2), int(height * 1.2)))

def cache_album_art(cache_key, surface, base_dir):
    """Saves the current album art to a rolling cache directory for the collage screensaver."""
    try:
        cache_dir = os.path.join(base_dir, 'album_cache')
        os.makedirs(cache_dir, exist_ok=True)
        
        safe_key = "".join(c for c in str(cache_key) if c.isalnum())
        if not safe_key:
            safe_key = str(int(time.time()))
            
        file_path = os.path.join(cache_dir, f"{safe_key}.png")
        
        if not os.path.exists(file_path):
            files = [os.path.join(cache_dir, f) for f in os.listdir(cache_dir) if f.endswith('.png')]
            if len(files) >= 50:
                files.sort(key=lambda x: os.path.getmtime(x))
                oldest_file = files[0]
                os.remove(oldest_file)
                logger.info(f"Cache full! Deleted oldest album art to make room: {oldest_file}")
                
            pygame.image.save(surface, file_path)
            logger.info(f"Cached new album art for collage: {file_path}")
    except Exception as e:
        logger.error(f"Failed to cache album art: {e}")

def create_refresh_spinner(angle, radius=16, text="", font=None, color=(255, 255, 255)):
    """Generates a subtle rotating spinner surface. Theme color can be passed to override white."""
    if text:
        radius = max(radius, 16) 
        
    size = (radius * 2) + 16
    spinner_surf = pygame.Surface((size, size), pygame.SRCALPHA)
    center = (size // 2, size // 2)
    
    # Subtle background ring matching the target color with 40/255 alpha opacity
    pygame.draw.circle(spinner_surf, color[:3] + (40,), center, radius, 2)
    
    # Highlighted rotating arc (approx 120 degrees) matching the target color with 200/255 alpha opacity
    end_rad = angle + (math.pi * 0.6)
    pygame.draw.arc(spinner_surf, color[:3] + (200,), (center[0]-radius, center[1]-radius, radius*2, radius*2), angle, end_rad, 2)
    
    # Draw retry text perfectly centered inside the ring
    if text and font:
        text_surf = font.render(text, True, color)
        text_surf.set_alpha(200)
        text_rect = text_surf.get_rect(center=center)
        spinner_surf.blit(text_surf, text_rect)
        
    return spinner_surf