"""
HARVIS Browser Fetch Tool — Playwright-powered headless browser reader.

Uses headless Chromium to fully render any page (JavaScript, Cloudflare, SPAs, etc.)
and extract readable text, links, or interactive element descriptions.

This gives HARVIS near-100% internet access even on bot-protected or JS-heavy sites.
"""

import asyncio
import logging
import re

logger = logging.getLogger("harvis.tools")

# ─── HTML → readable text helper ─────────────────────────────────────────────
def _html_to_text(html: str, max_chars: int = 12000) -> str:
    """Strip HTML tags and collapse whitespace into readable plain text."""
    # Remove non-content elements entirely
    for tag in ("script", "style", "noscript", "svg", "head", "nav", "header", "footer",
                "aside", "form", "button", "input", "select", "option", "meta", "link"):
        html = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Replace block elements with newlines
    html = re.sub(r"<(br|p|div|li|h[1-6]|tr|article|section)[^>]*>", "\n", html, flags=re.IGNORECASE)
    # Strip all remaining tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Decode common entities
    html = (html
        .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        .replace("&nbsp;", " ").replace("&quot;", '"').replace("&#39;", "'")
        .replace("&#x27;", "'").replace("&apos;", "'"))
    # Collapse spaces on each line, then collapse blank lines
    lines = [re.sub(r" {2,}", " ", line).strip() for line in html.splitlines()]
    lines = [line for line in lines if line]  # drop empty lines
    # Remove duplicate adjacent lines (nav repetition)
    deduped = []
    for line in lines:
        if not deduped or line != deduped[-1]:
            deduped.append(line)
    text = "\n".join(deduped)
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


