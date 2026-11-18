"""
NapsterTec AI - Milestone 1 Verification Test Suite
Verifies genuine Playwright Chromium execution, JS rendering, DOM inspection,
element interaction, screenshot capture, session continuity, and fail-closed failure handling.
"""
import pytest
import base64
from unittest.mock import patch
from app.browser.browser_pool import browser_pool
from app.browser_operator import BrowserOperatorInput, browser_operator_manager


@pytest.mark.anyio
async def test_a_browser_launch():
    """Test A: Launch real Chromium instance and return its actual browser version."""
    page = await browser_pool.get_or_create_page(
        department="test",
        agent_id="test_a_launch",
        headless=True,
    )
    assert page is not None
    assert browser_pool.browser is not None
    assert browser_pool.browser.is_connected()
    version = browser_pool.browser.version
    assert version and len(version.split(".")) >= 3
    print(f"\n[Test A Passed] Real Chromium version: {version}")
    await browser_pool.close_session("test_test_a_launch_ephemeral")


@pytest.mark.anyio
async def test_b_website_navigation():
    """Test B: Navigate to https://example.com and verify real URL and page title."""
    result = await browser_operator_manager.execute(
        BrowserOperatorInput(
            action="navigate",
            session_id="test_b_nav",
            url="https://example.com",
            allow_interactions=True,
        )
    )
    assert result["status"] == "COMPLETED"
    assert result["url"] == "https://example.com/" or result["url"] == "https://example.com"
    assert "Example Domain" in result["title"]
    assert "documentation examples" in result["text"].lower() or "domain" in result["text"].lower()
    assert result.get("error") is None
    print(f"\n[Test B Passed] Title: {result['title']}, URL: {result['url']}")
    await browser_operator_manager.execute(
        BrowserOperatorInput(action="close", session_id="test_b_nav")
    )


@pytest.mark.anyio
async def test_c_javascript_rendering():
    """Test C: Navigate to https://quotes.toscrape.com/js/ and verify content rendered through JavaScript."""
    result = await browser_operator_manager.execute(
        BrowserOperatorInput(
            action="navigate",
            session_id="test_c_js",
            url="https://quotes.toscrape.com/js/",
            allow_interactions=True,
        )
    )
    assert result["status"] == "COMPLETED"
    # Content rendered via JavaScript inside quotes.toscrape.com/js/
    assert "The world as we have created it" in result["text"] or "Albert Einstein" in result["text"]
    print(f"\n[Test C Passed] Successfully extracted client-side JS rendered text")
    await browser_operator_manager.execute(
        BrowserOperatorInput(action="close", session_id="test_c_js")
    )


@pytest.mark.anyio
async def test_d_dom_inspection():
    """Test D: Extract real page text and inspect actual page elements."""
    page = await browser_pool.get_or_create_page(
        department="test",
        agent_id="test_d_dom",
        headless=True,
    )
    await page.goto("https://example.com")
    title = await page.title()
    assert "Example Domain" in title
    p_text = await page.locator("p").first.inner_text()
    assert "documentation examples" in p_text.lower() or "illustrative" in p_text.lower() or "domain" in p_text.lower()
    print(f"\n[Test D Passed] Inspected DOM title='{title}', p='{p_text[:50]}...'")
    await browser_pool.close_session("test_test_d_dom_ephemeral")


@pytest.mark.anyio
async def test_e_interaction_click():
    """Test E: Locate interactive element, click it, and verify the resulting state."""
    # Navigate to quotes.toscrape.com/js/
    await browser_operator_manager.execute(
        BrowserOperatorInput(
            action="navigate",
            session_id="test_e_interact",
            url="https://quotes.toscrape.com/js/",
            allow_interactions=True,
        )
    )
    # Click next page link '.pager .next a'
    click_res = await browser_operator_manager.execute(
        BrowserOperatorInput(
            action="click",
            session_id="test_e_interact",
            selector=".pager .next a",
            allow_interactions=True,
        )
    )
    assert click_res["status"] == "COMPLETED"
    assert "page/2" in click_res["url"]
    print(f"\n[Test E Passed] Click navigated to page 2: {click_res['url']}")
    await browser_operator_manager.execute(
        BrowserOperatorInput(action="close", session_id="test_e_interact")
    )


