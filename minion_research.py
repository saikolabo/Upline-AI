#!/usr/bin/env python3
"""
minion_research.py — Nightly topic discovery for Minion Farm.
Runs at 22:00. Scores candidate topics via pytrends, Reddit, and Google
Autocomplete, then saves the top 3 to data/topics.json.
"""

import json
import random
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
DATA_DIR = Path("data")
TOPICS_FILE = DATA_DIR / "topics.json"
PUBLISHED_FILE = DATA_DIR / "published.json"

# ── Candidate topics (FR/EN pairs, pre-curated for 3 weeks of content) ───────
TOPIC_PAIRS = [
    {
        "title_fr": "Les 5 meilleurs outils IA pour streamers en 2026",
        "title_en": "5 Best AI Tools for Streamers in 2026",
        "kw_fr": "meilleur outil IA streamer 2026",
        "kw_en": "best AI tool for streamers 2026",
        "affiliate": "krisp, streamlabs, nvidia broadcast",
        "tags": ["stream", "tool", "ai", "best"],
    },
    {
        "title_fr": "Supprimer le bruit de fond avec l'IA : les meilleures solutions gratuites",
        "title_en": "Free AI Noise Cancellation for Streamers: Top Picks 2026",
        "kw_fr": "supprimer bruit micro IA gratuit",
        "kw_en": "free AI noise cancellation streaming",
        "affiliate": "krisp, nvidia broadcast, cleanvoice ai",
        "tags": ["noise", "micro", "audio", "free"],
    },
    {
        "title_fr": "Créer des clips Twitch automatiquement avec l'IA en 2026",
        "title_en": "Best AI Clip Maker for Twitch in 2026 (Auto Highlights)",
        "kw_fr": "IA pour faire des clips automatiques",
        "kw_en": "AI automatic clip maker twitch",
        "affiliate": "capsule, streamlabs",
        "tags": ["clip", "highlight", "twitch", "auto"],
    },
    {
        "title_fr": "Comment améliorer la qualité de ton stream grâce à l'IA",
        "title_en": "How to Use AI to Grow Your Twitch Channel in 2026",
        "kw_fr": "comment améliorer qualité stream avec IA",
        "kw_en": "how to use AI to grow twitch channel",
        "affiliate": "streamlabs, krisp, nvidia broadcast",
        "tags": ["stream", "quality", "grow", "twitch"],
    },
    {
        "title_fr": "Meilleur outil IA de coaching gaming : comparatif 2026",
        "title_en": "Best Free AI Game Coaching Tools in 2026",
        "kw_fr": "outil IA coaching gaming",
        "kw_en": "AI game coaching tool free",
        "affiliate": "streamlabs, ai coaching tools",
        "tags": ["coach", "gaming", "fps", "free"],
    },
    {
        "title_fr": "Les meilleurs overlays IA pour OBS en 2026",
        "title_en": "Best AI Overlays for OBS Streamers in 2026",
        "kw_fr": "meilleure IA pour overlay stream",
        "kw_en": "best AI overlay for OBS",
        "affiliate": "streamlabs, nvidia broadcast",
        "tags": ["overlay", "obs", "stream", "ai"],
    },
    {
        "title_fr": "IA de transcription pour Twitch : quels outils choisir en 2026 ?",
        "title_en": "Best AI Transcription Tools for Streamers in 2026",
        "kw_fr": "IA transcription stream twitch",
        "kw_en": "AI stream transcription tool",
        "affiliate": "cleanvoice ai, streamlabs",
        "tags": ["transcription", "caption", "twitch", "ai"],
    },
    {
        "title_fr": "Générer des highlights automatiquement avec l'IA : top outils 2026",
        "title_en": "Best AI Highlight Generator for Streamers in 2026",
        "kw_fr": "outil IA créer highlights automatique",
        "kw_en": "automatic highlight generator AI",
        "affiliate": "capsule, streamlabs",
        "tags": ["highlight", "clip", "automatic", "ai"],
    },
    {
        "title_fr": "Améliorer ses FPS avec l'IA : les solutions en 2026",
        "title_en": "AI FPS Improvement Tools for Gamers in 2026",
        "kw_fr": "IA pour améliorer FPS gaming",
        "kw_en": "AI FPS improvement tool",
        "affiliate": "nvidia broadcast, streamlabs",
        "tags": ["fps", "gaming", "performance", "ai"],
    },
    {
        "title_fr": "Meilleures alternatives gratuites à Krisp en 2026",
        "title_en": "Best Free Krisp Alternatives with AI in 2026",
        "kw_fr": "meilleure alternative Krisp gratuite",
        "kw_en": "best Krisp alternative free AI",
        "affiliate": "cleanvoice ai, nvidia broadcast, voicemod",
        "tags": ["krisp", "noise", "alternative", "free"],
    },
]

