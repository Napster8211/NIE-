"""
NapsterTec AI - 2D Animated Wisdom Character & Scene Renderer
Module: app/media/character_renderer.py

Renders 1080x1920 vertical canvas scenes matching @view.storm5:
- Office / architect studio backdrop with drafting table and lamp
- 2D animated professor character with synchronized mouth cycles & blinks
- Word-level highlighted subtitles in high-contrast pill
- Animated emoji badges
"""
import math
import logging
from typing import Optional, List, Tuple
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920

class CharacterSceneRenderer:
    def __init__(self):
        # Base background caching to maximize rendering FPS
        self._cached_bg: Optional[Image.Image] = None
        self._load_fonts()

    def _load_fonts(self):
        """Loads available bold system fonts or falls back cleanly."""
        font_paths = [
            "C:/Windows/Fonts/impact.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/trebucbd.ttf",
            "C:/Windows/Fonts/arial.ttf"
        ]
        self.sub_font = None
        self.sub_font_large = None
        self.emoji_font = None
        
        for p in font_paths:
            try:
                self.sub_font = ImageFont.truetype(p, 54)
                self.sub_font_large = ImageFont.truetype(p, 64)
                break
            except Exception:
                continue
                
        if not self.sub_font:
            self.sub_font = ImageFont.load_default()
            self.sub_font_large = self.sub_font

        # Emoji font for Windows
        try:
            self.emoji_font = ImageFont.truetype("C:/Windows/Fonts/seguiemj.ttf", 68)
        except Exception:
            self.emoji_font = self.sub_font

    def render_background(self) -> Image.Image:
        """Generates the modern architect studio / office backdrop."""
        if self._cached_bg is not None:
            return self._cached_bg.copy()

        bg = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), "#60A5C2") # Soft slate blue wall
        draw = ImageDraw.Draw(bg)

        # 1. Back Office Window (Left Side)
        draw.rectangle([60, 420, 480, 1150], fill="#C5E5F5", outline="#3B7D9C", width=6)
        # Window panes
        draw.line([270, 420, 270, 1150], fill="#FFFFFF", width=8)
        draw.line([60, 785, 480, 785], fill="#FFFFFF", width=8)
        # Outside skyline tint
        draw.polygon([(60, 950), (200, 880), (320, 930), (480, 860), (480, 1150), (60, 1150)], fill="#97C8E2")

        # 2. Ceiling Lamp (Hanging from top right)
        draw.line([880, 0, 880, 520], fill="#1C2833", width=8) # Lamp wire
        # Lamp shade
        draw.polygon([(820, 570), (940, 570), (900, 520), (860, 520)], fill="#2C3E50")
        # Soft light beam
        light_beam = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        lb_draw = ImageDraw.Draw(light_beam)
        lb_draw.polygon([(840, 570), (920, 570), (1080, 1500), (620, 1500)], fill=(255, 255, 230, 40))
        bg = Image.alpha_composite(bg, light_beam)
        draw = ImageDraw.Draw(bg)

        # 3. Architect Drafting Table (Right Side, Angled)
        # Wooden table legs
        draw.polygon([(680, 1150), (710, 1150), (630, 1650), (600, 1650)], fill="#2C3E50")
        draw.polygon([(980, 1150), (1010, 1150), (1050, 1650), (1020, 1650)], fill="#2C3E50")
        draw.line([620, 1420, 1030, 1420], fill="#1C2833", width=12) # Crossbar

        # Slanted Board Surface
        draw.polygon([(460, 1020), (1080, 850), (1080, 1220), (460, 1390)], fill="#EAE6DA", outline="#B59A72", width=14)
        # Black drafting tray / ruler on table
        draw.polygon([(620, 1260), (920, 1180), (925, 1220), (625, 1300)], fill="#2C3E50")

        # 4. Floor Line
        draw.rectangle([0, 1620, CANVAS_WIDTH, CANVAS_HEIGHT], fill="#417D9A")
        draw.line([0, 1620, CANVAS_WIDTH, 1620], fill="#274D5E", width=8)

        self._cached_bg = bg
        return bg.copy()

    def draw_character(
        self,
        canvas: Image.Image,
        mouth_open: bool = False,
        eyes_blink: bool = False,
        gesture_frame: int = 0
    ):
        """Draws the 2D cartoon professor character on the stool."""
        draw = ImageDraw.Draw(canvas)

        # Character Anchor Coordinates
        cx, cy = 290, 960 # Head center

        # 1. Stool (Below Professor)
        draw.ellipse([cx - 90, cy + 540, cx + 90, cy + 580], fill="#2C3E50") # Stool cushion
        draw.line([cx - 60, cy + 570, cx - 80, cy + 780], fill="#1C2833", width=8)
        draw.line([cx + 60, cy + 570, cx + 80, cy + 780], fill="#1C2833", width=8)
        draw.line([cx, cy + 570, cx, cy + 780], fill="#1C2833", width=8)

        # 2. Body / Torso (Dark Grey Sweater)
        draw.rounded_rectangle([cx - 100, cy + 200, cx + 100, cy + 550], radius=50, fill="#3D3D3D", outline="#1F1F1F", width=6)

        # 3. Stick Legs & Feet
        draw.line([cx - 45, cy + 530, cx - 45, cy + 720], fill="#1F1F1F", width=8)
        draw.line([cx + 45, cy + 530, cx + 45, cy + 720], fill="#1F1F1F", width=8)
        draw.ellipse([cx - 70, cy + 710, cx - 20, cy + 735], fill="#1A1A1A") # Shoes
        draw.ellipse([cx + 20, cy + 710, cx + 70, cy + 735], fill="#1A1A1A")

        # 4. Stick Arms & Hands
        # Subtle breathing/gesture movement
        hand_offset = int(math.sin(gesture_frame * 0.1) * 8)
        draw.line([cx - 95, cy + 240, cx - 130, cy + 390 + hand_offset], fill="#1F1F1F", width=7)
        draw.line([cx - 130, cy + 390 + hand_offset, cx - 40, cy + 450], fill="#1F1F1F", width=7)
        
        draw.line([cx + 95, cy + 240, cx + 150, cy + 380 - hand_offset], fill="#1F1F1F", width=7)
        draw.line([cx + 150, cy + 380 - hand_offset, cx + 60, cy + 450], fill="#1F1F1F", width=7)
        draw.ellipse([cx - 50, cy + 440, cx - 20, cy + 470], fill="#FCD0A1") # Hand left
        draw.ellipse([cx + 20, cy + 440, cx + 50, cy + 470], fill="#FCD0A1") # Hand right

        # 5. Head (Warm peach oval)
        draw.ellipse([cx - 110, cy - 110, cx + 110, cy + 120], fill="#FCD0A1", outline="#1F1F1F", width=6)

        # 6. Bushy White Hair
        hair_color = "#FFFFFF"
        # Hair puffs on sides and top
        draw.ellipse([cx - 145, cy - 60, cx - 80, cy + 60], fill=hair_color, outline="#1F1F1F", width=5)
        draw.ellipse([cx + 80, cy - 60, cx + 145, cy + 60], fill=hair_color, outline="#1F1F1F", width=5)
        draw.ellipse([cx - 135, cy - 110, cx - 60, cy - 20], fill=hair_color, outline="#1F1F1F", width=5)
        draw.ellipse([cx + 60, cy - 110, cx + 135, cy - 20], fill=hair_color, outline="#1F1F1F", width=5)
        draw.ellipse([cx - 90, cy - 140, cx + 90, cy - 70], fill=hair_color, outline="#1F1F1F", width=5) # Top crown

        # 7. Big Round Glasses & Bridge
        # Left eye glass
        draw.ellipse([cx - 85, cy - 40, cx - 15, cy + 30], fill="#FFFFFF", outline="#1A1A1A", width=7)
        # Right eye glass
        draw.ellipse([cx + 15, cy - 40, cx + 85, cy + 30], fill="#FFFFFF", outline="#1A1A1A", width=7)
        # Glass bridge
        draw.line([cx - 15, cy - 5, cx + 15, cy - 5], fill="#1A1A1A", width=7)

        # 8. Eyes (Open or Blinking)
        if eyes_blink:
            draw.line([cx - 65, cy - 5, cx - 35, cy - 5], fill="#1A1A1A", width=6)
            draw.line([cx + 35, cy - 5, cx + 65, cy - 5], fill="#1A1A1A", width=6)
        else:
            # Black pupils
            draw.ellipse([cx - 58, cy - 15, cx - 42, cy + 5], fill="#1A1A1A")
            draw.ellipse([cx + 42, cy - 15, cx + 58, cy + 5], fill="#1A1A1A")
            # White catchlights
            draw.ellipse([cx - 55, cy - 12, cx - 48, cy - 5], fill="#FFFFFF")
            draw.ellipse([cx + 45, cy - 12, cx + 52, cy - 5], fill="#FFFFFF")

        # 9. White Eyebrows
        draw.arc([cx - 85, cy - 65, cx - 15, cy - 35], start=190, end=350, fill="#FFFFFF", width=8)
        draw.arc([cx + 15, cy - 65, cx + 85, cy - 35], start=190, end=350, fill="#FFFFFF", width=8)

        # 10. Animated Mouth (Under Mustache)
        if mouth_open:
            # Talking open mouth
            draw.ellipse([cx - 30, cy + 45, cx + 30, cy + 90], fill="#A93226", outline="#1F1F1F", width=5)
            draw.chord([cx - 20, cy + 65, cx + 20, cy + 90], start=0, end=180, fill="#E74C3C") # Tongue
        else:
            # Idle mouth
            draw.arc([cx - 22, cy + 55, cx + 22, cy + 75], start=10, end=170, fill="#1F1F1F", width=5)

        # 11. Iconic Bushy White Mustache (Over mouth)
        draw.ellipse([cx - 65, cy + 18, cx + 5, cy + 62], fill=hair_color, outline="#1F1F1F", width=5)
        draw.ellipse([cx - 5, cy + 18, cx + 65, cy + 62], fill=hair_color, outline="#1F1F1F", width=5)
        draw.ellipse([cx - 16, cy + 10, cx + 16, cy + 38], fill="#FCD0A1", outline="#1F1F1F", width=4) # Nose

    def draw_subtitles(
        self,
        canvas: Image.Image,
        text: str,
        highlights: List[str],
        emoji_icon: Optional[str] = None
    ):
        """Draws dynamic word-level highlighted subtitles in high-contrast pill."""
        if not text:
            return

        draw = ImageDraw.Draw(canvas)
        words = text.split()
        
        # Calculate text segment widths
        space_w = draw.textlength(" ", font=self.sub_font)
        word_metrics = []
        total_w = 0
        
        for w in words:
            clean_w = w.strip('.,!?"\'').upper()
            w_len = draw.textlength(w, font=self.sub_font)
            is_hl = any(clean_w == hl.upper() for hl in highlights)
            word_metrics.append((w, w_len, is_hl))
            total_w += w_len + space_w

        total_w -= space_w # Remove trailing space
        
        # Subtitle Box Position
        box_y = 1340
        box_h = 90
        pad_x = 36
        
        start_x = (CANVAS_WIDTH - total_w) / 2
        box_x1 = max(40, start_x - pad_x)
        box_x2 = min(CANVAS_WIDTH - 40, start_x + total_w + pad_x)

        # Draw black pill background with subtle glow
        draw.rounded_rectangle([box_x1 - 4, box_y - 4, box_x2 + 4, box_y + box_h + 4], radius=24, fill="#000000")
        draw.rounded_rectangle([box_x1, box_y, box_x2, box_y + box_h], radius=20, fill="#121212")

        # Draw words with custom colors
        curr_x = start_x
        for w, w_len, is_hl in word_metrics:
            # Highlight colors: Cyan for primary keyword, or Red for alert words
            color = "#00E5FF" if is_hl else "#FFFFFF"
            if is_hl and any(kw in w.upper() for kw in ("PAIN", "DISCIPLINE", "NOT", "WITHOUT", "ALONE")):
                color = "#FF3B30" # Bold energetic red

            draw.text((curr_x, box_y + 14), w, fill=color, font=self.sub_font)
            curr_x += w_len + space_w

        # Draw emoji badge if present
        if emoji_icon:
            emoji_x = int(box_x2 - 30)
            emoji_y = int(box_y - 50)
            # Yellow/White circular background badge
            draw.ellipse([emoji_x - 10, emoji_y - 10, emoji_x + 80, emoji_y + 80], fill="#FFFFFF", outline="#1F1F1F", width=4)
            draw.text((emoji_x, emoji_y), emoji_icon, fill="#000000", font=self.emoji_font)

    def render_frame(
        self,
        mouth_open: bool = False,
        eyes_blink: bool = False,
        gesture_frame: int = 0,
        subtitle_text: str = "",
        subtitle_highlights: Optional[List[str]] = None,
        emoji_icon: Optional[str] = None
    ) -> Image.Image:
        """Composes and returns a complete 1080x1920 video frame."""
        frame = self.render_background()
        self.draw_character(frame, mouth_open=mouth_open, eyes_blink=eyes_blink, gesture_frame=gesture_frame)
        if subtitle_text:
            self.draw_subtitles(
                frame,
                text=subtitle_text,
                highlights=subtitle_highlights or [],
                emoji_icon=emoji_icon
            )
        return frame

# Singleton instance
character_renderer = CharacterSceneRenderer()
