"""
ui_audit.py — Automated UI/UX audit: screenshots + layout checks + HTML report.

Takes screenshots of every key section at desktop (1920×1080) and mobile (440×956),
runs automated layout checks, and generates a side-by-side HTML report with verdicts.

Usage:
  py tools/ui_audit.py
  py tools/ui_audit.py --url http://localhost:8000
  py tools/ui_audit.py --open
"""

import argparse
import base64
import json
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

# ── Config ────────────────────────────────────────────────────────────────────

VIEWPORTS = {
    "desktop": {"width": 1920, "height": 1080},
    "mobile":  {"width": 440,  "height": 956},
}

SECTIONS = [
    {"name": "Full Page",  "selector": None,               "tab": None},
    {"name": "Clasament",  "selector": "#view-clasament",   "tab": "clasament"},
    {"name": "Echipe MD",  "selector": "#view-echipe-md",   "tab": "echipe-md"},
    {"name": "Scouting",   "selector": "#view-scouting",    "tab": "scouting"},
    {"name": "TOTW",       "selector": "#view-totw",        "tab": "totw"},
]

REPORT_PATH = Path(".tmp/ui-audit-report.html")
SHOTS_DIR = Path(".tmp/audit")

MIN_TAP_TARGET = 44  # px


# ── Checks ────────────────────────────────────────────────────────────────────

def check_horizontal_overflow(page):
    """Returns True if page has no horizontal overflow."""
    result = page.evaluate("""() => {
        return document.documentElement.scrollWidth <= document.documentElement.clientWidth;
    }""")
    return result


def check_section_visible(page, selector):
    """Returns True if element exists and has non-zero dimensions."""
    result = page.evaluate("""(sel) => {
        const el = document.querySelector(sel);
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }""", selector)
    return result


def check_tap_targets(page):
    """Returns list of too-small interactive elements (mobile)."""
    smalls = page.evaluate(f"""() => {{
        const min = {MIN_TAP_TARGET};
        const issues = [];
        const interactives = document.querySelectorAll('a, button, input, select, textarea, [role="button"], [onclick]');
        for (const el of interactives) {{
            const r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0 && (r.width < min || r.height < min)) {{
                const tag = el.tagName.toLowerCase();
                const text = (el.textContent || '').trim().slice(0, 30);
                issues.push({{tag, text, width: Math.round(r.width), height: Math.round(r.height)}});
            }}
        }}
        return issues;
    }}""")
    return smalls


def check_text_clipping(page):
    """Returns list of elements where text is clipped (scrollHeight > clientHeight)."""
    clipped = page.evaluate("""() => {
        const issues = [];
        const els = document.querySelectorAll('p, span, div, h1, h2, h3, h4, h5, h6, li, td, th, label');
        for (const el of els) {
            const style = getComputedStyle(el);
            if (style.overflow === 'hidden' && el.scrollHeight > el.clientHeight + 2) {
                const text = (el.textContent || '').trim().slice(0, 40);
                if (text.length > 0) {
                    issues.push({tag: el.tagName.toLowerCase(), text,
                                 scrollH: el.scrollHeight, clientH: el.clientHeight});
                }
            }
        }
        return issues.slice(0, 15);
    }""")
    return clipped


# ── Screenshot helpers ────────────────────────────────────────────────────────

def take_section_shot(page, section, viewport_name):
    """Take a screenshot of a section. Returns path."""
    fname = f"{viewport_name}_{section['name'].lower().replace(' ', '_')}.png"
    fpath = SHOTS_DIR / fname

    if section["selector"]:
        el = page.query_selector(section["selector"])
        if el:
            el.screenshot(path=str(fpath))
        else:
            return None
    else:
        page.screenshot(path=str(fpath), full_page=True)

    return fpath


def img_to_base64(path):
    """Read image file and return base64 data URI."""
    data = Path(path).read_bytes()
    return f"data:image/png;base64,{base64.b64encode(data).decode()}"


# ── Main audit ────────────────────────────────────────────────────────────────

