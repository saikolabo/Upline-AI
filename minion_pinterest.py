#!/usr/bin/env python3
"""
minion_pinterest.py — Creates and posts Pinterest pins for published articles.
Runs daily at 09:00 UTC via GitHub Actions.

Flags:
  --preview   Generate a single sample image and exit (no API calls)
  --dry-run   Generate all pending images, skip Pinterest posting
"""

import base64
import json
import os
import re
import sys
import time
import random
from datetime import date, datetime
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
MODEL = "claude-sonnet-4-6"
DATA_DIR = Path("data")
ASSETS_DIR = Path("assets")
FONTS_DIR = ASSETS_DIR / "fonts"
PINS_DIR = ASSETS_DIR / "pins"
PUBLISHED_FILE = DATA_DIR / "published.json"
PINTEREST_LOG_FILE = DATA_DIR / "pinterest_log.json"
POSTS_FR = Path("posts/fr")
POSTS_EN = Path("posts/en")

BLOG_BASE_URL = "https://saikolabo.github.io/Upline-AI"
PIN_W, PIN_H = 1000, 1500

# Brand palette (from logo.svg)
C_BG       = (10, 10, 10)        # #0A0A0A
C_CARD     = (22, 22, 22)        # #161616
C_VIOLET   = (124, 58, 237)      # #7C3AED  primary accent
C_VIOLET_D = (91, 33, 182)       # #5B21B6  darker violet
C_GREEN    = (0, 255, 148)       # #00FF94  brand secondary
C_WHITE    = (255, 255, 255)
C_OFFWHITE = (226, 226, 240)     # #e2e2f0  wordmark color
C_GRAY     = (156, 163, 175)     # #9CA3AF
C_LGRAY    = (55, 55, 75)        # separator lines

# FR slug detection heuristics
FR_PREFIXES = ("ia-", "comment-", "outil-", "supprimer-", "ameliorer-",
               "creer-", "utiliser-", "generateur-")

# Font downloads (Poppins from Google Fonts GitHub mirror)
FONT_URLS = {
    "bold":    "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf",
    "medium":  "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Medium.ttf",
    "regular": "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Regular.ttf",
}

PINTEREST_SYSTEM = """\
You are a Pinterest content strategist for an AI/gaming tech blog.
Generate highly click-worthy Pinterest content.
- Title: ≤60 characters, benefit-driven, curiosity-inducing
- Description: ≤500 characters, 3-5 SEO keywords woven in naturally, \
ends with a soft call to action
Output ONLY valid JSON: {"title": "...", "description": "..."}
"""


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M')}] {msg}", flush=True)


# ── Font helpers ───────────────────────────────────────────────────────────────

def _ensure_fonts() -> None:
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    for variant, url in FONT_URLS.items():
        dst = FONTS_DIR / f"Poppins-{variant}.ttf"
        if dst.exists():
            continue
        log(f"  Downloading Poppins-{variant}…")
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            dst.write_bytes(r.content)
        except Exception as exc:
            log(f"  Font download failed ({exc}), will use system fallback")


def _font(variant: str, size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        str(FONTS_DIR / f"Poppins-{variant}.ttf"),
        "C:/Windows/Fonts/arialbd.ttf"   if variant in ("bold", "medium") else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ── Drawing helpers ────────────────────────────────────────────────────────────

def _wrap(draw: ImageDraw.Draw, text: str, font: ImageFont.FreeTypeFont,
          max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], []
    for word in words:
        test = " ".join(cur + [word])
        if draw.textbbox((0, 0), test, font=font)[2] <= max_w or not cur:
            cur.append(word)
        else:
            lines.append(" ".join(cur))
            cur = [word]
    if cur:
        lines.append(" ".join(cur))
    return lines


def _glow(canvas: Image.Image, cx: int, cy: int, r: int,
          color: tuple[int, int, int], layers: int = 8) -> None:
    """Composite translucent concentric ellipses for a soft glow."""
    cr, cg, cb = color
    for i in range(layers, 0, -1):
        alpha = int(22 * i)
        radius = int(r * (1.0 + (layers - i) * 0.16))
        ov = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(ov).ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=(cr, cg, cb, alpha),
        )
        canvas.alpha_composite(ov)