@pytest.mark.anyio
async def test_f_screenshot_capture():
    """Test F: Capture actual Chromium screenshot and verify returned image represents rendered website."""
    result = await browser_operator_manager.execute(
        BrowserOperatorInput(
            action="navigate",
            session_id="test_f_screenshot",
            url="https://example.com",
            allow_interactions=True,
        )
    )
    assert result["screenshot_data_url"] is not None
    assert result["screenshot_data_url"].startswith("data:image/jpeg;base64,")
    # Decode base64 bytes to verify it's a real JPEG
    b64_data = result["screenshot_data_url"].split(",")[1]
    image_bytes = base64.b64decode(b64_data)
    # Check JPEG SOI marker (0xFF, 0xD8) and length > 5KB
    assert image_bytes[:2] == b"\xff\xd8"
    assert len(image_bytes) > 5000
    print(f"\n[Test F Passed] Verified genuine JPEG screenshot bytes: {len(image_bytes)}")
    await browser_operator_manager.execute(
        BrowserOperatorInput(action="close", session_id="test_f_screenshot")
    )


@pytest.mark.anyio
async def test_g_session_continuity():
    """Test G: Navigate to another page within the same session and verify context remains operational."""
    # Step 1: Navigate to example.com
    res1 = await browser_operator_manager.execute(
        BrowserOperatorInput(
            action="navigate",
            session_id="test_g_continuous",
            url="https://example.com",
            allow_interactions=True,
        )
    )
    assert res1["status"] == "COMPLETED"
    assert "example.com" in res1["url"]

    # Step 2: Navigate to quotes.toscrape.com in the exact same session
    res2 = await browser_operator_manager.execute(
        BrowserOperatorInput(
            action="navigate",
            session_id="test_g_continuous",
            url="https://quotes.toscrape.com/js/",
            allow_interactions=True,
        )
    )
    assert res2["status"] == "COMPLETED"
    assert "quotes.toscrape.com" in res2["url"]
    print(f"\n[Test G Passed] Session preserved across sequential navigations")
    await browser_operator_manager.execute(
        BrowserOperatorInput(action="close", session_id="test_g_continuous")
    )


@pytest.mark.anyio
async def test_h_failure_handling():
    """Test H: Verify system reports FAILED with diagnostic error instead of mock success."""
    # 1. Invalid navigation (unreachable port/host)
    bad_nav_result = await browser_operator_manager.execute(
        BrowserOperatorInput(
            action="navigate",
            session_id="test_h_fail",
            url="https://127.0.0.1:9999",
            allow_interactions=True,
        )
    )
    assert bad_nav_result["status"] == "FAILED"
    assert "blocked" in bad_nav_result["error"].lower() or "failed" in bad_nav_result["error"].lower() or "error" in bad_nav_result["error"].lower()
    assert bad_nav_result.get("screenshot_data_url") is None

    # 2. Driver launch failure simulation
    with patch.object(browser_operator_manager, "_page", side_effect=RuntimeError("Simulated Chromium engine failure")):
        launch_fail_result = await browser_operator_manager.execute(
            BrowserOperatorInput(
                action="navigate",
                session_id="test_h_launch_fail",
                url="https://example.com",
                allow_interactions=True,
            )
        )
        assert launch_fail_result["status"] == "FAILED"
        assert "Simulated Chromium engine failure" in launch_fail_result["error"]
        assert launch_fail_result.get("screenshot_data_url") is None
        print(f"\n[Test H Passed] Fail-closed verified: FAILED status reported without mock card fabrication")
