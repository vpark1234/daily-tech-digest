#!/usr/bin/env python3
"""
FeedlyVP — Weekly "Week in Review" Digest
Reads this week's daily digest_log.json entries, has Claude curate
the top 3 stories per day, then delivers via Telegram.
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
from telegram import Bot
from telegram.constants import ParseMode

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DIGEST_LOG_FILE = BASE_DIR / "digest_log.json"

MODEL = "claude-sonnet-4-20250514"
DAYS_BACK = 7       # how many days of daily logs to include
TOP_PER_DAY = 3     # Claude picks this many per day


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------

def load_digest_log() -> list:
    if DIGEST_LOG_FILE.exists():
        with open(DIGEST_LOG_FILE) as f:
            return json.load(f)
    return []


def save_digest_log(log: list) -> None:
    with open(DIGEST_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


def get_week_entries(log: list, today: datetime) -> list[dict]:
    """Return daily entries whose 'date' falls within the past DAYS_BACK days."""
    cutoff = (today - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")
    return [
        e for e in log
        if e.get("type") == "daily"
        and "date" in e
        and cutoff <= e["date"] <= today_str
        and e.get("articles")
    ]


# ---------------------------------------------------------------------------
# Claude helpers
# ---------------------------------------------------------------------------

def pick_top_articles(
    client: anthropic.Anthropic, day_entry: dict, n: int
) -> list[dict]:
    """Ask Claude to pick the n most impactful articles from one day's entry."""
    articles = day_entry["articles"]
    if len(articles) <= n:
        return articles

    numbered = "\n".join(
        f"{i}. [{a['score']}/10] {a['title']} ({a['source']})\n   {a['summary']}"
        for i, a in enumerate(articles, 1)
    )
    prompt = (
        f"These are the top articles from {day_entry['date']} "
        f"for an Oracle NetSuite Solution Consultant:\n\n"
        f"{numbered}\n\n"
        f"Pick the {n} most impactful for someone who pitches ERP and enterprise AI. "
        f"Return ONLY a JSON array of the chosen 1-based index numbers, e.g. [2, 5, 7]."
    )
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=64,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        match = re.search(r"\[[\d,\s]+\]", raw)
        if not match:
            raise ValueError("No index array found")
        indices = json.loads(match.group())
        chosen = []
        for idx in indices[:n]:
            if 1 <= idx <= len(articles):
                chosen.append(articles[idx - 1])
        return chosen if chosen else articles[:n]
    except Exception as exc:
        print(f"  [WARN] Claude pick failed for {day_entry['date']}: {exc}", file=sys.stderr)
        return articles[:n]


def get_weekly_big_picture(client: anthropic.Anthropic, all_picks: list[dict]) -> str:
    """Generate a weekly synthesis paragraph."""
    headlines = "\n".join(
        f"- {a['title']} ({a['source']}): {a['summary']}"
        for a in all_picks
    )
    prompt = (
        "Here are this week's top enterprise tech stories:\n\n"
        f"{headlines}\n\n"
        "Write a 3-sentence 'Week in Review' paragraph synthesizing the major themes "
        "and what they mean for enterprise software buyers and ERP solution consultants. "
        "Be direct and forward-looking."
    )
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=350,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        print(f"  [WARN] Weekly Big Picture failed: {exc}", file=sys.stderr)
        return (
            "This week brought notable developments across enterprise AI, ERP, and "
            "automation — themes worth tracking as the market accelerates."
        )


# ---------------------------------------------------------------------------
# Telegram delivery
# ---------------------------------------------------------------------------

def _score_emoji(score: int) -> str:
    if score == 10: return "🔥"
    if score == 9:  return "⭐"
    if score == 8:  return "🔵"
    return "🟢"


