#!/usr/bin/env python3
"""
minion_publish.py — Commits new articles, regenerates indexes, notifies Telegram.
Runs at 01:00 after minion_write.py.
"""

import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR = Path("data")
PUBLISHED_FILE = DATA_DIR / "published.json"
POSTS_FR = Path("posts/fr")
POSTS_EN = Path("posts/en")
INDEX_FR = Path("index.md")
INDEX_EN = Path("index-en.md")

TODAY = date.today().isoformat()
TODAY_DISPLAY = date.today().strftime("%d/%m")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M')}] {msg}", flush=True)


# ── Frontmatter parser ────────────────────────────────────────────────────────
def parse_frontmatter(content: str) -> dict:
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    fields = {}
    for line in content[3:end].strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip().strip("\"'")
    return fields


# ── Git helpers ───────────────────────────────────────────────────────────────
def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", check=check
    )


def get_new_files() -> list[Path]:
    """Return untracked + modified .md files in posts/ (handles untracked dirs)."""
    files: list[Path] = []

    # Untracked files (ls-files expands untracked dirs, unlike git status --short)
    untracked = git("ls-files", "--others", "--exclude-standard", "--", "posts/")
    for line in untracked.stdout.splitlines():
        p = Path(line.strip())
        if p.suffix == ".md":
            files.append(p)

    # Modified tracked files
    modified = git("diff", "--name-only", "--", "posts/", check=False)
    for line in modified.stdout.splitlines():
        p = Path(line.strip())
        if p.suffix == ".md" and p not in files:
            files.append(p)

    return files


def configure_git_identity() -> None:
    git("config", "user.name", "Minion Farm Bot")
    git("config", "user.email", "minion-farm-bot@noreply.github.com")


def set_remote_with_token() -> None:
    token = os.environ.get("GH_TOKEN", "")
    username = os.environ.get("GITHUB_USERNAME", "saikolabo")
    repo = os.environ.get("GITHUB_REPO", "minion-farm")
    if token:
        url = f"https://{token}@github.com/{username}/{repo}.git"
        git("remote", "set-url", "origin", url)


def commit_and_push(files: list[Path], message: str) -> bool:
    """Stage files, commit, push. Returns True if a commit was made."""
    for f in files:
        if f.exists():
            git("add", str(f))

    diff = git("diff", "--cached", "--stat")
    if not diff.stdout.strip():
        log("  Rien à committer (déjà à jour)")
        return False

    git("commit", "-m", message)
    set_remote_with_token()
    push = git("push", "origin", "main", check=False)
    if push.returncode != 0:
        log(f"  ERREUR push: {push.stderr.strip()}")
        return False
    return True


# ── Index builders ────────────────────────────────────────────────────────────
def scan_articles(posts_dir: Path) -> list[dict]:
    articles = []
    for f in sorted(posts_dir.glob("*.md"), reverse=True):
        if f.name == ".gitkeep":
            continue
        fm = parse_frontmatter(f.read_text(encoding="utf-8"))
        if not fm:
            continue
        articles.append({
            "filename": f.name,
            "title": fm.get("title", f.stem),
            "date": fm.get("date", ""),
            "description": fm.get("description", ""),
        })
    return articles


def build_index(articles: list[dict], lang: str) -> str:
    if lang == "fr":
        heading = "Outils IA pour gamers et streamers"
        intro = "Toutes nos analyses et comparatifs des meilleurs outils IA pour améliorer ton stream et ton gaming."
        updated = f"*{len(articles)} article(s) — Mis à jour le {TODAY}*"
        subdir = "posts/fr"
        fm_title = "Tous les articles — Outils IA pour gamers et streamers"
    else:
        heading = "AI Tools for Gamers & Streamers"
        intro = "All our reviews and comparisons of the best AI tools to improve your stream and gaming."
        updated = f"*{len(articles)} article(s) — Updated {TODAY}*"
        subdir = "posts/en"
        fm_title = "All Articles — AI Tools for Gamers & Streamers"

    lines = [
        "---",
        f'title: "{fm_title}"',
        f"lang: {lang}",
        "---",
        "",
        f"# {heading}",
        "",
        intro,
        "",
        "---",
        "",
    ]
    for a in articles:
        lines.append(f"### [{a['title']}]({subdir}/{a['filename']})")
        if a["description"]:
            lines.append(f"*{a['date']}* — {a['description']}")
        else:
            lines.append(f"*{a['date']}*")
        lines.append("")
    lines.append(updated)
    return "\n".join(lines)


# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log("  Telegram non configuré — skip")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        r.raise_for_status()
        log("  Notification Telegram envoyée")
    except Exception as exc:
        log(f"  Telegram error: {exc}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    log("Publish démarré")

    # ── 1. Detect new articles ─────────────────────────────────────────────────
    new_files = get_new_files()
    new_fr = [f for f in new_files if f.parent.name == "fr"]
    new_en = [f for f in new_files if f.parent.name == "en"]

    log(f"Nouveaux fichiers: {len(new_fr)} FR, {len(new_en)} EN")
    for f in new_files:
        log(f"  {f}")

    if not new_files:
        log("Aucun article à publier — fin")
        return

    # ── 2. Collect titles for Telegram ────────────────────────────────────────
    new_titles = []
    for f in new_fr:
        if f.exists():
            fm = parse_frontmatter(f.read_text(encoding="utf-8"))
            if fm.get("title"):
                new_titles.append(fm["title"])

    # ── 3. Regenerate index files ─────────────────────────────────────────────
    log("Génération des index...")
    articles_fr = scan_articles(POSTS_FR)
    articles_en = scan_articles(POSTS_EN)

    INDEX_FR.write_text(build_index(articles_fr, "fr"), encoding="utf-8")
    INDEX_EN.write_text(build_index(articles_en, "en"), encoding="utf-8")
    log(f"  index.md ({len(articles_fr)} articles FR)")
    log(f"  index-en.md ({len(articles_en)} articles EN)")

    # ── 4. Commit + push ──────────────────────────────────────────────────────
    log("Commit et push...")
    configure_git_identity()

    files_to_commit = new_files + [INDEX_FR, INDEX_EN]
    commit_msg = f"nightly [{TODAY}]: +{len(new_fr)} FR, +{len(new_en)} EN"

    try:
        pushed = commit_and_push(files_to_commit, commit_msg)
    except subprocess.CalledProcessError as exc:
        log(f"  ERREUR git: {exc.stderr}")
        sys.exit(1)

    if pushed:
        log(f"  Push OK: {commit_msg}")

    # ── 5. Telegram notification ──────────────────────────────────────────────
    log("Envoi notification Telegram...")
    total = len(articles_fr)
    titles_str = " — ".join(f"<i>{t}</i>" for t in new_titles) if new_titles else "?"
    message = (
        f"<b>Nuit du {TODAY_DISPLAY}</b> : {len(new_fr)} article(s) publié(s)\n"
        f"{titles_str}\n"
        f"Total publié : {total} article(s)"
    )
    send_telegram(message)

    log(f"Publish terminé — {len(new_files)} fichiers publiés")


if __name__ == "__main__":
    main()
