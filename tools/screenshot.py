"""
screenshot.py — Captures a full-page screenshot of a URL using Playwright.

Usage:
  py tools/screenshot.py
  py tools/screenshot.py --url http://localhost:8000 --out .tmp/shot.png
  py tools/screenshot.py --url http://localhost:8000 --selector "#view-clasament" --out .tmp/clasament.png
"""

import argparse
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

def take_screenshot(url: str, out: str, selector: str | None, wait_ms: int, full_page: bool):
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        print(f"  Opening {url}")
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(wait_ms)

        if selector:
            el = page.query_selector(selector)
            if el:
                print(f"  Screenshotting element: {selector}")
                el.screenshot(path=str(out_path))
            else:
                print(f"  Selector '{selector}' not found - falling back to full page")
                page.screenshot(path=str(out_path), full_page=full_page)
        else:
            page.screenshot(path=str(out_path), full_page=full_page)

        browser.close()

    print(f"  Saved to {out_path.resolve()}")
    return str(out_path.resolve())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Take a Playwright screenshot")
    parser.add_argument("--url",      default="http://localhost:8000", help="URL to capture")
    parser.add_argument("--out",      default=".tmp/screenshot.png",   help="Output file path")
    parser.add_argument("--selector", default=None,                    help="CSS selector to capture instead of full page")
    parser.add_argument("--wait",     default=800, type=int,           help="Extra wait in ms after page load")
    parser.add_argument("--no-full",  action="store_true",             help="Capture viewport only (not full page)")
    args = parser.parse_args()

    take_screenshot(
        url=args.url,
        out=args.out,
        selector=args.selector,
        wait_ms=args.wait,
        full_page=not args.no_full,
    )
