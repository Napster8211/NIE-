import os
import csv
from typing import Tuple, List

# Note: Production environment requires `pip install PyPDF2 python-docx`
try:
    import PyPDF2
    import docx
except ImportError:
    pass # Graceful degradation handled in extraction logic

class DocumentParserService:
    CHUNK_SIZE = 2000 # Character limit for naive chunking
    CHUNK_OVERLAP = 200

    @classmethod
    def chunk_text(cls, text: str) -> List[str]:
        """Naively chunks text for LLM context windows."""
        if not text:
            return []
        chunks = []
        for i in range(0, len(text), cls.CHUNK_SIZE - cls.CHUNK_OVERLAP):
            chunks.append(text[i:i + cls.CHUNK_SIZE])
        return chunks

    @classmethod
    async def extract_content(cls, file_path: str, mime_type: str) -> Tuple[str, List[str]]:
        """
        Extracts text based on file type and returns (full_text, chunks).
        Future: Add OCR fallback for images/scanned PDFs.
        """
        ext = os.path.splitext(file_path)[1].lower()
        text = ""

        try:
            if ext == '.pdf' or 'pdf' in mime_type:
                with open(file_path, 'rb') as f:
                    reader = PyPDF2.PdfReader(f)
                    text = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
            
            elif ext == '.docx' or 'word' in mime_type:
                doc = docx.Document(file_path)
                text = "\n".join([para.text for para in doc.paragraphs])
            
            elif ext in ['.txt', '.md', '.csv'] or 'text' in mime_type:
                with open(file_path, 'r', encoding='utf-8') as f:
                    text = f.read()

            elif ext in ['.xlsx', '.pptx']:
                text = "[System: Excel/PPT extraction pipeline pending future RAG update.]"

            elif ext in ['.mp4', '.mov', '.webm', '.avi', '.mkv'] or (mime_type and 'video' in mime_type):
                file_size_mb = os.path.getsize(file_path) / (1024 * 1024) if os.path.exists(file_path) else 0.0
                duration_sec = 0.0
                width, height, fps, codec = "Unknown", "Unknown", "Unknown", "Unknown"
                has_audio = False
                try:
                    import av
                    container = av.open(file_path)
                    video_stream = next((s for s in container.streams if s.type == "video"), None)
                    audio_stream = next((s for s in container.streams if s.type == "audio"), None)
                    if container.duration:
                        duration_sec = float(container.duration) / 1000000.0
                    if video_stream:
                        width = video_stream.width
                        height = video_stream.height
                        codec = video_stream.codec_context.name if video_stream.codec_context else "Unknown"
                        fps = float(video_stream.average_rate) if video_stream.average_rate else "Unknown"
                    has_audio = audio_stream is not None
                    container.close()
                except Exception as av_err:
                    pass

                text = (
                    f"[Attached Video Media: {os.path.basename(file_path)}]\n"
                    f"• Container / Format: {ext.upper()} ({mime_type})\n"
                    f"• Duration: {duration_sec:.1f}s\n"
                    f"• Resolution: {width}x{height} @ {fps} fps (Codec: {codec})\n"
                    f"• Audio Track Detected: {has_audio}\n"
                    f"• Media File Size: {file_size_mb:.2f} MB\n\n"
                    f"[Video Context: This video file has been ingested into the NapsterTec Intelligence Engine. "
                    f"Visual snapshot frames are attached to this context for multimodal scene, character, and text analysis.]"
                )
                
            else:
                text = f"[System: Unsupported file format {ext}]"

        except Exception as e:
            raise ValueError(f"Extraction failed: {str(e)}")

        chunks = cls.chunk_text(text)
        return text, chunks