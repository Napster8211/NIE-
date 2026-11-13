"""
NapsterTec AI - Zero-Cost WhatsApp Web Browser Outreach Dispatcher
Module: app/browser/whatsapp_outreach.py

Eliminates Twilio paid APIs by dispatching authentic client messages directly
through WhatsApp Web using sandboxed Playwright with persistent sessions.
"""
import re
import os
import urllib.parse
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from pathlib import Path

from app.browser.browser_pool import browser_pool
from app.browser.session_vault import session_vault

logger = logging.getLogger(__name__)

AUDIT_DIR = Path(__file__).resolve().parent.parent.parent / "storage" / "outreach_proofs"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

class WhatsAppBrowserDispatcher:
    def __init__(self):
        self.profile_name = "whatsapp"

    def is_configured(self) -> bool:
        from app.browser.browser_pool import HAS_PLAYWRIGHT
        if not HAS_PLAYWRIGHT:
            return False
        return session_vault.has_session(self.profile_name)

    def clean_phone_number(self, phone: str) -> str:
        """Standardizes phone numbers into E.164 without symbols (e.g., 233246952225)."""
        digits = re.sub(r"[^\d]", "", phone)
        # Default Ghana country code if local leading zero
        if digits.startswith("0") and len(digits) == 10:
            digits = "233" + digits[1:]
        return digits

    async def send_message(
        self,
        recipient_phone: str,
        message_text: str,
        timeout_ms: int = 45000,
        headless: bool = True
    ) -> Dict[str, Any]:
        """
        Dispatches a WhatsApp message using the authenticated WhatsApp Web session.
        Returns execution status, timestamps, and screenshot proof of delivery.
        """
        if not self.is_configured():
            return {
                "success": False,
                "error": "WhatsApp Web session is not seeded. Please run 'python -m app.browser.session_seeder' to scan QR code.",
                "status": "UNCONFIGURED"
            }

        clean_phone = self.clean_phone_number(recipient_phone)
        if not clean_phone or len(clean_phone) < 9:
            return {
                "success": False,
                "error": f"Invalid recipient phone number: '{recipient_phone}'",
                "status": "INVALID_PHONE"
            }

        encoded_text = urllib.parse.quote(message_text)
        url = f"https://web.whatsapp.com/send?phone={clean_phone}&text={encoded_text}"
        session_key = f"outreach_{clean_phone}"

        logger.info(f"[WhatsAppOutreach] Opening WhatsApp Web for +{clean_phone}...")
        page = None
        try:
            page = await browser_pool.get_or_create_page(
                department="growth",
                agent_id="outreach_agent",
                profile_name=self.profile_name,
                headless=headless
            )

            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            logger.info("[WhatsAppOutreach] Waiting for WhatsApp chat input to settle...")

            # Wait for either the compose message box or invalid number dialog
            # WhatsApp Web message box selector
            chat_input_selectors = [
                'div[contenteditable="true"][data-tab="10"]',
                'div[contenteditable="true"][data-tab="6"]',
                'div[contenteditable="true"][role="textbox"]',
                'div[aria-placeholder="Type a message"]'
            ]
            
            combined_selector = ", ".join(chat_input_selectors)
            try:
                await page.wait_for_selector(combined_selector, timeout=25000)
            except Exception:
                # Check for "Phone number shared via url is invalid."
                invalid_dialog = await page.locator("text='Phone number shared via url is invalid.'").count()
                if invalid_dialog > 0:
                    return {
                        "success": False,
                        "error": f"WhatsApp reports that phone number +{clean_phone} is not on WhatsApp.",
                        "status": "PHONE_NOT_ON_WHATSAPP"
                    }
                raise

            # Small natural delay
            await page.wait_for_timeout(1500)

            # Hit Enter to send the prepared text or click send button
            send_btn = page.locator('span[data-icon="send"], button[aria-label="Send"], span[data-icon="wds-ic-send-filled"]').first
            if await send_btn.count() > 0:
                await send_btn.click()
            else:
                input_box = page.locator(combined_selector).first
                await input_box.press("Enter")

            # Wait 2 seconds for transmission
            await page.wait_for_timeout(2500)

            # Capture screenshot proof
            timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            proof_filename = f"whatsapp_proof_{clean_phone}_{timestamp_str}.png"
            proof_path = AUDIT_DIR / proof_filename
            await page.screenshot(path=str(proof_path), full_page=False)

            logger.info(f"[WhatsAppOutreach] Message delivered to +{clean_phone}! Proof saved at {proof_path}")

            return {
                "success": True,
                "status": "DELIVERED",
                "recipient": f"+{clean_phone}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "proof_screenshot": str(proof_path),
                "cost_usd": 0.00
            }

        except Exception as e:
            logger.error(f"[WhatsAppOutreach] Dispatch error to +{clean_phone}: {e}", exc_info=True)
            return {
                "success": False,
                "status": "FAILED",
                "error": str(e),
                "recipient": f"+{clean_phone}"
            }
        finally:
            # We keep context alive in pool or let it idle
            pass

# Singleton instance
whatsapp_dispatcher = WhatsAppBrowserDispatcher()
