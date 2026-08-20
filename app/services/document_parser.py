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
                
            else:
                text = f"[System: Unsupported file format {ext}]"

        except Exception as e:
            raise ValueError(f"Extraction failed: {str(e)}")

        chunks = cls.chunk_text(text)
        return text, chunks