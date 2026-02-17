#!/usr/bin/env python3
"""Generate PWA icons for Sentient Core web chat."""

from PIL import Image, ImageDraw
import math
import os

def create_icon(size, output_path):
    """Create a hexagonal Cortana logo icon."""
    # Dark background
    img = Image.new('RGBA', (size, size), (10, 14, 23, 255))
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2
    r = int(size * 0.42)

    # Draw hexagon outline with glow effect
    points = []
    for i in range(6):
        angle = math.radians(60 * i - 30)
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))

    # Outer glow (multiple passes for glow effect)
    for offset in range(-3, 4):
        pts = []
        for i in range(6):
            angle = math.radians(60 * i - 30)
            ro = r + offset
            pts.append((cx + ro * math.cos(angle), cy + ro * math.sin(angle)))
        alpha = max(100, 255 - abs(offset) * 40)
        draw.polygon(pts, outline=(0, 212, 255, alpha))

    # Inner circle (AI core)
    cr = int(size * 0.15)
    core_width = max(2, size // 128)
    draw.ellipse([cx-cr, cy-cr, cx+cr, cy+cr],
                 outline=(0, 212, 255, 200),
                 width=core_width)

    # Center dot (bright core)
    dr = int(size * 0.05)
    draw.ellipse([cx-dr, cy-dr, cx+dr, cy+dr],
                 fill=(0, 212, 255, 255))

    # Orbital ring
    or_r = int(size * 0.28)
    ring_width = max(1, size // 256)
    draw.ellipse([cx-or_r, cy-or_r, cx+or_r, cy+or_r],
                 outline=(0, 212, 255, 100),
                 width=ring_width)

    # Secondary orbital ring (offset)
    or_r2 = int(size * 0.32)
    draw.ellipse([cx-or_r2, cy-or_r2, cx+or_r2, cy+or_r2],
                 outline=(0, 212, 255, 60),
                 width=ring_width)

    img.save(output_path, 'PNG')
    print(f"Created {output_path} ({size}x{size})")

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))

    create_icon(192, os.path.join(script_dir, 'icon-192.png'))
    create_icon(512, os.path.join(script_dir, 'icon-512.png'))

    print("Icons generated successfully!")
