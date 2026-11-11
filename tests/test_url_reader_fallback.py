import pytest
import asyncio
from app.tools.url_reader import UrlReaderInput, UrlReaderTool


def test_url_reader_strips_json_delimiters_from_url():
    parsed = UrlReaderInput(url='https://github.com/"}')
    assert parsed.url == "https://github.com/"


@pytest.mark.asyncio
async def test_url_reader_fallback():
    tool = UrlReaderTool()
    # Test on a static, highly reliable URL
    result = await tool.execute(url="https://en.wikipedia.org/wiki/Artificial_intelligence")
    assert "extracted_text" in result
    text = result["extracted_text"]
    assert len(text) > 100
    assert "Artificial intelligence" in text or "Wikipedia" in text
    print("\nScraped length:", len(text))
