"""
NapsterTec AI - 2D Animated Wisdom Character & Scene Renderer
Module: app/media/character_renderer.py

High-definition 1080x1920 vertical renderer matching @view.storm5:
- Authentic Einstein professor studio backdrop with drafting board and overhead lamp
- Synchronized mouth movement (open/talking vs closed/smiling)
- Subtle periodic blinking through black glasses
- High-retention Montserrat / Impact bold subtitles with thick black outline & red/white keyword highlighting
- Contextual emoji icons on drafting desk
"""
import os
import math
import logging
from pathlib import Path
from typing import Optional, List, Tuple
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

class CharacterSceneRenderer:
    def __init__(self):
        self.asset_dir = ASSETS_DIR
        self._load_assets()
        self._load_fonts()

    def _load_assets(self):
        """Loads the authentic high-definition base scene and mouth sprites."""
        base_path = self.asset_dir / "einstein_studio_base.png"
        mouth_open_path = self.asset_dir / "mouth_open.png"
        mouth_closed_path = self.asset_dir / "mouth_closed.png"

        if not base_path.exists():
            raise FileNotFoundError(f"Missing character base plate at {base_path}")

        self.base_plate = Image.open(base_path).convert("RGBA")
        self.mouth_open = Image.open(mouth_open_path).convert("RGBA")
        self.mouth_closed = Image.open(mouth_closed_path).convert("RGBA")
        
        # Aligned mouth anchor in 1080x1920 coordinates
        self.mouth_pos = (150, 750)

    def _load_fonts(self):
        """Loads available bold system fonts for subtitles and emojis."""
        font_paths = [
            "C:/Windows/Fonts/impact.ttf",
            "C:/Windows/Fonts/ariblk.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arial.ttf"
        ]
        self.sub_font = None
        for p in font_paths:
            try:
                self.sub_font = ImageFont.truetype(p, 68)
                break
            except Exception:
                continue

        if not self.sub_font:
            self.sub_font = ImageFont.load_default()

        try:
            self.emoji_font = ImageFont.truetype("C:/Windows/Fonts/seguiemj.ttf", 76)
        except Exception:
            self.emoji_font = self.sub_font

    def _draw_subtitles_with_highlights(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        highlights: List[str],
        base_y: int = 1240
    ):
        """
        Renders multi-word highlighted subtitles with thick black outlines,
        vibrant red on emphasized keywords, and white on standard text.
        """
        if not text:
            return

        words = text.strip().split()
        if not words:
            return

        clean_highlights = [h.upper().strip(" .,!?:;") for h in highlights]

        # Break words into lines if line length exceeds ~22 characters
        lines: List[List[Tuple[str, str]]] = []
        current_line: List[Tuple[str, str]] = []
        current_len = 0

        for w in words:
            clean_w = w.upper().strip(" .,!?:;")
            # Highlight if explicitly in list or starts with point numbering (e.g., '1.', '2.', '10.')
            is_hl = clean_w in clean_highlights or (clean_w and clean_w[0].isdigit())
            color = "#FF3344" if is_hl else "#FFFFFF" # Vibrant Red vs Pure White

            if current_len + len(w) > 20 and current_line:
                lines.append(current_line)
                current_line = [(w, color)]
                current_len = len(w)
            else:
                current_line.append((w, color))
                current_len += len(w) + 1

        if current_line:
            lines.append(current_line)

        # Render lines centered
        line_height = 82
        start_y = base_y - (len(lines) - 1) * (line_height // 2)

        for line_idx, line in enumerate(lines):
            line_y = start_y + line_idx * line_height
            
            # Calculate total line width
            line_widths = []
            space_w = draw.textlength(" ", font=self.sub_font)
            total_w = 0.0

            for word, _ in line:
                w_len = draw.textlength(word, font=self.sub_font)
                line_widths.append(w_len)
                total_w += w_len
            total_w += space_w * (len(line) - 1)

            # Center X
            curr_x = (CANVAS_WIDTH - total_w) / 2.0

            for (word, color), w_len in zip(line, line_widths):
                # Thick black outline
                draw.text(
                    (curr_x, line_y),
                    word,
                    font=self.sub_font,
                    fill=color,
                    stroke_width=7,
                    stroke_fill=(0, 0, 0)
                )
                curr_x += w_len + space_w

    def render_frame(
        self,
        mouth_open: bool = False,
        eyes_blink: bool = False,
        gesture_frame: int = 0,
        subtitle_text: str = "",
        subtitle_highlights: Optional[List[str]] = None,
        emoji_icon: Optional[str] = None
    ) -> Image.Image:
        """
        Renders a single 1080x1920 video frame matching @view.storm5.
        """
        # 1. Base Studio Plate
        frame = self.base_plate.copy()

        # 2. Character Facial Animation
        # Overlay mouth state
        if mouth_open:
            frame.paste(self.mouth_open, self.mouth_pos)
        else:
            frame.paste(self.mouth_closed, self.mouth_pos)

        draw = ImageDraw.Draw(frame)

        # 3. Eye Blinking (overlay eyelid arcs over glasses if blinking)
        if eyes_blink:
            # Left eye inside glasses
            draw.arc([235, 785, 275, 805], start=0, end=180, fill="#000000", width=6)
            # Right eye inside glasses
            draw.arc([310, 785, 350, 805], start=0, end=180, fill="#000000", width=6)

        # 4. Contextual Emoji Badge (Pops up above subtitle on table)
        if emoji_icon:
            emoji_x = 490
            emoji_y = 1060
            # Small shadow / backing
            draw.text((emoji_x + 2, emoji_y + 2), emoji_icon, font=self.emoji_font, fill=(0, 0, 0, 140))
            draw.text((emoji_x, emoji_y), emoji_icon, font=self.emoji_font, fill=(255, 255, 255))

        # 5. Viral Subtitles
        if subtitle_text:
            self._draw_subtitles_with_highlights(
                draw=draw,
                text=subtitle_text,
                highlights=subtitle_highlights or [],
                base_y=1250
            )

        return frame

# Singleton instance
character_renderer = CharacterSceneRenderer()
