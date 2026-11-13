"""
NapsterTec AI - 2D Animated Video Compiler Engine
Module: app/media/video_compiler.py

Synthesizes ElevenLabs audio narration, aligns subtitle timings, and pipes
rendered 1080x1920 frames into FFmpeg to compile high-definition MP4 videos for @view.storm5.
"""
import os
import re
import sys
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

from app.media.wisdom_script_engine import WisdomScript, WisdomItem, SubtitleChunk
from app.media.character_renderer import character_renderer, CANVAS_WIDTH, CANVAS_HEIGHT

logger = logging.getLogger(__name__)

from dotenv import load_dotenv
load_dotenv()

import imageio_ffmpeg

def get_ffmpeg_path() -> str:
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "storage" / "rendered_videos"
DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

class VideoCompiler:
    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg_path = get_ffmpeg_path()

    def get_audio_duration_seconds(self, audio_path: str) -> float:
        """Extracts exact audio duration in seconds using FFmpeg stderr output."""
        cmd = [self.ffmpeg_path, "-i", audio_path]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr)
        if match:
            hours, minutes, seconds = match.groups()
            return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
        return 15.0 # Fallback default

    async def generate_narration_audio(self, script: WisdomScript) -> Tuple[str, float]:
        """Synthesizes voiceover narration using ElevenLabs and returns (audio_path, duration)."""
        from app.services.director_voice_service import DirectorVoiceService
        
        # Combine hook and items into one coherent spoken text
        spoken_segments = [script.hook]
        for item in script.items:
            spoken_segments.append(item.spoken_text)
        
        full_text = " ".join(spoken_segments)
        # Cap length if exceeding single-call safety bounds
        if len(full_text) > 4000:
            full_text = full_text[:4000]

        temp_audio = self.output_dir / "temp_narration.mp3"
        
        try:
            voice_service = DirectorVoiceService()
            logger.info(f"[VideoCompiler] Synthesizing ElevenLabs narration ({len(full_text)} chars)...")
            audio_res = await voice_service.synthesize_text(full_text)
            
            with open(temp_audio, "wb") as f:
                f.write(audio_res.audio_bytes)
            logger.info("[VideoCompiler] ElevenLabs voiceover synthesized successfully.")
        except Exception as e:
            logger.warning(f"[VideoCompiler] ElevenLabs fallback notice: {e}. Generating audio track via synthesizer...")
            # Generate a clean audio tone fallback if API offline
            self._generate_ambient_audio(str(temp_audio), duration_sec=20.0)

        duration = self.get_audio_duration_seconds(str(temp_audio))
        logger.info(f"[VideoCompiler] Audio duration: {duration:.2f} seconds.")
        return str(temp_audio), duration

    def _generate_ambient_audio(self, output_path: str, duration_sec: float = 15.0):
        """Generates a clean silent/ambient audio track using FFmpeg as a fallback."""
        cmd = [
            self.ffmpeg_path, "-y",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
            "-t", str(duration_sec),
            "-c:a", "libmp3lame", output_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _flatten_timeline_chunks(self, script: WisdomScript, total_duration: float) -> List[Dict[str, Any]]:
        """Maps subtitle chunks evenly across the duration timeline."""
        all_chunks: List[SubtitleChunk] = []
        
        # Hook chunk
        all_chunks.append(SubtitleChunk(text=script.hook.upper(), highlights=["10", "THINGS", "ALONE"]))
        
        # Point chunks
        for item in script.items:
            for ch in item.chunks:
                all_chunks.append(ch)

        if not all_chunks:
            return []

        chunk_duration = total_duration / len(all_chunks)
        timeline = []
        
        curr_t = 0.0
        for ch in all_chunks:
            timeline.append({
                "start": curr_t,
                "end": curr_t + chunk_duration,
                "text": ch.text,
                "highlights": ch.highlights,
                "emoji": ch.emoji
            })
            curr_t += chunk_duration

        return timeline

    async def compile_video(
        self,
        script: WisdomScript,
        output_filename: str = "viewstorm_wisdom_reel.mp4",
        fps: int = 24,
        max_duration_seconds: Optional[float] = None
    ) -> Path:
        """
        Compiles the complete 2D animated vertical MP4 video (1080x1920)
        matching the exact visual layout and timing of @view.storm5.
        """
        output_file = self.output_dir / output_filename
        logger.info(f"[VideoCompiler] Starting video compilation for '{script.title}'...")

        # 1. Synthesize Audio
        audio_path, duration = await self.generate_narration_audio(script)
        if max_duration_seconds:
            duration = min(duration, max_duration_seconds)

        total_frames = int(duration * fps)
        logger.info(f"[VideoCompiler] Rendering {total_frames} frames at {fps} FPS ({duration:.1f}s)...")

        # 2. Map timeline
        timeline = self._flatten_timeline_chunks(script, duration)

        # 3. Spawn FFmpeg process with stdin pipe for direct frame delivery
        ffmpeg_cmd = [
            self.ffmpeg_path, "-y",
            "-loglevel", "error",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{CANVAS_WIDTH}x{CANVAS_HEIGHT}",
            "-pix_fmt", "rgba",
            "-r", str(fps),
            "-i", "-", # Stdin pipe
            "-i", audio_path,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(output_file)
        ]

        logger.info(f"[VideoCompiler] Launching FFmpeg renderer: {self.ffmpeg_path}")
        proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

        try:
            for frame_idx in range(total_frames):
                current_time = frame_idx / fps
                
                # Find active subtitle chunk
                active_chunk = None
                for ch in timeline:
                    if ch["start"] <= current_time < ch["end"]:
                        active_chunk = ch
                        break

                sub_text = active_chunk["text"] if active_chunk else ""
                sub_hl = active_chunk["highlights"] if active_chunk else []
                sub_emoji = active_chunk["emoji"] if active_chunk else None

                # Animation cycles
                # Mouth moves 3 frames open, 3 frames closed during active speech
                mouth_open = (frame_idx % 6) in (1, 2, 3)
                # Eyes blink for 2 frames every 80 frames
                eyes_blink = (frame_idx % 80) in (0, 1)

                # Render frame
                frame_img = character_renderer.render_frame(
                    mouth_open=mouth_open,
                    eyes_blink=eyes_blink,
                    gesture_frame=frame_idx,
                    subtitle_text=sub_text,
                    subtitle_highlights=sub_hl,
                    emoji_icon=sub_emoji
                )

                # Write raw RGBA bytes into FFmpeg stdin
                proc.stdin.write(frame_img.tobytes())

                if frame_idx % (fps * 5) == 0:
                    pct = (frame_idx / total_frames) * 100
                    logger.info(f"[VideoCompiler] Render progress: {pct:.0f}% ({frame_idx}/{total_frames} frames)")

            proc.stdin.close()
            proc.wait()
            logger.info(f"[VideoCompiler] FFmpeg compilation complete! Saved to: {output_file}")
            return output_file

        except Exception as e:
            logger.error(f"[VideoCompiler] Error during compilation: {e}", exc_info=True)
            if proc and proc.stdin:
                proc.stdin.close()
            raise

# Singleton instance
video_compiler = VideoCompiler()