def _pill(draw: ImageDraw.Draw, x: int, y: int, text: str,
          font: ImageFont.FreeTypeFont, bg: tuple, fg: tuple,
          pad_x: int = 24, pad_y: int = 10, radius: int = 20) -> int:
    """Draw a rounded pill badge. Returns the x right-edge."""
    tw, th = draw.textbbox((0, 0), text, font=font)[2:4]
    x1, y1 = x + tw + pad_x * 2, y + th + pad_y * 2
    draw.rounded_rectangle([x, y, x1, y1], radius=radius, fill=bg)
    draw.text((x + pad_x, y + pad_y), text, font=font, fill=fg)
    return x1


def _staircase(draw: ImageDraw.Draw, ox: int, oy: int,
               scale: float, color: tuple) -> None:
    """Reproduce the SVG staircase mark at (ox,oy) with given scale."""
    pts = [(5, 42), (5, 28), (16, 28), (16, 17), (27, 17), (27, 9)]
    scaled = [(ox + int(x * scale), oy + int(y * scale)) for x, y in pts]
    for i in range(len(scaled) - 1):
        draw.line([scaled[i], scaled[i + 1]], fill=color, width=max(2, int(scale * 0.7)))
    # Step nodes
    nodes = [(5, 42, C_VIOLET), (16, 28, (147, 85, 232)), (27, 17, C_GREEN)]
    for nx, ny, nc in nodes:
        r = max(3, int(scale * 1.2))
        cx = ox + int(nx * scale)
        cy = oy + int(ny * scale)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=nc)
    # Top glow node (green)
    tx = ox + int(27 * scale)
    ty = oy + int(9 * scale)
    gr = max(5, int(scale * 2.2))
    draw.ellipse([tx - gr, ty - gr, tx + gr, ty + gr], fill=(*C_GREEN, 255))


# ── Image generation ───────────────────────────────────────────────────────────