REDDIT_SUBS = ["Twitch", "LivestreamFail", "pcgaming", "artificial"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ── Logging ───────────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M')}] {msg}", flush=True)


# ── Retry helper ──────────────────────────────────────────────────────────────
def with_retry(func, retries: int = 3, base_delay: float = 2.0):
    for attempt in range(retries):
        try:
            return func()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0.3, 1.0)
            log(f"  Retry {attempt + 1}/{retries} — {exc} — attente {delay:.1f}s")
            time.sleep(delay)


# ── Source 1 : pytrends ───────────────────────────────────────────────────────
def get_pytrends_scores(keywords: list[str]) -> dict[str, float]:
    """Return mean interest score (0–100) per keyword over the last 7 days."""
    try:
        from pytrends.request import TrendReq
    except ImportError:
        log("  pytrends non disponible — trends ignorés")
        return {kw: 0.0 for kw in keywords}

    scores: dict[str, float] = {}
    pt = TrendReq(hl="fr-FR", tz=60, timeout=(10, 30))

    for i in range(0, len(keywords), 5):
        batch = keywords[i : i + 5]
        try:
            def _fetch(b=batch):
                pt.build_payload(b, timeframe="now 7-d", geo="")
                return pt.interest_over_time()

            df = with_retry(_fetch, retries=2, base_delay=5.0)
            if df is not None and not df.empty:
                for kw in batch:
                    scores[kw] = float(df[kw].mean()) if kw in df.columns else 0.0
            else:
                for kw in batch:
                    scores[kw] = 0.0
        except Exception as exc:
            log(f"  pytrends batch error: {exc}")
            for kw in batch:
                scores[kw] = 0.0

        time.sleep(random.uniform(2.5, 4.5))

    return scores


# ── Source 2 : Reddit ─────────────────────────────────────────────────────────
def scrape_reddit(subs: list[str]) -> list[dict]:
    """Fetch hot posts from subreddits via public JSON API."""
    posts: list[dict] = []
    for sub in subs:
        try:
            def _fetch(s=sub):
                r = requests.get(
                    f"https://www.reddit.com/r/{s}/hot.json?limit=25",
                    headers=HEADERS,
                    timeout=10,
                )
                r.raise_for_status()
                return r.json()

            data = with_retry(_fetch)
            for child in data.get("data", {}).get("children", []):
                d = child.get("data", {})
                posts.append({
                    "title": d.get("title", "").lower(),
                    "score": d.get("score", 0),
                    "comments": d.get("num_comments", 0),
                })
            time.sleep(random.uniform(0.8, 1.8))
        except Exception as exc:
            log(f"  Reddit r/{sub} error: {exc}")

    return posts


# ── Source 3 : Google Autocomplete ────────────────────────────────────────────
def get_autocomplete(query: str, lang: str = "en") -> list[str]:
    """Fetch Google Suggest completions for a query (no API key needed)."""
    try:
        def _fetch():
            r = requests.get(
                "https://suggestqueries.google.com/complete/search",
                params={"client": "firefox", "q": query, "hl": lang},
                headers=HEADERS,
                timeout=8,
            )
            r.raise_for_status()
            return r.json()

        data = with_retry(_fetch, retries=2, base_delay=1.0)
        return data[1] if isinstance(data, list) and len(data) > 1 else []
    except Exception as exc:
        log(f"  Autocomplete '{query[:40]}': {exc}")
        return []


# ── Scoring ───────────────────────────────────────────────────────────────────
def reddit_relevance(posts: list[dict], tags: list[str]) -> float:
    """0–100: how many Reddit posts mention any tag from this topic."""
    if not posts:
        return 0.0
    hits = sum(1 for p in posts if any(t in p["title"] for t in tags))
    # Amplify: even 1-2 matching posts out of 100 is a signal
    return min(hits / len(posts) * 600, 100.0)


def score_topic(
    pair: dict,
    trends_en: dict[str, float],
    trends_fr: dict[str, float],
    reddit_posts: list[dict],
    ac_en: list[str],
    ac_fr: list[str],
) -> float:
    trend = (trends_en.get(pair["kw_en"], 0.0) + trends_fr.get(pair["kw_fr"], 0.0)) / 2
    reddit = reddit_relevance(reddit_posts, pair["tags"])
    autocomplete = min((len(ac_en) + len(ac_fr)) * 7, 100.0)
    return trend * 0.50 + reddit * 0.30 + autocomplete * 0.20