def run_audit(url, auto_open):
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)

    results = {}  # {viewport: {section_name: {screenshot, checks}}}

    with sync_playwright() as p:
        browser = p.chromium.launch()

        for vp_name, vp_size in VIEWPORTS.items():
            print(f"\n{'='*50}")
            print(f"  Viewport: {vp_name} ({vp_size['width']}x{vp_size['height']})")
            print(f"{'='*50}")

            page = browser.new_page(viewport=vp_size)
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(1000)

            vp_results = {}

            # Global checks
            no_overflow = check_horizontal_overflow(page)
            tap_issues = check_tap_targets(page) if vp_name == "mobile" else []
            clip_issues = check_text_clipping(page)

            print(f"  Overflow:  {'PASS' if no_overflow else 'FAIL'}")
            if vp_name == "mobile":
                print(f"  Tap targets: {len(tap_issues)} too small")
            print(f"  Clipped text: {len(clip_issues)} elements")

            for section in SECTIONS:
                name = section["name"]

                # Switch tab if this is a SPA tab-based section
                if section["tab"]:
                    page.evaluate(f'goTab("{section["tab"]}")')
                    page.wait_for_timeout(500)

                visible = check_section_visible(page, section["selector"]) if section["selector"] else True

                # Scroll to section
                if section["selector"] and visible:
                    page.evaluate(f'document.querySelector("{section["selector"]}").scrollIntoView()')
                    page.wait_for_timeout(300)

                shot_path = take_section_shot(page, section, vp_name)

                status = "PASS"
                if section["selector"] and not visible:
                    status = "FAIL"

                print(f"  {name}: visible={visible}, screenshot={'OK' if shot_path else 'SKIP'}")

                vp_results[name] = {
                    "screenshot": str(shot_path) if shot_path else None,
                    "visible": visible,
                    "status": status,
                }

            results[vp_name] = {
                "sections": vp_results,
                "no_overflow": no_overflow,
                "tap_issues": tap_issues,
                "clip_issues": clip_issues,
            }

            page.close()

        browser.close()

    # ── Compute overall verdict ───────────────────────────────────────────
    has_fail = False
    has_warn = False

    for vp_name, vp_data in results.items():
        if not vp_data["no_overflow"]:
            has_fail = True
        if len(vp_data["tap_issues"]) > 0:
            has_warn = True
        if len(vp_data["clip_issues"]) > 3:
            has_warn = True
        for sec in vp_data["sections"].values():
            if sec["status"] == "FAIL":
                has_fail = True

    if has_fail:
        verdict = "FAIL"
    elif has_warn:
        verdict = "WARN"
    else:
        verdict = "PASS"

    print(f"\n{'='*50}")
    print(f"  VERDICT: {verdict}")
    print(f"{'='*50}\n")

    # ── Generate HTML report ──────────────────────────────────────────────
    generate_report(url, results, verdict)

    if auto_open:
        webbrowser.open(str(REPORT_PATH.resolve()))

    return verdict


