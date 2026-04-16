#!/usr/bin/env python3
"""
FeedlyVP — Weekly "Week in Review" Digest
Reads this week's daily digest_log.json entries, has Claude curate
the top 3 stories per day, then emails a Week in Review via SendGrid.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DIGEST_LOG_FILE = BASE_DIR / "digest_log.json"

MODEL = "claude-sonnet-4-20250514"
DAYS_BACK = 7          # how many days of daily logs to include
TOP_PER_DAY = 3        # Claude picks this many per day


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
# HTML builder
# ---------------------------------------------------------------------------

def _score_color(score: int) -> str:
    if score >= 9:
        return "#10b981"
    if score >= 7:
        return "#f59e0b"
    return "#ef4444"


def _day_label(date_str: str) -> str:
    """Convert '2026-04-14' → 'Tuesday, April 14'."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime(f"%A, %B {dt.day}")
    except ValueError:
        return date_str


def build_weekly_html(
    day_sections: list[dict],   # [{"date": "2026-04-14", "articles": [...]}]
    big_picture: str,
    week_label: str,
) -> str:
    sections_html = ""
    total_articles = 0

    for section in day_sections:
        label = _day_label(section["date"])
        cards = ""
        for a in section["articles"]:
            color = _score_color(a["score"])
            cards += f"""
        <div style="background:#ffffff;border-radius:10px;padding:18px 20px;
                    margin-bottom:12px;border-left:4px solid {color};
                    box-shadow:0 1px 4px rgba(0,0,0,0.07);">
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:7px;">
            <tr>
              <td style="font-size:11px;color:#888;text-transform:uppercase;
                         letter-spacing:0.6px;">{a['source']}</td>
              <td align="right">
                <span style="background:{color};color:#fff;border-radius:12px;
                             padding:2px 9px;font-size:11px;font-weight:700;
                             white-space:nowrap;">{a['score']}/10</span>
              </td>
            </tr>
          </table>
          <h3 style="margin:0 0 7px;font-size:15px;line-height:1.4;color:#111;">
            <a href="{a['url']}" style="color:#111;text-decoration:none;">
              {a['title']}
            </a>
          </h3>
          <p style="margin:0;color:#444;font-size:13px;line-height:1.65;">
            {a['summary']}
          </p>
        </div>"""
            total_articles += 1

        sections_html += f"""
    <div style="margin-bottom:24px;">
      <p style="margin:0 0 10px;font-size:12px;color:#6b7280;text-transform:uppercase;
                letter-spacing:0.8px;font-weight:600;border-bottom:1px solid #e5e7eb;
                padding-bottom:6px;">{label}</p>
      {cards}
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>FeedlyVP Week in Review &mdash; {week_label}</title>
</head>
<body style="margin:0;padding:0;background:#f0f2f5;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,
             'Helvetica Neue',Arial,sans-serif;">
  <div style="max-width:620px;margin:0 auto;padding:20px 12px;">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#064e3b 0%,#065f46 55%,#047857 100%);
                border-radius:12px;padding:30px 24px;margin-bottom:20px;text-align:center;">
      <h1 style="margin:0 0 4px;color:#fff;font-size:26px;font-weight:800;
                 letter-spacing:-0.5px;">FeedlyVP</h1>
      <p style="margin:0 0 2px;color:#6ee7b7;font-size:13px;font-weight:600;
                text-transform:uppercase;letter-spacing:1px;">Week in Review</p>
      <p style="margin:0;color:#a7f3d0;font-size:12px;">{week_label}</p>
    </div>

    <!-- Weekly Big Picture -->
    <div style="background:linear-gradient(135deg,#065f46 0%,#0d9488 100%);
                border-radius:10px;padding:22px 24px;margin-bottom:22px;">
      <p style="margin:0 0 10px;color:#99f6e4;font-size:11px;text-transform:uppercase;
                letter-spacing:1.2px;font-weight:700;">Week in Review</p>
      <p style="margin:0;color:#f0fdf9;font-size:15px;line-height:1.75;">
        {big_picture}
      </p>
    </div>

    <!-- Article count label -->
    <p style="margin:0 0 16px;font-size:12px;color:#6b7280;text-transform:uppercase;
              letter-spacing:0.8px;font-weight:600;">
      Top {total_articles} Stories This Week
    </p>

    <!-- Day sections -->
    {sections_html}

    <!-- Footer -->
    <div style="text-align:center;padding:22px 0 10px;color:#9ca3af;font-size:11px;">
      <p style="margin:0;">Generated by FeedlyVP &nbsp;&middot;&nbsp; Powered by Claude</p>
    </div>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# SendGrid
# ---------------------------------------------------------------------------

def send_email(html: str, subject: str, to_email: str, from_email: str) -> int:
    sg = SendGridAPIClient(os.environ["SENDGRID_API_KEY"])
    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject=subject,
        html_content=html,
    )
    response = sg.send(message)
    return response.status_code


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== FeedlyVP Weekly Digest ===")

    required_env = {
        "ANTHROPIC_API_KEY": "Anthropic API key",
        "SENDGRID_API_KEY": "SendGrid API key",
        "FEEDLYVP_TO_EMAIL": "recipient email address",
        "FEEDLYVP_FROM_EMAIL": "sender email address",
    }
    missing = [k for k in required_env if not os.environ.get(k)]
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
    # 4. Build & send email
    # ------------------------------------------------------------------
    print("\n[4/4] Building HTML and sending email …")
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

    html = build_weekly_html(day_sections, big_picture, week_label)
    subject = f"FeedlyVP Week in Review — {week_label}"

    status = send_email(
        html,
        subject,
        os.environ["FEEDLYVP_TO_EMAIL"],
        os.environ["FEEDLYVP_FROM_EMAIL"],
    )
    print(f"  SendGrid response: HTTP {status}")

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
            "sendgrid_status": status,
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
    print(f"  Sent to:            {os.environ['FEEDLYVP_TO_EMAIL']}")
    print(f"  SendGrid status:    {status}")
    print(f"{'='*36}")


if __name__ == "__main__":
    main()