def _label(score: float, thresholds: tuple[float, float], labels: tuple[str, str, str]) -> str:
    if score >= thresholds[0]:
        return labels[0]
    if score >= thresholds[1]:
        return labels[1]
    return labels[2]


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    log("Research démarré")
    DATA_DIR.mkdir(exist_ok=True)

    # Skip slugs that are already published
    published_slugs: set[str] = set()
    if PUBLISHED_FILE.exists():
        pub = json.loads(PUBLISHED_FILE.read_text(encoding="utf-8"))
        published_slugs = set(pub.get("slugs", []))
    log(f"Slugs déjà publiés : {len(published_slugs)}")

    # ── 1. pytrends ───────────────────────────────────────────────────────────
    log("Google Trends (pytrends)…")
    kws_en = [p["kw_en"] for p in TOPIC_PAIRS]
    kws_fr = [p["kw_fr"] for p in TOPIC_PAIRS]
    trends_en = get_pytrends_scores(kws_en)
    time.sleep(3)
    trends_fr = get_pytrends_scores(kws_fr)
    log(
        f"  Données disponibles — EN: "
        f"{sum(1 for v in trends_en.values() if v > 0)}/{len(kws_en)}, "
        f"FR: {sum(1 for v in trends_fr.values() if v > 0)}/{len(kws_fr)}"
    )

    # ── 2. Reddit ─────────────────────────────────────────────────────────────
    log("Scraping Reddit…")
    reddit_posts = scrape_reddit(REDDIT_SUBS)
    log(f"  {len(reddit_posts)} posts récupérés")

    # ── 3. Google Autocomplete (sampled to save time) ─────────────────────────
    log("Google Autocomplete…")
    ac_cache: dict[str, list[str]] = {}
    for pair in TOPIC_PAIRS[:7]:
        ac_cache[pair["kw_en"]] = get_autocomplete(pair["kw_en"], "en")
        ac_cache[pair["kw_fr"]] = get_autocomplete(pair["kw_fr"], "fr")
        time.sleep(0.5)
    log(f"  Suggestions récupérées pour {len(ac_cache)} requêtes")

    # ── 4. Score all candidates ───────────────────────────────────────────────
    log("Calcul des scores…")
    scored: list[tuple[float, dict]] = []
    for pair in TOPIC_PAIRS:
        slug = pair["kw_en"].lower().replace(" ", "-")
        if slug in published_slugs:
            log(f"  Skip (déjà publié) : {slug}")
            continue
        s = score_topic(
            pair,
            trends_en,
            trends_fr,
            reddit_posts,
            ac_en=ac_cache.get(pair["kw_en"], []),
            ac_fr=ac_cache.get(pair["kw_fr"], []),
        )
        scored.append((s, pair))

    scored.sort(key=lambda x: x[0], reverse=True)
    top3 = scored[:3]

    # ── 5. Build output JSON ──────────────────────────────────────────────────
    topics = [
        {
            "title_fr": pair["title_fr"],
            "title_en": pair["title_en"],
            "keyword_fr": pair["kw_fr"],
            "keyword_en": pair["kw_en"],
            "search_volume_estimate": _label(s, (55.0, 25.0), ("high", "medium", "low")),
            "competition": _label(s, (70.0, 40.0), ("high", "medium", "low")),
            "affiliate_angle": pair["affiliate"],
        }
        for s, pair in top3
    ]

    TOPICS_FILE.write_text(
        json.dumps(topics, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ── 6. Display ────────────────────────────────────────────────────────────
    log(f"Research terminé — {len(topics)} topics trouvés -> {TOPICS_FILE}")
    print()
    print("=" * 65)
    print("  TOP 3 TOPICS DU JOUR")
    print("=" * 65)
    for i, (score, pair) in enumerate(top3, 1):
        vol = _label(score, (55.0, 25.0), ("high", "medium", "low"))
        comp = _label(score, (70.0, 40.0), ("high", "medium", "low"))
        print(f"\n#{i}  Score composite : {score:.1f}  [{vol} volume / {comp} competition]")
        print(f"  FR : {pair['title_fr']}")
        print(f"  EN : {pair['title_en']}")
        print(f"  Affiliation : {pair['affiliate']}")
    print()
    print("=" * 65)


if __name__ == "__main__":
    main()
