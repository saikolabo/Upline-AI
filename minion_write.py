#!/usr/bin/env python3
"""
minion_write.py — Generates FR + EN SEO articles for each topic in topics.json.
Reads data/topics.json, skips already-published slugs, calls Claude API (FR),
then calls minion_translate for the EN version.
Runs at 00:00 after minion_research.py.
"""

import json
import os
import re
import sys
import time
import random
import unicodedata
from datetime import date, datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from minion_translate import translate_article

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2000

DATA_DIR = Path("data")
TOPICS_FILE = DATA_DIR / "topics.json"
PUBLISHED_FILE = DATA_DIR / "published.json"
POSTS_FR = Path("posts/fr")
POSTS_EN = Path("posts/en")
TODAY = date.today().isoformat()

# ── Affiliate links — update with tracking URLs after joining programs ──────────
AFFILIATE_LINKS: dict[str, str] = {
    "krisp": "https://krisp.ai/",
    "nvidia broadcast": "https://www.nvidia.com/broadcast",
    "cleanvoice ai": "https://cleanvoice.ai/",
    "capsule": "https://getcapsule.ai/",
    "streamlabs": "https://streamlabs.com/",
    "voicemod": "https://www.voicemod.net/",
    "epidemic sound": "https://www.epidemicsound.com/",
    "ai coaching tools": "https://streamlabs.com/",
}

# ── System prompt (cacheable — same for all articles) ─────────────────────────
WRITE_SYSTEM = """\
Tu es un expert en outils IA pour gamers et streamers.
Tu rédiges des articles SEO de haute qualité en [LANGUE] pour un blog d'affiliation.

Structure OBLIGATOIRE :
1. Frontmatter YAML entre --- : title, date, description (160 chars max avec keyword), lang, slug
2. Introduction accrocheuse ~100 mots qui accroche le lecteur
3. 3 à 5 sections H2 avec contenu concret, comparatifs et conseils pratiques
4. Liens d'affiliation intégrés naturellement dans le texte (format Markdown [texte](url))
5. Conclusion avec call-to-action clair

Règles strictes :
- Longueur totale : 900-1100 mots (hors frontmatter)
- Ton : expert mais accessible, sans jargon inutile
- 2-3 liens d'affiliation maximum, jamais de liste de liens bruts
- Commence DIRECTEMENT par --- sans texte avant
- N'utilise JAMAIS les balises ```yaml ou ```markdown"""


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


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[-\s]+", "-", text).strip("-")


def clean_article(text: str) -> str:
    """Remove any preamble before the YAML frontmatter that Claude might add."""
    idx = text.find("---")
    if idx > 0:
        text = text[idx:]
    # Strip accidental code fences
    text = re.sub(r"^```[a-z]*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```$", "", text, flags=re.MULTILINE)
    return text.strip()


def build_affiliate_block(affiliate_angle: str) -> str:
    tools = [t.strip() for t in affiliate_angle.split(",")]
    lines = []
    for tool in tools:
        url = AFFILIATE_LINKS.get(tool.lower(), f"https://{tool.replace(' ', '')}.ai/")
        lines.append(f"- {tool.title()}: {url}")
    return "\n".join(lines)


def generate_fr_article(topic: dict, client: anthropic.Anthropic) -> str:
    """Generate the French article for a topic."""
    slug = slugify(topic["keyword_fr"])
    system = WRITE_SYSTEM.replace("[LANGUE]", "français")

    user_prompt = f"""Rédige un article SEO en français sur ce sujet.

Titre : {topic['title_fr']}
Keyword principal : {topic['keyword_fr']}
Date : {TODAY}
Slug : {slug}

Liens d'affiliation à intégrer naturellement (choisis 2-3 parmi cette liste) :
{build_affiliate_block(topic['affiliate_angle'])}

Format frontmatter attendu :
---
title: "{topic['title_fr']}"
date: {TODAY}
description: "Description SEO de 150-160 caractères contenant le keyword"
lang: fr
slug: "{slug}"
---"""

    def _call():
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text

    return clean_article(with_retry(_call))


def load_published() -> dict:
    if PUBLISHED_FILE.exists():
        return json.loads(PUBLISHED_FILE.read_text(encoding="utf-8"))
    return {"slugs": []}


def save_published(data: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    PUBLISHED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_article(content: str, lang: str, filename: str) -> Path:
    base_dir = POSTS_FR if lang == "fr" else POSTS_EN
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


def main() -> None:
    log("Write démarré")

    if not TOPICS_FILE.exists():
        log("ERREUR: data/topics.json introuvable — lance d'abord minion_research.py")
        sys.exit(1)

    topics = json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
    published = load_published()
    published_slugs: set[str] = set(published.get("slugs", []))

    log(f"Topics: {len(topics)} | Déjà publiés: {len(published_slugs)}")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    generated: list[str] = []

    for i, topic in enumerate(topics, 1):
        slug_fr = slugify(topic["keyword_fr"])
        slug_en = slugify(topic["keyword_en"])
        filename_fr = f"{TODAY}-{slug_fr}.md"
        filename_en = f"{TODAY}-{slug_en}.md"

        if slug_fr in published_slugs:
            log(f"[{i}/{len(topics)}] Skip (déjà publié): {slug_fr}")
            continue

        # ── Generate FR ───────────────────────────────────────────────────────
        log(f"[{i}/{len(topics)}] FR: {topic['title_fr'][:60]}...")
        try:
            fr_content = generate_fr_article(topic, client)
            fr_path = save_article(fr_content, "fr", filename_fr)
            log(f"  Sauvegardé -> {fr_path}")
        except Exception as exc:
            log(f"  ERREUR génération FR: {exc}")
            continue

        time.sleep(random.uniform(2.0, 4.0))

        # ── Translate to EN ───────────────────────────────────────────────────
        log(f"[{i}/{len(topics)}] EN: traduction...")
        en_path = None
        try:
            en_content = translate_article(fr_content, client)
            en_content = clean_article(en_content)
            # Update slug in EN frontmatter to the English slug
            en_content = re.sub(
                r'(^slug:\s*["\']?)' + re.escape(slug_fr) + r'(["\']?\s*$)',
                rf'\g<1>{slug_en}\2',
                en_content, count=1, flags=re.MULTILINE,
            )
            en_path = save_article(en_content, "en", filename_en)
            log(f"  Sauvegardé -> {en_path}")
        except Exception as exc:
            log(f"  ERREUR traduction EN: {exc}")

        # ── Update published.json immediately (crash-safe) ────────────────────
        new_slugs = [slug_fr] + ([slug_en] if en_path else [])
        published_slugs.update(new_slugs)
        published["slugs"] = sorted(published_slugs)
        save_published(published)

        generated.extend([str(fr_path)] + ([str(en_path)] if en_path else []))

        if i < len(topics):
            time.sleep(random.uniform(3.0, 6.0))

    # ── Summary ───────────────────────────────────────────────────────────────
    log(f"Write terminé — {len(generated)} fichiers générés")
    for f in generated:
        log(f"  {f}")


if __name__ == "__main__":
    main()