def _tg_escape(text: str) -> str:
    """Escape a string for Telegram MarkdownV2."""
    text = text.replace("\\", "\\\\")
    for ch in ("_", "*", "[", "]", "(", ")", "~", "`", ">",
               "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(ch, f"\\{ch}")
    return text


def _tg_escape_url(url: str) -> str:
    """Escape a URL for use inside a MarkdownV2 inline link [text](url)."""
    return url.replace("\\", "\\\\").replace(")", "\\)")


def _tg_day_label(date_str: str) -> str:
    """Convert '2026-04-14' → 'Tuesday, April 14'."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime(f"%A, %B {dt.day}")
    except ValueError:
        return date_str


async def _send_weekly_telegram(
    bot_token: str,
    chat_id: str,
    day_sections: list[dict],
    big_picture: str,
    week_label: str,
    all_picks: list[dict],
) -> int:
    """Send weekly digest to Telegram. Returns total messages sent."""
    delay = 1  # seconds between messages
    sent = 0

    async with Bot(token=bot_token) as bot:
        # ── 1. Hero message ─────────────────────────────────────────────
        hero = (
            f"📅 *FeedlyVP — Week in Review*\n"
            f"{_tg_escape(week_label)}\n\n"
            f"{_tg_escape(big_picture)}\n\n"
            f"📊 {len(all_picks)} top stories across {len(day_sections)} days"
        )
        await bot.send_message(chat_id=chat_id, text=hero, parse_mode=ParseMode.MARKDOWN_V2)
        sent += 1
        await asyncio.sleep(delay)

        # ── 2. One message per day ───────────────────────────────────────
        for section in day_sections:
            label = _tg_day_label(section["date"])
            articles_text = ""
            for a in section["articles"]:
                emoji = _score_emoji(a["score"])
                articles_text += (
                    f"\n{emoji} *{_tg_escape(a['title'])}*\n"
                    f"🗂 {_tg_escape(a.get('category', ''))} · "
                    f"📰 {_tg_escape(a.get('source', ''))}\n"
                    f"{_tg_escape(a.get('summary', ''))}\n"
                    f"🔗 [Read]({_tg_escape_url(a['url'])})\n"
                )

            day_msg = f"📆 *{_tg_escape(label)}*\n{articles_text}"
            await bot.send_message(
                chat_id=chat_id, text=day_msg, parse_mode=ParseMode.MARKDOWN_V2
            )
            sent += 1
            await asyncio.sleep(delay)

        # ── 3. Closing message ───────────────────────────────────────────
        closing = (
            f"✅ Week in Review complete — "
            f"{len(all_picks)} articles from {len(day_sections)} days\n"
            "See you next Sunday."
        )
        await bot.send_message(chat_id=chat_id, text=closing)
        sent += 1

    return sent


def deliver_weekly_telegram(
    bot_token: str,
    chat_id: str,
    day_sections: list[dict],
    big_picture: str,
    week_label: str,
    all_picks: list[dict],
) -> int:
    """Synchronous entry point for weekly Telegram delivery."""
    return asyncio.run(
        _send_weekly_telegram(
            bot_token, chat_id, day_sections, big_picture, week_label, all_picks
        )
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== FeedlyVP Weekly Digest ===")

    required_env = {
        "ANTHROPIC_API_KEY": "Anthropic API key",
        "TELEGRAM_BOT_TOKEN": "Telegram bot token",
        "TELEGRAM_CHAT_ID": "Telegram chat ID",
    }
    missing = [k for k in required_env if not os.environ.get(k, "").strip()]
    if missing:
        for k in missing:
            print(f"ERROR: {k} ({required_env[k]}) is not set", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # ------------------------------------------------------------------
    # 1. Load this week's daily entries
    # ------------------------------------------------------------------
    print("\n[1/4] Loading digest_log.json …")
    log = load_digest_log()
    week_entries = get_week_entries(log, now)
    week_entries.sort(key=lambda e: e["date"])

    print(f"  Found {len(week_entries)} daily entries in the past {DAYS_BACK} days")
    if not week_entries:
        print("No daily entries found for this week. Nothing to send.")
        sys.exit(0)

    # ------------------------------------------------------------------
    # 2. Have Claude pick top 3 per day
    # ------------------------------------------------------------------
    print(f"\n[2/4] Claude curating top {TOP_PER_DAY} per day …")
    day_sections: list[dict] = []
    all_picks: list[dict] = []

    for entry in week_entries:
        picks = pick_top_articles(client, entry, TOP_PER_DAY)
        day_sections.append({"date": entry["date"], "articles": picks})
        all_picks.extend(picks)
        print(f"  {entry['date']}: {len(entry['articles'])} → {len(picks)} picked")

    # ------------------------------------------------------------------
    # 3. Weekly Big Picture
    # ------------------------------------------------------------------
    print("\n[3/4] Writing weekly Big Picture …")
    big_picture = get_weekly_big_picture(client, all_picks)

    # ------------------------------------------------------------------
    # 4. Deliver to Telegram
    # ------------------------------------------------------------------
    print("\n[4/4] Delivering to Telegram …")
    start_date = week_entries[0]["date"]
    end_date = week_entries[-1]["date"]
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        week_label = (
            f"{start_dt.strftime(f'%B {start_dt.day}')} – "
            f"{end_dt.strftime(f'%B {end_dt.day}, %Y')}"
        )
    except ValueError:
        week_label = f"{start_date} – {end_date}"

    messages_sent = deliver_weekly_telegram(
        os.environ["TELEGRAM_BOT_TOKEN"],
        os.environ["TELEGRAM_CHAT_ID"],
        day_sections,
        big_picture,
        week_label,
        all_picks,
    )
    print(f"  Sent {messages_sent} Telegram messages")

    # ------------------------------------------------------------------
    # Append weekly summary entry to digest_log.json
    # ------------------------------------------------------------------
    log.append(
        {
            "type": "weekly",
            "date": now.strftime("%Y-%m-%d"),
            "run_at": now.isoformat(),
            "days_covered": len(week_entries),
            "articles_featured": len(all_picks),
            "telegram_messages_sent": messages_sent,
            "big_picture": big_picture,
        }
    )
    save_digest_log(log)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(f"\n{'='*36}")
    print(f"  Days with articles: {len(week_entries)}")
    print(f"  Articles featured:  {len(all_picks)}")
    print(f"  Telegram messages:  {messages_sent}")
    print(f"{'='*36}")


if __name__ == "__main__":
    main()
