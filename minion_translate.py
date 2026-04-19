#!/usr/bin/env python3
"""
minion_translate.py — Translates a French SEO article to English using Claude.
Called by minion_write.py; also runnable standalone:
  python minion_translate.py posts/fr/2026-04-19-some-slug.md
"""

import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2000

SYSTEM = """\
You are an expert FR→EN translator specialized in gaming and streaming content.
Translate French SEO articles to English for an English-speaking audience.

Rules:
- Adapt idioms naturally — never translate word for word
- Keep all Markdown links and affiliate URLs exactly as they are
- Change only "lang: fr" → "lang: en" in the YAML frontmatter
- Title and description in frontmatter must be in English
- Target length: 900-1100 words (same as the original)
- Preserve all Markdown formatting and YAML frontmatter structure
- Output ONLY the translated article, starting with ---
- No preamble, no explanation"""


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M')}] {msg}", flush=True)


def with_retry(func, retries: int = 3, base_delay: float = 2.0):
    for attempt in range(retries):
        try:
            return func()
        except (anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            if attempt == retries - 1:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0.5, 2.0)
            log(f"  Retry {attempt + 1}/{retries} — {exc} — attente {delay:.1f}s")
            time.sleep(delay)


def translate_article(fr_content: str, client: anthropic.Anthropic) -> str:
    """Return the English version of a French markdown article."""

    def _call():
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[
                {
                    "role": "user",
                    "content": f"Translate this French article to English:\n\n{fr_content}",
                }
            ],
        )
        return response.content[0].text

    return with_retry(_call)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python minion_translate.py <posts/fr/YYYY-MM-DD-slug.md>")
        sys.exit(1)

    fr_path = Path(sys.argv[1])
    if not fr_path.exists():
        print(f"Fichier introuvable : {fr_path}")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    fr_content = fr_path.read_text(encoding="utf-8")

    log(f"Traduction de {fr_path.name}...")
    en_content = translate_article(fr_content, client)

    en_path = Path("posts/en") / fr_path.name
    en_path.parent.mkdir(parents=True, exist_ok=True)
    en_path.write_text(en_content, encoding="utf-8")
    log(f"Article EN sauvegardé -> {en_path}")