async def _playwright_fetch(
    url: str,
    wait_for: str = "networkidle",   # "load", "domcontentloaded", "networkidle"
    extract: str = "text",            # "text", "html", "links", "screenshot_path"
    wait_selector: str = None,        # optional CSS selector to wait for before reading
    scroll: bool = True,              # scroll down to trigger lazy loading
    max_chars: int = 12000,
    timeout_ms: int = 45000,
    headless: bool = True,           # Set False to open visible browser for manual CAPTCHA solving
) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"success": False, "error": "Playwright is not installed."}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--window-size=1920,1080",
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            locale="en-US,en;q=0.9",
            timezone_id="Africa/Johannesburg",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Linux"',
            }
        )
        page = await context.new_page()

        # Stealth: remove automation fingerprints & override navigator
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        """)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await asyncio.sleep(1.5)

            # Detect CAPTCHA / Bot Check
            content_preview = (await page.content()).lower()
            is_captcha = "unusual traffic" in content_preview or "captcha" in content_preview or "cf-challenge" in content_preview

            if is_captcha:
                if headless:
                    logger.info("CAPTCHA detected in headless mode! Relaunching visible browser for Harold to solve...")
                    await browser.close()
                    return await _playwright_fetch(
                        url=url, wait_for=wait_for, extract=extract,
                        wait_selector=wait_selector, scroll=scroll,
                        max_chars=max_chars, timeout_ms=60000, headless=False
                    )
                else:
                    logger.info("CAPTCHA window open! Waiting up to 40s for Harold to solve the CAPTCHA...")
                    for _ in range(40):
                        await asyncio.sleep(1)
                        try:
                            c = (await page.content()).lower()
                            if "unusual traffic" not in c and "captcha" not in c:
                                logger.info("CAPTCHA solved by user! Resuming scrape...")
                                break
                        except Exception:
                            pass

            # Wait for the page to reach the desired stable state
            try:
                if wait_for == "networkidle":
                    await page.wait_for_load_state("networkidle", timeout=15000)
                elif wait_for == "load":
                    await page.wait_for_load_state("load", timeout=15000)
                else:
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass  # proceed with whatever is loaded

            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=8000)
                except Exception:
                    pass  # proceed anyway

            if scroll:
                try:
                    for _ in range(3):
                        await page.keyboard.press("PageDown")
                        await asyncio.sleep(0.4)
                    await asyncio.sleep(0.6)
                except Exception:
                    pass  # scroll failure is non-fatal

            if extract == "screenshot_path":
                import os
                path = os.path.expanduser("~/Pictures/harvis_browse.png")
                await page.screenshot(path=path, full_page=False)
                await browser.close()
                return {"success": True, "output": f"Screenshot saved to {path}", "path": path}

            elif extract == "media":
                media_data = await page.evaluate("""
                    () => {
                        const imgs = Array.from(document.querySelectorAll('img[src]'))
                            .map(img => ({ type: 'image', src: img.src, alt: img.alt || '' }))
                            .filter(i => i.src && !i.src.startsWith('data:image/svg'));
                        const videos = Array.from(document.querySelectorAll('video, video source'))
                            .map(v => ({ type: 'video', src: v.src || v.currentSrc }))
                            .filter(v => v.src);
                        const audios = Array.from(document.querySelectorAll('audio, audio source'))
                            .map(a => ({ type: 'audio', src: a.src || a.currentSrc }))
                            .filter(a => a.src);
                        const ogMedia = Array.from(document.querySelectorAll('meta[property^="og:image"], meta[property^="og:video"], meta[property^="og:audio"]'))
                            .map(m => ({ type: m.getAttribute('property'), src: m.content }))
                            .filter(m => m.src);
                        return [...ogMedia, ...imgs.slice(0, 30), ...videos, ...audios];
                    }
                """)
                lines = [f"- [{m.get('type', 'media').upper()}] {m['src']} {('(' + m['alt'] + ')') if m.get('alt') else ''}" for m in media_data if m.get("src")]
                await browser.close()
                return {"success": True, "output": "\n".join(lines) or "No media found on page."}

            elif extract == "links":
                anchors = await page.eval_on_selector_all("a[href]", """
                    els => els.slice(0, 50).map(a => ({text: a.innerText.trim().slice(0, 80), href: a.href}))
                """)
                lines = [f"- [{a['text'] or '(no label)'}]({a['href']})" for a in anchors if a.get("href")]
                await browser.close()
                return {"success": True, "output": "\n".join(lines) or "No links found."}

            elif extract == "html":
                html = await page.content()
                await browser.close()
                return {"success": True, "output": html[:max_chars]}

            else:  # text (default)
                html = await page.content()
                title = await page.title()
                text = _html_to_text(html, max_chars=max_chars)
                await browser.close()
                result = f"# {title}\n\n{text}" if title else text
                return {"success": True, "output": result}

        except Exception as e:
            await browser.close()
            return {"success": False, "error": str(e)}


def browser_fetch(
    url: str,
    extract: str = "text",
    wait_for: str = "networkidle",
    wait_selector: str = None,
    scroll: bool = True,
    max_chars: int = 12000,
    headless: bool = True,
) -> dict:
    """
    Fetch and read any webpage using a real Chromium browser.
    Handles JavaScript rendering, SPAs, Cloudflare, lazy loading.
    If a CAPTCHA is encountered, opens a visible browser window so Harold can solve it!

    Args:
        url: The full URL to fetch (must start with http:// or https://)
        extract: What to extract — 'text' (default), 'links', 'html', or 'screenshot_path'
        wait_for: When to consider page loaded — 'networkidle' (default), 'load', 'domcontentloaded'
        wait_selector: Optional CSS selector to wait for before reading
        scroll: Whether to scroll down to trigger lazy-loaded content (default: True)
        max_chars: Max characters to return in text/html mode (default: 12000)
        headless: Whether to run headless (default True). Automatically flips to False if CAPTCHA detected.
    """
    return asyncio.run(_playwright_fetch(
        url=url,
        extract=extract,
        wait_for=wait_for,
        wait_selector=wait_selector,
        scroll=scroll,
        max_chars=max_chars,
        headless=headless,
    ))
