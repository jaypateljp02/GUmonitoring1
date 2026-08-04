import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Capture console logs
        page.on("console", lambda msg: print(f"CONSOLE {msg.type}: {msg.text}"))
        page.on("pageerror", lambda err: print(f"PAGE ERROR: {err}"))
        
        print("Navigating to dashboard...")
        await page.goto("https://gu-monitoring.initiativesewafoundation.com/", wait_until="networkidle")
        
        # Take a screenshot
        screenshot_path = "C:/Users/User/.gemini/antigravity-ide/brain/c6a02fa4-8064-48b6-9c94-84a01a72f3dd/browser_screenshot.png"
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        
        await browser.close()

asyncio.run(run())