def generate_report(url, results, verdict):
    """Generate an HTML report with side-by-side screenshots."""

    verdict_colors = {"PASS": "#22c55e", "WARN": "#f59e0b", "FAIL": "#ef4444"}
    vc = verdict_colors[verdict]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sections_html = ""
    for section in SECTIONS:
        name = section["name"]
        desktop_data = results["desktop"]["sections"].get(name, {})
        mobile_data = results["mobile"]["sections"].get(name, {})

        desktop_img = ""
        if desktop_data.get("screenshot") and Path(desktop_data["screenshot"]).exists():
            desktop_img = f'<img src="{img_to_base64(desktop_data["screenshot"])}" style="width:100%;border-radius:8px;">'
        else:
            desktop_img = '<div style="padding:40px;text-align:center;color:#94a3b8;background:#1e293b;border-radius:8px;">Not captured</div>'

        mobile_img = ""
        if mobile_data.get("screenshot") and Path(mobile_data["screenshot"]).exists():
            mobile_img = f'<img src="{img_to_base64(mobile_data["screenshot"])}" style="width:100%;border-radius:8px;">'
        else:
            mobile_img = '<div style="padding:40px;text-align:center;color:#94a3b8;background:#1e293b;border-radius:8px;">Not captured</div>'

        d_status = desktop_data.get("status", "—")
        m_status = mobile_data.get("status", "—")
        d_color = verdict_colors.get(d_status, "#94a3b8")
        m_color = verdict_colors.get(m_status, "#94a3b8")

        sections_html += f"""
        <div style="margin-bottom:48px;">
            <h2 style="font-family:'Inter',sans-serif;font-size:20px;color:#e2e8f0;margin-bottom:16px;
                        border-bottom:1px solid #334155;padding-bottom:8px;">
                {name}
            </h2>
            <div style="display:grid;grid-template-columns:2fr 1fr;gap:24px;align-items:start;">
                <div>
                    <div style="font-size:12px;color:#94a3b8;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em;">
                        Desktop (1920×1080)
                        <span style="color:{d_color};font-weight:700;margin-left:8px;">{d_status}</span>
                    </div>
                    {desktop_img}
                </div>
                <div>
                    <div style="font-size:12px;color:#94a3b8;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em;">
                        Mobile (440×956)
                        <span style="color:{m_color};font-weight:700;margin-left:8px;">{m_status}</span>
                    </div>
                    {mobile_img}
                </div>
            </div>
        </div>
        """

    # Issues section
    issues_html = ""
    for vp_name, vp_data in results.items():
        if not vp_data["no_overflow"]:
            issues_html += f'<div style="padding:12px 16px;background:#7f1d1d;border-radius:8px;margin-bottom:8px;color:#fca5a5;">FAIL — {vp_name}: Horizontal overflow detected</div>'

        if vp_data["tap_issues"]:
            items = "".join(
                f'<li>&lt;{t["tag"]}&gt; "{t["text"]}" → {t["width"]}×{t["height"]}px</li>'
                for t in vp_data["tap_issues"][:10]
            )
            issues_html += f"""
            <div style="padding:12px 16px;background:#78350f;border-radius:8px;margin-bottom:8px;color:#fcd34d;">
                WARN — {vp_name}: {len(vp_data["tap_issues"])} tap targets below {MIN_TAP_TARGET}px
                <ul style="margin:8px 0 0 16px;font-size:13px;">{items}</ul>
            </div>"""

        if vp_data["clip_issues"]:
            items = "".join(
                f'<li>&lt;{c["tag"]}&gt; "{c["text"]}" (scroll:{c["scrollH"]} vs client:{c["clientH"]})</li>'
                for c in vp_data["clip_issues"][:8]
            )
            issues_html += f"""
            <div style="padding:12px 16px;background:#78350f;border-radius:8px;margin-bottom:8px;color:#fcd34d;">
                WARN — {vp_name}: {len(vp_data["clip_issues"])} clipped text elements
                <ul style="margin:8px 0 0 16px;font-size:13px;">{items}</ul>
            </div>"""

    if not issues_html:
        issues_html = '<div style="padding:16px;color:#86efac;background:#14532d;border-radius:8px;">No issues detected.</div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>UI Audit Report</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:#0f172a;color:#e2e8f0;font-family:'Inter',sans-serif;">

<div style="max-width:1400px;margin:0 auto;padding:40px 32px;">

    <!-- Header -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:40px;
                padding:24px 32px;background:#1e293b;border-radius:12px;border:1px solid #334155;">
        <div>
            <h1 style="margin:0;font-size:28px;font-weight:700;letter-spacing:-0.02em;">UI/UX Audit Report</h1>
            <p style="margin:4px 0 0;font-size:14px;color:#94a3b8;">{url} — {now}</p>
        </div>
        <div style="padding:12px 28px;border-radius:8px;font-size:22px;font-weight:700;
                     background:{vc}22;color:{vc};border:2px solid {vc};">
            {verdict}
        </div>
    </div>

    <!-- Issues -->
    <div style="margin-bottom:40px;">
        <h2 style="font-size:18px;color:#94a3b8;margin-bottom:12px;">Issues</h2>
        {issues_html}
    </div>

    <!-- Sections -->
    {sections_html}

    <!-- Footer -->
    <div style="text-align:center;padding:24px;color:#475569;font-size:12px;">
        Generated by ui_audit.py
    </div>

</div>

</body>
</html>"""

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(html, encoding="utf-8")
    print(f"  Report saved to {REPORT_PATH.resolve()}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UI/UX audit with screenshots and layout checks")
    parser.add_argument("--url",  default="http://localhost:8000", help="URL to audit")
    parser.add_argument("--open", action="store_true",            help="Auto-open report in browser")
    args = parser.parse_args()

    verdict = run_audit(args.url, args.open)
    sys.exit(0 if verdict == "PASS" else 1)
