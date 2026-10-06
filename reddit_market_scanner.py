#!/usr/bin/env python3
"""
Reddit Market Research Scanner — Wealthy and Fulfilled / Handled Wealth Program
=================================================================================

Fetches and filters candidate posts + comments from target subreddits into a
weekly "candidates" markdown file. This script does NOT do the 11-field
semantic extraction — that step needs judgment (is this actually the ICP?
is this language worth keeping?) and happens afterward, in a Claude chat,
using 4_weekly_extraction_prompt.md.

Requires an approved Reddit API app (see 1_reddit_api_setup.md — as of the
2025 Responsible Builder Policy, this needs manual approval, 2-4 weeks).
Until you have credentials, use 2_manual_fallback_workflow.md instead; it
produces the same kind of candidates file by hand/browser, feeding the same
downstream prompt and log.

Usage:
    pip3 install praw
    export REDDIT_CLIENT_ID="..."
    export REDDIT_CLIENT_SECRET="..."
    export REDDIT_USER_AGENT="wealthy-and-fulfilled-market-research/1.0 by u/yourname"
    python3 reddit_market_scanner.py
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import praw

# ---------------------------------------------------------------------------
# CONFIG — edit this section to tune targeting. Everything else below is
# plumbing you shouldn't need to touch.
# ---------------------------------------------------------------------------

# mode "all"     = pull every post above MIN_SCORE, no keyword filter
#                  (use for small, already-on-topic subs)
# mode "keyword" = only keep posts/comments matching KEYWORDS
#                  (use for large, mostly-off-topic subs)
#
# Note: r/financialindependence was deliberately left out — Portia asked to
# exclude FIRE communities, but also named this one, which is FIRE's home
# sub. Add it back here if that was meant to stay in.
SUBREDDITS = {
    "HENRYfinance": "all",
    "personalfinance": "keyword",
    "relationship_advice": "keyword",
    "Marriage": "keyword",
    "Parenting": "keyword",
}

MIN_SCORE = 5             # ignore low-engagement noise
LOOKBACK_DAYS = 7          # matches the weekly cadence
MAX_POSTS_PER_SUB = 100    # safety cap on API calls per run
TOP_COMMENTS_PER_POST = 8
MIN_COMMENT_SCORE = 2

KEYWORDS = [
    # fragmentation / coordination
    "financial advisor", "financial planner", "estate plan", "estate attorney",
    "college savings", "529", "spreadsheet", "net worth", "combined income",
    "household income", "dual income", "family cfo", "money fight",
    "who handles the money", "who manages our money", "financial mental load",
    "multiple accounts", "too many accounts", "tax planning", "cpa",
    "life insurance", "estate documents", "will and trust", "inheritance",
    "equity comp", "rsu", "stock options", "financially scattered",
    "financially behind", "financially overwhelmed", "second opinion",
    "gut check", "we make good money", "make six figures", "high income",
    "overwhelmed", "behind on",
]

STATE_FILE = Path(__file__).parent / "scanner_state.json"
OUTPUT_DIR = Path(__file__).parent / "candidates"

REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT",
    "wealthy-and-fulfilled-market-research/1.0 by u/YOUR_USERNAME",
)

# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen_ids": []}


def save_state(state: dict) -> None:
    # Cap growth so this file doesn't grow forever.
    state["seen_ids"] = state["seen_ids"][-5000:]
    STATE_FILE.write_text(json.dumps(state, indent=2))


def matches_keywords(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in KEYWORDS)


def prune_deleted(reddit: "praw.Reddit", state: dict) -> None:
    """
    Reddit's Data API terms require deleting stored content within 48 hours
    if the author deletes it or a mod removes it. We don't keep a permanent
    local archive (candidates files are meant to be triaged weekly and the
    surviving material re-typed into the signal log), but if you keep old
    candidates/*.md files around longer than that, spot-check them against
    this list, or just don't retain raw candidate files past a couple weeks.
    This function is a placeholder reminder, not an automated purge, because
    we don't re-fetch old IDs by default (would burn API calls for no
    research value). If you build automated retention later, wire actual
    deletion-checking here.
    """
    return


def scan() -> None:
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        raise SystemExit(
            "Missing Reddit API credentials. Set REDDIT_CLIENT_ID and "
            "REDDIT_CLIENT_SECRET as environment variables — see "
            "1_reddit_api_setup.md."
        )

    reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
    )
    reddit.read_only = True

    state = load_state()
    seen = set(state["seen_ids"])
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    OUTPUT_DIR.mkdir(exist_ok=True)
    run_date = datetime.now().strftime("%Y-%m-%d")
    out_path = OUTPUT_DIR / f"candidates_{run_date}.md"

    new_seen: list[str] = []
    lines = [
        f"# Reddit Market Research Candidates — {run_date}",
        "",
        "Unverified market-wide signal. Do not treat as confirmed ICP language "
        "until it shows up in a real client/discovery conversation — see the "
        "escalation rule in the Reddit Market Signal Log.",
        "",
    ]
    total_found = 0

    for sub_name, mode in SUBREDDITS.items():
        subreddit = reddit.subreddit(sub_name)
        sub_hits = []

        for post in subreddit.new(limit=MAX_POSTS_PER_SUB):
            created = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
            if created < cutoff:
                continue
            if post.id in seen:
                continue
            if post.score < MIN_SCORE:
                continue

            full_text = f"{post.title}\n{post.selftext or ''}"
            if mode == "keyword" and not matches_keywords(full_text):
                continue

            post.comments.replace_more(limit=0)
            top_comments = sorted(
                post.comments.list(), key=lambda c: c.score, reverse=True
            )[:TOP_COMMENTS_PER_POST]
            comment_blocks = [
                f"  > ({c.score} pts) {c.body.strip()}"
                for c in top_comments
                if c.score >= MIN_COMMENT_SCORE
            ]

            sub_hits.append(
                {
                    "id": post.id,
                    "title": post.title,
                    "score": post.score,
                    "num_comments": post.num_comments,
                    "created": created.strftime("%Y-%m-%d"),
                    "permalink": f"https://reddit.com{post.permalink}",
                    "selftext": (post.selftext or "").strip(),
                    "comments": comment_blocks,
                }
            )
            new_seen.append(post.id)

        if sub_hits:
            lines.append(f"## r/{sub_name} ({len(sub_hits)} candidates)")
            lines.append("")
            for hit in sub_hits:
                lines.append(f"### {hit['title']}")
                lines.append(
                    f"*{hit['created']} · score {hit['score']} · "
                    f"{hit['num_comments']} comments · {hit['permalink']}*"
                )
                lines.append("")
                if hit["selftext"]:
                    lines.append(hit["selftext"])
                    lines.append("")
                if hit["comments"]:
                    lines.append("**Top comments:**")
                    lines.extend(hit["comments"])
                lines.append("")
                lines.append("---")
                lines.append("")
            total_found += len(sub_hits)

    if total_found == 0:
        lines.append("No new candidates matched this week's filters.")

    out_path.write_text("\n".join(lines))
    state["seen_ids"] = list(seen) + new_seen
    save_state(state)
    prune_deleted(reddit, state)

    print(f"Found {total_found} new candidate thread(s) across {len(SUBREDDITS)} subreddits.")
    print(f"Written to: {out_path}")
    print("Next: paste this file into a Claude chat with 4_weekly_extraction_prompt.md")


if __name__ == "__main__":
    scan()
