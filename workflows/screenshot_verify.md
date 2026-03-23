# Workflow: Screenshot & Visual Verification

## Objective
Take a browser screenshot of the local dashboard to visually verify UI changes without asking the user.

## Prerequisites
- Server must be running: `py -m uvicorn main:app --port 8000` (from `UCL-Fantasy-Friends-main/`)
- Playwright installed: `py -m pip install playwright && py -m playwright install chromium`

## Tool
`tools/screenshot.py`

## Steps

### 1. Full page screenshot
```
py tools/screenshot.py
```
Saves to `.tmp/screenshot.png`. Default URL: `http://localhost:8000`.

### 2. Specific section screenshot
```
py tools/screenshot.py --selector "#view-clasament" --out .tmp/clasament.png
```

### 3. Read the image
Use the Read tool on the output path (e.g., `.tmp/screenshot.png`) — Claude can view images directly.

## Common selectors
| Section    | Selector            |
|------------|---------------------|
| Clasament  | `#view-clasament`   |
| Echipe MD  | `#view-echipe-md`   |
| Scouting   | `#view-scouting`    |
| TOTW       | `#view-totw`        |
| Full page  | *(omit --selector)* |

## Edge cases
- If server isn't running, script fails with connection error → start server first, then retry
- `networkidle` wait + 800ms extra handles async JS rendering; increase `--wait` if data hasn't loaded yet
- Selector not found → falls back to full page automatically

## After screenshot
Read the image file and visually inspect. If issues found, fix CSS/JS, then re-run this workflow to confirm.
