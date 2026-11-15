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

# Dynamic memory-safe scaling: On Render (512MB RAM ceiling) or low memory mode,
# render at 540x960 (2MB per frame instead of 8.3MB). FFmpeg upscales to 1080x1920
# in its C-filter graph with negligible RAM.
RENDER_SCALE = 0.5
RENDER_WIDTH = int(CANVAS_WIDTH * RENDER_SCALE)
RENDER_HEIGHT = int(CANVAS_HEIGHT * RENDER_SCALE)

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

class CharacterSceneRenderer:
    def __init__(self, scale: float = RENDER_SCALE):
        self.scale = scale
        self.render_width = int(CANVAS_WIDTH * self.scale)
        self.render_height = int(CANVAS_HEIGHT * self.scale)
        self.asset_dir = ASSETS_DIR
        self._load_assets()
        self._load_fonts()

    def _load_assets(self):
        """Loads and pre-scales base scene and mouth sprites for lean memory footprint."""
        base_path = self.asset_dir / "einstein_studio_base.png"
        mouth_open_path = self.asset_dir / "mouth_open.png"
        mouth_closed_path = self.asset_dir / "mouth_closed.png"

        if not base_path.exists():
            raise FileNotFoundError(f"Missing character base plate at {base_path}")

        raw_base = Image.open(base_path).convert("RGBA")
        raw_open = Image.open(mouth_open_path).convert("RGBA")
        raw_closed = Image.open(mouth_closed_path).convert("RGBA")

        if self.scale != 1.0:
            self.base_plate = raw_base.resize((self.render_width, self.render_height), Image.Resampling.LANCZOS)
            self.mouth_open = raw_open.resize((int(raw_open.width * self.scale), int(raw_open.height * self.scale)), Image.Resampling.LANCZOS)
            self.mouth_closed = raw_closed.resize((int(raw_closed.width * self.scale), int(raw_closed.height * self.scale)), Image.Resampling.LANCZOS)
        else:
            self.base_plate = raw_base
            self.mouth_open = raw_open
            self.mouth_closed = raw_closed
        
        # Aligned mouth anchor coordinates scaled
        self.mouth_pos = (int(150 * self.scale), int(750 * self.scale))

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
        target_size = max(18, int(68 * self.scale))
        for p in font_paths:
            try:
                self.sub_font = ImageFont.truetype(p, target_size)
                break
            except Exception:
                continue

        if not self.sub_font:
            self.sub_font = ImageFont.load_default()

        try:
            self.emoji_font = ImageFont.truetype("C:/Windows/Fonts/seguiemj.ttf", max(20, int(76 * self.scale)))
        except Exception:
            self.emoji_font = self.sub_font

    def _draw_subtitles_with_highlights(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        highlights: List[str],
        base_y: Optional[int] = None
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

        lines: List[List[Tuple[str, str]]] = []
        current_line: List[Tuple[str, str]] = []
        current_len = 0

        for w in words:
            clean_w = w.upper().strip(" .,!?:;")
            is_hl = clean_w in clean_highlights or (clean_w and clean_w[0].isdigit())
            color = "#FF3344" if is_hl else "#FFFFFF"

            if current_len + len(w) > 20 and current_line:
                lines.append(current_line)
                current_line = [(w, color)]
                current_len = len(w)
            else:
                current_line.append((w, color))
                current_len += len(w) + 1

        if current_line:
            lines.append(current_line)

        target_base_y = base_y if base_y is not None else int(1240 * self.scale)
        line_height = int(82 * self.scale)
        start_y = target_base_y - (len(lines) - 1) * (line_height // 2)

        for line_idx, line in enumerate(lines):
            line_y = start_y + line_idx * line_height
            
            line_widths = []
            space_w = draw.textlength(" ", font=self.sub_font)
            total_w = 0.0

            for word, _ in line:
                w_len = draw.textlength(word, font=self.sub_font)
                line_widths.append(w_len)
                total_w += w_len
            total_w += space_w * (len(line) - 1)

            curr_x = (self.render_width - total_w) / 2.0

            stroke_w = max(2, int(7 * self.scale))
            for (word, color), w_len in zip(line, line_widths):
                draw.text(
                    (curr_x, line_y),
                    word,
                    font=self.sub_font,
                    fill=color,
                    stroke_width=stroke_w,
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
        Renders a single memory-optimized video frame.
        """
        frame = self.base_plate.copy()

        if mouth_open:
            frame.paste(self.mouth_open, self.mouth_pos)
        else:
            frame.paste(self.mouth_closed, self.mouth_pos)

        draw = ImageDraw.Draw(frame)

        if eyes_blink:
            eye_w = max(2, int(6 * self.scale))
            draw.arc([int(235 * self.scale), int(785 * self.scale), int(275 * self.scale), int(805 * self.scale)], start=0, end=180, fill="#000000", width=eye_w)
            draw.arc([int(310 * self.scale), int(785 * self.scale), int(350 * self.scale), int(805 * self.scale)], start=0, end=180, fill="#000000", width=eye_w)

        if emoji_icon:
            emoji_x = int(490 * self.scale)
            emoji_y = int(1060 * self.scale)
            draw.text((emoji_x + 1, emoji_y + 1), emoji_icon, font=self.emoji_font, fill=(0, 0, 0, 140))
            draw.text((emoji_x, emoji_y), emoji_icon, font=self.emoji_font, fill=(255, 255, 255))

        if subtitle_text:
            self._draw_subtitles_with_highlights(
                draw=draw,
                text=subtitle_text,
                highlights=subtitle_highlights or [],
                base_y=int(1250 * self.scale)
            )

        return frame

character_renderer = CharacterSceneRenderer()
