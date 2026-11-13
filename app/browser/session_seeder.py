"""
NapsterTec AI - Session Seeder CLI
Module: app/browser/session_seeder.py

Interactive utility to seed authenticated browser sessions (WhatsApp Web, 
TikTok, YouTube, Instagram) into the Session Vault via a visible desktop browser.
"""
import sys
import asyncio
import logging
from pathlib import Path
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.browser.session_vault import session_vault

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SessionSeeder")

SEEDS = {
    "1": ("whatsapp", "https://web.whatsapp.com", "WhatsApp Web (Scan QR Code)"),
    "2": ("tiktok_viewstorm", "https://www.tiktok.com/login", "TikTok (@view.storm5 Account)"),
    "3": ("youtube", "https://studio.youtube.com", "YouTube Studio (Google Account)"),
    "4": ("instagram", "https://www.instagram.com/accounts/login/", "Instagram Account"),
}

async def seed_interactive(profile_key: str, profile_name: str, target_url: str, label: str):
    logger.info(f"\n=======================================================")
    logger.info(f" SEEDING SESSION: {label}")
    logger.info(f" Target URL: {target_url}")
    logger.info(f" Vault Profile Name: {profile_name}.json")
    logger.info(f"=======================================================")
    
    async with async_playwright() as p:
        logger.info("Launching visible Chromium browser on your screen...")
        browser = await p.chromium.launch(
            headless=False, # Visible desktop window so you can interact / scan QR
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--window-size=1280,900"
            ]
        )
        
        # Check if an existing session exists to refresh it
        storage_path = str(session_vault.get_storage_path(profile_name)) if session_vault.has_session(profile_name) else None
        
        context = await browser.new_context(
            storage_state=storage_path,
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        logger.info(f"Navigating to {target_url}...")
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=90000)
        except Exception as e:
            logger.warning(f"Navigation wait notice ({e}). The page will continue loading in your browser window.")
        
        if profile_key == "1":
            print("\n>>> INSTRUCTIONS: Scan the QR code with WhatsApp on your phone (Linked Devices).")
        else:
            print(f"\n>>> INSTRUCTIONS: Complete login in the opened browser window.")
            
        print(">>> Once you are completely logged in and see your dashboard/chats,")
        input(">>> PRESS [ENTER] HERE IN THE TERMINAL TO SAVE YOUR SESSION TO THE VAULT: ")
        
        # Save session
        state = await context.storage_state()
        saved_file = session_vault.save_session(profile_name, state)
        print(f"\n[SUCCESS] Session saved successfully to: {saved_file}")
        
        await browser.close()
        logger.info("Browser closed. Vault profile is active!")

def main():
    print("\n=================================================")
    print("  NapsterTec AI - Autonomous Session Seeder")
    print("=================================================")
    print("Select a service to authenticate and save session:")
    for k, (name, _, label) in SEEDS.items():
        exists = "[ACTIVE]" if session_vault.has_session(name) else "[NOT SEEDED]"
        print(f"  [{k}] {label} {exists}")
    print("  [Q] Quit")
    print("-------------------------------------------------")
    
    choice = input("Enter choice (1-4 or Q): ").strip().upper()
    if choice in SEEDS:
        name, url, label = SEEDS[choice]
        asyncio.run(seed_interactive(choice, name, url, label))
    elif choice == "Q":
        print("Exiting.")
    else:
        print("Invalid choice.")

if __name__ == "__main__":
    main()