def generate_image(title: str, description: str, slug: str,
                   lang: str = "en") -> Path:
    """Generate a 1000×1500 Pinterest pin. Returns saved path."""
    PINS_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_fonts()

    # ── RGBA canvas ────────────────────────────────────────────────────────────
    canvas = Image.new("RGBA", (PIN_W, PIN_H), (*C_BG, 255))

    # Glow clusters (upper third)
    _glow(canvas, 500, 390, 270, C_VIOLET, layers=9)
    _glow(canvas, 720, 230, 110, (168, 85, 247), layers=5)   # lighter violet
    _glow(canvas, 300, 500, 90,  (0, 200, 120),  layers=4)   # green hint

    # ── Convert to RGB for opaque drawing ─────────────────────────────────────
    img = canvas.convert("RGB")
    draw = ImageDraw.Draw(img)

    pad = 56  # horizontal margin

    # ── Top + bottom accent bars ───────────────────────────────────────────────
    draw.rectangle([0, 0, PIN_W, 7], fill=C_VIOLET)
    draw.rectangle([0, PIN_H - 7, PIN_W, PIN_H], fill=C_VIOLET)

    # ── Left accent bar (subtle) ───────────────────────────────────────────────
    draw.rectangle([0, 7, 5, PIN_H - 7], fill=C_VIOLET)

    # ── Top branding row ───────────────────────────────────────────────────────
    brand_y = 26
    sc = 1.05  # staircase scale
    _staircase(draw, pad, brand_y, sc, C_VIOLET)
    mark_w = int(27 * sc) + int(5 * sc) + 4

    f_brand = _font("bold", 26)
    draw.text((pad + mark_w + 10, brand_y + 4), "Upline ", font=f_brand, fill=C_OFFWHITE)
    ai_x = pad + mark_w + 10 + draw.textbbox((0, 0), "Upline ", font=f_brand)[2]
    draw.text((ai_x, brand_y + 4), "AI", font=f_brand, fill=C_GREEN)

    # Thin separator
    sep_y = 94
    draw.rectangle([pad, sep_y, PIN_W - pad, sep_y + 1], fill=C_LGRAY)

    # ── Central decorative circle ──────────────────────────────────────────────
    # Dark filled circle so the glow sits on a darker base
    draw.ellipse([360, 168, 640, 448], fill=C_CARD)
    draw.ellipse([378, 186, 622, 430], fill=(18, 8, 40))   # dark violet tint

    # Mini staircase inside circle (large, decorative)
    _staircase(draw, 404, 232, 5.5, C_VIOLET)

    # ── Category badge ─────────────────────────────────────────────────────────
    f_badge = _font("medium", 21)
    badge = "AI TOOLS FOR STREAMERS" if lang == "en" else "OUTILS IA STREAMERS"
    bw = draw.textbbox((0, 0), badge, font=f_badge)[2]
    bx = (PIN_W - bw - 48) // 2
    _pill(draw, bx, 468, badge, f_badge, bg=(40, 14, 90), fg=C_GREEN)

    # ── Title ──────────────────────────────────────────────────────────────────
    f_title = _font("bold", 66)
    lines = _wrap(draw, title, f_title, PIN_W - pad * 2)[:4]
    ty = 558
    line_gap = 80
    for line in lines:
        draw.text((pad, ty), line, font=f_title, fill=C_WHITE)
        ty += line_gap

    # ── Accent divider ─────────────────────────────────────────────────────────
    div_y = ty + 26
    draw.rectangle([pad, div_y, pad + 100, div_y + 4], fill=C_VIOLET)
    draw.rectangle([pad + 110, div_y + 1, pad + 140, div_y + 3], fill=C_GREEN)

    # ── Description ───────────────────────────────────────────────────────────
    f_desc = _font("regular", 32)
    desc_clean = description[:220].replace("\n", " ")
    desc_lines = _wrap(draw, desc_clean, f_desc, PIN_W - pad * 2)[:3]
    dy = div_y + 40
    for dline in desc_lines:
        draw.text((pad, dy), dline, font=f_desc, fill=C_GRAY)
        dy += 48

    # ── Footer ─────────────────────────────────────────────────────────────────
    foot_y = PIN_H - 90
    draw.rectangle([pad, foot_y, PIN_W - pad, foot_y + 1], fill=C_LGRAY)

    f_url = _font("regular", 22)
    url_text = "saikolabo.github.io/Upline-AI"
    uw = draw.textbbox((0, 0), url_text, font=f_url)[2]
    draw.text(((PIN_W - uw) // 2, foot_y + 14), url_text, font=f_url, fill=C_GRAY)

    out = PINS_DIR / f"{slug}.png"
    img.save(str(out), "PNG", optimize=True)
    return out


# ── Article helpers ────────────────────────────────────────────────────────────

def _parse_fm(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    fm: dict[str, str] = {}
    for line in m.group(1).split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip("\"'")
    return fm


def _find_article(slug: str) -> tuple[str, str, str] | None:
    """Return (title, lang, url) for a slug, checking EN then FR."""
    for lang, posts_dir in (("en", POSTS_EN), ("fr", POSTS_FR)):
        if posts_dir.exists():
            for f in posts_dir.glob(f"*{slug}*.md"):
                fm = _parse_fm(f)
                title = fm.get("title", slug)
                article_slug = fm.get("slug", slug)
                url = f"{BLOG_BASE_URL}/{article_slug}/"
                return title, lang, url
    return None


def _is_fr(slug: str) -> bool:
    return (
        any(c in slug for c in "éèàùûîôêç")
        or slug.startswith(FR_PREFIXES)
    )


def _pending_slugs(log_data: dict) -> list[str]:
    """Slugs in published.json not yet pinned."""
    if not PUBLISHED_FILE.exists():
        return []
    all_slugs = json.loads(PUBLISHED_FILE.read_text(encoding="utf-8")).get("slugs", [])
    pinned = {p["slug"] for p in log_data.get("pins", [])}
    return [s for s in all_slugs if s not in pinned]


# ── Log helpers ────────────────────────────────────────────────────────────────

def _load_log() -> dict:
    if PINTEREST_LOG_FILE.exists():
        return json.loads(PINTEREST_LOG_FILE.read_text(encoding="utf-8"))
    return {"pins": []}


def _save_log(data: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    PINTEREST_LOG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── Claude content generation ──────────────────────────────────────────────────

def _generate_content(
    title: str, slug: str, lang: str, client: anthropic.Anthropic
) -> tuple[str, str]:
    prompt = f"""Article title: "{title}"
Slug: {slug}
Language: {"French" if lang == "fr" else "English"}
Niche: AI tools for gamers and streamers

Generate Pinterest-optimized title + description in {"French" if lang == "fr" else "English"}.
JSON only: {{"title": "...", "description": "..."}}"""

    resp = client.messages.create(
        model=MODEL,
        max_tokens=250,
        system=[{"type": "text", "text": PINTEREST_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": prompt}],
    )
    text = re.sub(r"^```[a-z]*\n?", "", resp.content[0].text.strip()).rstrip("` ").strip()
    data = json.loads(text)
    return data["title"][:60], data["description"][:500]


# ── Pinterest API v5 ───────────────────────────────────────────────────────────

def _post_pin(
    title: str,
    description: str,
    link: str,
    image_path: Path,
    board_id: str,
    token: str,
) -> str:
    """Create a pin. Returns pin ID."""
    payload = {
        "board_id": board_id,
        "title": title,
        "description": description,
        "link": link,
        "media_source": {
            "source_type": "image_base64",
            "content_type": "image/png",
            "data": base64.b64encode(image_path.read_bytes()).decode(),
        },
    }
    r = requests.post(
        "https://api.pinterest.com/v5/pins",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["id"]


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    dry_run = "--dry-run" in sys.argv
    preview = "--preview" in sys.argv

    if preview:
        log("PREVIEW — generating sample pin image")
        path = generate_image(
            title="Free AI Noise Cancellation for Your Stream",
            description="Remove background noise in real time with AI tools. "
                        "No expensive gear required — works with any microphone.",
            slug="preview",
            lang="en",
        )
        log(f"Preview saved: {path}")
        return

    if dry_run:
        log("DRY-RUN — images generated, Pinterest API skipped")

    log_data = _load_log()
    pending = _pending_slugs(log_data)

    if not pending:
        log("Nothing new to pin.")
        return

    log(f"Pending slugs: {pending}")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    token = os.environ.get("PINTEREST_ACCESS_TOKEN", "")
    board_fr = os.environ.get("PINTEREST_BOARD_ID_FR", "")
    board_en = os.environ.get("PINTEREST_BOARD_ID_EN", "")

    for slug in pending:
        article = _find_article(slug)
        if not article:
            log(f"Skip (file not found): {slug}")
            continue

        article_title, lang, url = article
        board_id = board_fr if lang == "fr" else board_en

        log(f"Processing [{lang}]: {slug}")

        try:
            pin_title, pin_desc = _generate_content(
                article_title, slug, lang, client
            )
        except Exception as exc:
            log(f"  Claude error: {exc}")
            continue

        try:
            img_path = generate_image(pin_title, pin_desc, slug, lang)
            log(f"  Image: {img_path}")
        except Exception as exc:
            log(f"  Image generation error: {exc}")
            continue

        if dry_run:
            log(f"  [DRY] Would post to board {board_id}")
            log(f"  Title: {pin_title}")
            log(f"  Desc : {pin_desc[:80]}…")
            continue

        try:
            pin_id = _post_pin(pin_title, pin_desc, url, img_path, board_id, token)
            log(f"  Posted pin {pin_id}")
            log_data["pins"].append({
                "slug": slug,
                "pin_id": pin_id,
                "board_id": board_id,
                "lang": lang,
                "posted_at": datetime.utcnow().isoformat() + "Z",
            })
            _save_log(log_data)
        except Exception as exc:
            log(f"  Pinterest API error: {exc}")

        time.sleep(random.uniform(4, 8))

    log("Pinterest run complete")


if __name__ == "__main__":
    main()
