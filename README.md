# FeedlyVP

Personal enterprise tech news digest. Fetches RSS feeds, scores each article
with Claude, and emails the top stories to your inbox via SendGrid — once per run,
no repeats across runs.

---

## Files

| File | Purpose |
|---|---|
| `digest.py` | Main script — run this |
| `feeds.yaml` | Feed list, weights, and keyword hints |
| `requirements.txt` | Pinned Python dependencies |
| `seen_urls.json` | Tracks already-processed article URLs (auto-managed) |
| `digest_log.json` | Run history log (auto-managed) |

---

## Setup

### 1. Python environment

Requires Python 3.10+.

```bash
cd FeedlyVP
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment variables

Export these in your shell, `.env` file, or shell profile:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."        # https://console.anthropic.com
export SENDGRID_API_KEY="SG...."             # https://app.sendgrid.com/settings/api_keys
export FEEDLYVP_TO_EMAIL="you@example.com"   # where the digest is delivered
export FEEDLYVP_FROM_EMAIL="digest@example.com"  # must be a verified SendGrid sender
```

**SendGrid sender verification:** your `FEEDLYVP_FROM_EMAIL` domain or address must be
verified in SendGrid → Settings → Sender Authentication before emails will deliver.

### 3. Run

```bash
python digest.py
```

A run typically takes 3–8 minutes depending on the number of new articles (each
scoring call is a separate Claude API request).

---

## Automate with cron

To receive a digest every weekday morning at 7 AM:

```bash
crontab -e
```

Add:
```
0 7 * * 1-5 /path/to/FeedlyVP/.venv/bin/python /path/to/FeedlyVP/digest.py >> /path/to/FeedlyVP/cron.log 2>&1
```

Or with a `.env` file loaded via a wrapper script:

```bash
#!/usr/bin/env bash
set -a
source /path/to/FeedlyVP/.env
set +a
exec /path/to/FeedlyVP/.venv/bin/python /path/to/FeedlyVP/digest.py "$@"
```

---

## Customizing feeds

Edit `feeds.yaml`:

- **`weight`** — multiplier applied to Claude's raw 1–10 score for ranking.
  Higher weight = source articles rank higher when scores are equal.
- **`max_articles`** — cap on how many recent entries to pull per feed per run.
- Add or remove feeds freely; `seen_urls.json` tracks by URL so changes take
  effect immediately on the next run.

---

## Scoring logic

1. Each new article title + excerpt is sent to `claude-sonnet-4-20250514` with
   the enterprise relevance rubric.
2. Claude returns `{"score": 1–10, "summary": "...", "why": "..."}`.
3. Articles with `score >= 7` qualify; they are ranked by `score × weight`.
4. Top 15 qualifying articles are included in the digest.
5. A final Claude call synthesizes a 3-sentence "Today's Big Picture".

---

## Resetting seen URLs

To re-process all articles (e.g., after a long pause):

```bash
echo "[]" > seen_urls.json
```

---

## Dependencies

| Package | Version | Use |
|---|---|---|
| feedparser | 6.0.11 | RSS/Atom parsing |
| anthropic | 0.49.0 | Claude API |
| sendgrid | 6.11.0 | Email delivery |
| PyYAML | 6.0.2 | feeds.yaml config |
| requests | 2.32.3 | HTTP (feedparser dep) |
