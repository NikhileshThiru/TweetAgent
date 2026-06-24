"""Vercel Python serverless function — generates tweet drafts on demand.

Two callers, one endpoint (POST /api/generate):
  • Supabase Cron  → header `x-cron-secret: <CRON_SECRET>`, body {"mode":"single"}.
    Trickle; generates only at the Atlanta hours in ACTIVE_HOURS (every 2h, 10am–10pm).
  • "Generate now" → header `Authorization: Bearer <supabase access token>`,
    body {"mode":"spread"}. Verified to belong to OWNER_EMAIL. Ignores the window.

Self-contained on purpose (sources + prompts + generation + handler in one file)
so Vercel's Python runtime bundles it without multi-file import gotchas.

Local testing (no deploy needed):
    python generate.py --dry-run                 # single, prints, writes nothing
    python generate.py --dry-run --mode spread   # one story per topic

Server env vars (set in Vercel → Project → Settings → Environment Variables):
    GEMINI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY,
    SUPABASE_ANON_KEY, OWNER_EMAIL, CRON_SECRET
"""

import os
import re
import sys
import json
import time
import random
import difflib
import calendar
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler

import requests
import feedparser

# ===========================================================================
# Config
# ===========================================================================
ENABLED_BUCKETS = ["ai", "tech", "markets", "running"]
KEEP_PER_STORY = {"single": 1, "spread": 2}

ACTIVE_TZ = "America/New_York"
# Scheduled runs generate only at these Atlanta hours (every 2h, 10am–10pm).
ACTIVE_HOURS = [10, 12, 14, 16, 18, 20, 22]

PROVIDER = "gemini"
GEMINI_MODEL = "gemini-2.5-flash"
GROQ_MODEL = "llama-4-scout-17b-16e-instruct"

MAX_TWEET_CHARS = 280
RECENT_FETCH_LIMIT = 60
RECENT_AVOID_IN_PROMPT = 25
DUP_SIMILARITY_THRESHOLD = 0.82
TASTE_LIKED_LIMIT = 10
TASTE_DISLIKED_LIMIT = 8
TASTE_NOTES_LIMIT = 8

LLM_TIMEOUT = 45
LLM_RETRIES = 2
RETRY_STATUSES = {429, 500, 502, 503, 504}

UA = "TweetDraftBot/1.0 (personal project)"
HTTP_TIMEOUT = 12
MAX_PER_SOURCE = 15
# Drop anything older than this — a news reaction has to be fresh, not week-old.
MAX_AGE_HOURS = 30

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF\U0000FE00-\U0000FE0F\U0001F1E6-\U0001F1FF]",
    flags=re.UNICODE,
)


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


# ===========================================================================
# Sources (HN + Google News + RSS; all fail-soft)
# ===========================================================================
AI_KEYWORDS = [
    "ai", "llm", "llms", "agi", "gpt", "chatgpt", "openai", "anthropic", "claude",
    "gemini", "llama", "mistral", "deepmind", "hugging face", "machine learning",
    "language model", "neural network", "neural", "transformer", "diffusion",
    "agent", "agents", "inference", "fine-tuning", "rag", "embedding", "embeddings",
]
MARKET_KEYWORDS = [
    "stock", "stocks", "market", "markets", "fed", "federal reserve", "earnings",
    "nasdaq", "s&p", "dow", "bond", "bonds", "yield", "yields", "inflation", "ipo",
    "valuation", "funding", "crypto", "bitcoin", "ethereum", "interest rate",
    "interest rates", "recession", "etf", "dividend", "wall street",
]


def _kw_regex(words):
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)


_AI_RE = _kw_regex(AI_KEYWORDS)
_MARKET_RE = _kw_regex(MARKET_KEYWORDS)
_GN = "https://news.google.com/rss/search"

RSS_FEEDS = {
    "markets": [
        ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
        ("CNBC Markets", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
        ("Google News", _GN + "?q=(stock+market+OR+earnings+OR+%22Federal+Reserve%22+OR+IPO)+when:2d&hl=en-US&gl=US&ceid=US:en"),
    ],
    "ai": [
        ("Google News", _GN + "?q=(%22artificial+intelligence%22+OR+LLM+OR+OpenAI+OR+Anthropic+OR+%22AI+model%22)+when:1d&hl=en-US&gl=US&ceid=US:en"),
        ("r/MachineLearning", "https://www.reddit.com/r/MachineLearning/hot/.rss?limit=15"),
        ("r/LocalLLaMA", "https://www.reddit.com/r/LocalLLaMA/hot/.rss?limit=15"),
    ],
    "tech": [
        ("r/startups", "https://www.reddit.com/r/startups/hot/.rss?limit=15"),
    ],
    "running": [
        ("Google News", _GN + "?q=(%22marathon+training%22+OR+%22half+marathon%22+OR+ultramarathon+OR+triathlon+OR+%22running+race%22+OR+%22marathon+world+record%22)+when:2d&hl=en-US&gl=US&ceid=US:en"),
        ("r/running", "https://www.reddit.com/r/running/hot/.rss?limit=15"),
        ("r/AdvancedRunning", "https://www.reddit.com/r/AdvancedRunning/hot/.rss?limit=15"),
    ],
}


def _route_hn(title):
    if _AI_RE.search(title):
        return "ai"
    if _MARKET_RE.search(title):
        return "markets"
    return "tech"


def _entry_ts(entry):
    """Best-effort publish time (epoch UTC) from an RSS/Atom entry, else None."""
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return calendar.timegm(t)
            except Exception:
                pass
    return None


def _fetch_hackernews():
    items = []
    try:
        r = requests.get(
            "https://hn.algolia.com/api/v1/search",
            params={"tags": "front_page", "hitsPerPage": 40},
            headers={"User-Agent": UA}, timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        cutoff = time.time() - MAX_AGE_HOURS * 3600
        for hit in r.json().get("hits", []):
            title = (hit.get("title") or "").strip()
            ts = hit.get("created_at_i", 0)
            if not title or ts < cutoff:
                continue
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            items.append({"title": title, "url": url, "source": "Hacker News",
                          "bucket": _route_hn(title), "ts": ts})
        log(f"  [hackernews] {len(items)} items")
    except Exception as e:
        log(f"  [hackernews] SKIPPED ({type(e).__name__}: {e})")
    return items


def _fetch_rss(source, url, bucket):
    items = []
    cutoff = time.time() - MAX_AGE_HOURS * 3600
    dropped_old = 0
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        for entry in feed.entries:
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            ts = _entry_ts(entry)
            # Drop anything we can date that's older than the cutoff. Entries with
            # no parseable date are kept (rare for news feeds) but sort last.
            if ts is not None and ts < cutoff:
                dropped_old += 1
                continue
            items.append({"title": title, "url": (entry.get("link") or "").strip(),
                          "source": source, "bucket": bucket, "ts": ts or 0})
            if len(items) >= MAX_PER_SOURCE:
                break
        suffix = f" ({dropped_old} too old)" if dropped_old else ""
        log(f"  [{source}] {len(items)} items{suffix}")
    except Exception as e:
        log(f"  [{source}] SKIPPED ({type(e).__name__}: {e})")
    return items


def gather_sources(enabled_buckets):
    log("Gathering source items...")
    by_bucket = {b: [] for b in enabled_buckets}
    for it in _fetch_hackernews():
        if it["bucket"] in by_bucket:
            by_bucket[it["bucket"]].append(it)
    for bucket in enabled_buckets:
        for source, url in RSS_FEEDS.get(bucket, []):
            for it in _fetch_rss(source, url, bucket):
                by_bucket[bucket].append(it)
    # Freshest first, so selection always prefers the most recent story.
    for items in by_bucket.values():
        items.sort(key=lambda it: it.get("ts", 0), reverse=True)
    log(f"Gathered {sum(len(v) for v in by_bucket.values())} items across {len(by_bucket)} buckets "
        f"(max age {MAX_AGE_HOURS}h).")
    return by_bucket


# ===========================================================================
# Prompt (the voice)
# ===========================================================================
# The author's persona drives the voice. A generic default lives here; set your
# real bio via the AUTHOR_PROFILE env var so it stays out of the (public) repo.
DEFAULT_PROFILE = """\
- A builder in their early 20s who ships side projects constantly.
- Deep into AI/LLMs, startups, and markets/investing; also an endurance runner.
- Lives on Hacker News; follows tech/startup/AI news closely.
So their reactions come from someone who actually ships and uses these tools \
daily and has skin in the game — not a pundit, journalist, or thought leader."""


SYSTEM_PROMPT_TEMPLATE = """\
You write tweet drafts for one specific person. Each draft is a reaction to a \
real, current news item that you'll be given. The person reviews every draft and \
posts only the ones they like — so each draft must be something they'd be glad to \
have on their public timeline.

WHO THEY ARE (for voice and point of view — not for bragging about):
__PROFILE__

VOICE:
- Casual, direct, conversational. Like texting a smart friend.
- Genuinely opinionated. You have a real take: agree, disagree, call out what \
everyone's missing, or say why it actually matters. Never a neutral summary.
- Builder-brained: practical, grounded in how things really work, allergic to hype.
- Short and punchy. Usually one or two sentences.
- Sounds like a sharp 18-year-old who builds things — NOT a brand or a thought leader.

HARD RULES (breaking any of these makes the draft unusable):
- No emojis. Ever.
- No hashtags.
- No links, and no "check this out" / "read more" / "thread below".
- Plain text, a single standalone tweet. No threads, no numbering, no prefixes \
  like "Hot take:" or "PSA:".
- 280 characters max. Aim under 230.
- Never fabricate facts, numbers, names, or quotes. React only to what the given \
  item actually says. If a detail isn't in the item, stay general rather than \
  inventing specifics. It is far better to be vague than wrong.
- Profanity is allowed when it genuinely fits the voice, but never forced and \
  never as filler.

STYLE — sharp but defensible:
- Clear angle, never reckless. Nothing they'd have to walk back or apologize for, \
  and never punching down at a specific named person.
- No engagement bait: no "Unpopular opinion:", "Hot take:", "Let that sink in", \
  and no rhetorical-question openers.
- Avoid AI/LinkedIn tells: "It's not just X, it's Y", "game-changer", "the real \
  question is", "this changes everything", listicles, fake profundity, and \
  em-dash windups.
- Be specific to THIS story. If the take could be pasted under any headline, \
  it's a bad take — rewrite it.

You'll get one news item plus a list of recent drafts to avoid repeating (same \
angle, opening, or topic). Produce three genuinely different angles on the item.

Output ONLY valid JSON matching this shape, nothing else:
{"tweets": ["<tweet 1>", "<tweet 2>", "<tweet 3>"]}
"""


def build_system_prompt():
    """System prompt with the author persona injected from AUTHOR_PROFILE (or a
    generic default), so no personal bio is hardcoded in the repo."""
    profile = os.environ.get("AUTHOR_PROFILE", "").strip() or DEFAULT_PROFILE
    return SYSTEM_PROMPT_TEMPLATE.replace("__PROFILE__", profile)


def _taste_block(taste):
    taste = taste or {}
    parts = []
    if taste.get("steering"):
        parts.append("STANDING INSTRUCTIONS — always follow these, no exceptions:\n"
                     + taste["steering"])
    if taste.get("notes"):
        parts.append("MY RECENT FEEDBACK — follow these instructions:\n"
                     + "\n".join(f"- {t}" for t in taste["notes"]))
    if taste.get("liked"):
        parts.append("TWEETS I APPROVED — match this taste, energy, and structure:\n"
                     + "\n".join(f"- {t}" for t in taste["liked"]))
    if taste.get("disliked"):
        parts.append("TWEETS I TRASHED — do NOT write anything like these:\n"
                     + "\n".join(f"- {t}" for t in taste["disliked"]))
    return ("\n\n".join(parts) + "\n\n") if parts else ""


def build_user_prompt(item, recent_texts, taste=None):
    avoid_block = "\n".join(f"- {t}" for t in recent_texts) or "(none yet)"
    return f"""\
{_taste_block(taste)}NEWS ITEM TO REACT TO
Topic area: {item['bucket']}
Source: {item['source']}
Headline: {item['title']}

Write 3 distinct standalone tweet takes reacting to this specific item. Different \
angles from each other, and different from everything in the avoid-list below.

RECENT DRAFTS — do NOT repeat these angles, openings, or topics:
{avoid_block}

Return only the JSON object: {{"tweets": ["...", "...", "..."]}}"""


# ===========================================================================
# LLM (Gemini REST; retries; recoverable per story)
# ===========================================================================
class LLMError(Exception):
    pass


def _need(name):
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"missing required env var {name}")
    return val


def _post_json(url, headers, body, label):
    last = "unknown error"
    for attempt in range(LLM_RETRIES + 1):
        try:
            r = requests.post(url, headers=headers, data=json.dumps(body), timeout=LLM_TIMEOUT)
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        else:
            if r.ok:
                return r.json()
            if r.status_code not in RETRY_STATUSES:
                raise LLMError(f"{label} {r.status_code}: {r.text[:400]}")
            last = f"{r.status_code}: {r.text[:200]}"
        if attempt < LLM_RETRIES:
            log(f"  {label}: {last} — retry {attempt + 1}/{LLM_RETRIES}")
            time.sleep(2 * (attempt + 1))
    raise LLMError(f"{label} failed after {LLM_RETRIES + 1} attempts ({last})")


def call_llm(system_prompt, user_prompt):
    if PROVIDER == "gemini":
        return _call_gemini(system_prompt, user_prompt)
    if PROVIDER == "groq":
        return _call_groq(system_prompt, user_prompt)
    raise RuntimeError(f"unknown PROVIDER {PROVIDER!r}")


def _call_gemini(system_prompt, user_prompt):
    api_key = _need("GEMINI_API_KEY")
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 1.0,
            "thinkingConfig": {"thinkingBudget": 0},
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {"tweets": {"type": "ARRAY", "items": {"type": "STRING"}}},
                "required": ["tweets"],
            },
        },
    }
    payload = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        {"x-goog-api-key": api_key, "Content-Type": "application/json"}, body, "Gemini",
    )
    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text).get("tweets", [])
    except Exception as e:
        raise LLMError(f"Gemini parse error ({type(e).__name__}: {e})")


def _call_groq(system_prompt, user_prompt):
    api_key = _need("GROQ_API_KEY")
    payload = _post_json(
        "https://api.groq.com/openai/v1/chat/completions",
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        {"model": GROQ_MODEL, "temperature": 1.0, "response_format": {"type": "json_object"},
         "messages": [{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}]}, "Groq",
    )
    try:
        return json.loads(payload["choices"][0]["message"]["content"]).get("tweets", [])
    except Exception as e:
        raise LLMError(f"Groq parse error ({type(e).__name__}: {e})")


# ===========================================================================
# Supabase (PostgREST over plain HTTP)
# ===========================================================================
def _sb_headers(service_key):
    return {"apikey": service_key, "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json"}


def _sb_get(url, service_key, params):
    r = requests.get(f"{url}/rest/v1/tweet_drafts", headers=_sb_headers(service_key),
                     params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_recent_drafts(url, service_key):
    try:
        return _sb_get(url, service_key, {"select": "text,topic_hint",
                       "order": "created_at.desc", "limit": str(RECENT_FETCH_LIMIT)})
    except Exception as e:
        log(f"WARN: could not fetch recent drafts ({type(e).__name__}: {e})")
        return []


def fetch_taste(url, service_key):
    taste = {"liked": [], "disliked": [], "notes": []}
    try:
        liked = _sb_get(url, service_key, {"select": "text", "status": "in.(approved,posted)",
                        "order": "updated_at.desc", "limit": str(TASTE_LIKED_LIMIT)})
        taste["liked"] = [d["text"] for d in liked if d.get("text")]
        disliked = _sb_get(url, service_key, {"select": "text", "status": "eq.trashed",
                           "order": "updated_at.desc", "limit": str(TASTE_DISLIKED_LIMIT)})
        taste["disliked"] = [d["text"] for d in disliked if d.get("text")]
        notes = _sb_get(url, service_key, {"select": "feedback", "feedback": "not.is.null",
                        "order": "updated_at.desc", "limit": str(TASTE_NOTES_LIMIT)})
        taste["notes"] = [d["feedback"] for d in notes if d.get("feedback")]
    except Exception as e:
        log(f"WARN: could not fetch taste signals ({type(e).__name__}: {e})")
    return taste


def fetch_steering(url, service_key):
    """Persistent standing instructions from app_settings (single row id=1).
    Fails soft to '' if the table doesn't exist yet (migration not run)."""
    try:
        rows = _sb_get(url, service_key, {"select": "steering", "id": "eq.1", "limit": "1"})
        if rows and rows[0].get("steering"):
            return rows[0]["steering"].strip()
    except Exception as e:
        log(f"WARN: could not fetch steering ({type(e).__name__}: {e})")
    return ""


def insert_drafts(url, service_key, rows):
    r = requests.post(f"{url}/rest/v1/tweet_drafts",
                      headers={**_sb_headers(service_key), "Prefer": "return=representation"},
                      data=json.dumps(rows), timeout=20)
    if not r.ok:
        raise RuntimeError(f"Supabase insert failed [{r.status_code}]: {r.text[:300]}")
    return r.json()


# ===========================================================================
# Selection + validation
# ===========================================================================
def within_active_window():
    return datetime.now(ZoneInfo(ACTIVE_TZ)).hour in ACTIVE_HOURS


def normalize(s):
    return re.sub(r"\s+", " ", s.lower()).strip()


def already_used(item, recent):
    hints = " ".join((d.get("topic_hint") or "") for d in recent)
    if item["url"] and item["url"] in hints:
        return True
    norm_title = normalize(item["title"])
    return bool(norm_title) and norm_title in normalize(hints)


def _fresh_items(items, recent, exclude=()):
    used_urls = {p["url"] for p in exclude if p.get("url")}
    return [it for it in items
            if not already_used(it, recent) and not (it["url"] and it["url"] in used_urls)]


def choose_item(by_bucket, recent, shuffle=False):
    order = list(ENABLED_BUCKETS)
    start = datetime.now(timezone.utc).hour % len(order)
    rotated = order[start:] + order[:start]
    if shuffle:
        random.shuffle(rotated)
    for bucket in rotated:
        fresh = _fresh_items(by_bucket.get(bucket, []), recent)
        if fresh:
            return random.choice(fresh) if shuffle else fresh[0]
    return None


def choose_spread(by_bucket, recent, shuffle=False):
    picked = []
    for bucket in ENABLED_BUCKETS:
        fresh = _fresh_items(by_bucket.get(bucket, []), recent, exclude=picked)
        if fresh:
            picked.append(random.choice(fresh) if shuffle else fresh[0])
    return picked


def clean_candidate(text):
    t = text.strip()
    if len(t) >= 2 and t[0] in "\"'" and t[-1] in "\"'":
        t = t[1:-1].strip()
    return t


def is_valid(text, avoid_texts):
    if not (15 <= len(text) <= MAX_TWEET_CHARS):
        return False
    if EMOJI_RE.search(text):
        return False
    norm = normalize(text)
    for prev in avoid_texts:
        if difflib.SequenceMatcher(None, norm, normalize(prev)).ratio() >= DUP_SIMILARITY_THRESHOLD:
            return False
    return True


def pick_valid(candidates, avoid_texts, keep):
    kept = []
    for raw in candidates:
        cleaned = clean_candidate(raw)
        if is_valid(cleaned, avoid_texts + kept):
            kept.append(cleaned)
        if len(kept) >= keep:
            break
    return kept


# ===========================================================================
# Generation pipeline
# ===========================================================================
def run_generation(mode="single", respect_window=False, dry_run=False):
    if mode not in KEEP_PER_STORY:
        mode = "single"
    log(f"run_generation(mode={mode}, respect_window={respect_window}, dry_run={dry_run})")

    if respect_window and not within_active_window():
        now = datetime.now(ZoneInfo(ACTIVE_TZ))
        log(f"Atlanta time {now:%H:%M} outside active window — skipping cleanly.")
        return {"status": "skipped", "reason": "outside active window", "inserted": 0}

    if dry_run:
        supabase_url = os.environ.get("SUPABASE_URL", "").strip()
        service_key = ""
        recent = []
        taste = {"liked": [], "disliked": [], "notes": [], "steering": ""}
    else:
        supabase_url = _need("SUPABASE_URL")
        service_key = _need("SUPABASE_SERVICE_KEY")
        recent = fetch_recent_drafts(supabase_url, service_key)
        taste = fetch_taste(supabase_url, service_key)
        taste["steering"] = fetch_steering(supabase_url, service_key)
    recent_texts = [d["text"] for d in recent if d.get("text")]

    by_bucket = gather_sources(ENABLED_BUCKETS)
    stories = choose_spread(by_bucket, recent, shuffle=dry_run) if mode == "spread" else (
        [i] if (i := choose_item(by_bucket, recent, shuffle=dry_run)) else []
    )
    if not stories:
        log("No fresh unused items found. Skipping cleanly.")
        return {"status": "ok", "mode": mode, "inserted": 0, "reason": "no fresh items"}

    keep = KEEP_PER_STORY[mode]
    model_tag = GEMINI_MODEL if PROVIDER == "gemini" else GROQ_MODEL
    sys_prompt = build_system_prompt()
    run_avoid = list(recent_texts)
    rows, errors = [], 0

    for item in stories:
        log(f"  [{item['bucket']}] {item['source']}: {item['title']!r}")
        user_prompt = build_user_prompt(item, recent_texts[:RECENT_AVOID_IN_PROMPT], taste)
        try:
            candidates = call_llm(sys_prompt, user_prompt)
        except LLMError as e:
            errors += 1
            log(f"  skipped — {e}")
            continue
        prior_avoid = list(run_avoid)
        kept = pick_valid(candidates, prior_avoid, keep)
        run_avoid.extend(kept)

        if dry_run:
            print("\n" + "=" * 66)
            print(f"REACTING TO  [{item['bucket']}]  {item['source']}: {item['title']}")
            print("=" * 66)
            for n, raw in enumerate(candidates, 1):
                c = clean_candidate(raw)
                verdict = "OK" if is_valid(c, prior_avoid) else "rejected (len/emoji/dup)"
                print(f"\n[{n}] {len(c)} chars — {verdict}\n{c}")
            print(f"\n--> would save {len(kept)}: " + (" || ".join(kept) if kept else "(none)"))
            continue

        topic_hint = f"{item['source']}: {item['title']} | {item['url']}"
        for text in kept:
            rows.append({"text": text, "topic_hint": topic_hint,
                         "model": model_tag, "status": "pending"})

    if dry_run:
        return {"status": "ok", "mode": mode, "dry_run": True, "stories": len(stories)}
    if not rows:
        if errors:
            raise RuntimeError(f"{errors} generation attempt(s) errored; nothing produced.")
        return {"status": "ok", "mode": mode, "inserted": 0, "reason": "nothing passed validation"}
    insert_drafts(supabase_url, service_key, rows)
    log(f"Inserted {len(rows)} draft(s).")
    return {"status": "ok", "mode": mode, "inserted": len(rows), "errors": errors}


# ===========================================================================
# Auth
# ===========================================================================
def _is_cron(headers):
    secret = os.environ.get("CRON_SECRET", "").strip()
    provided = (headers.get("x-cron-secret") or "").strip()
    return bool(secret) and provided == secret


def _is_owner(headers):
    auth = headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return False
    token = auth[7:].strip()
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    anon = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    owner = os.environ.get("OWNER_EMAIL", "").strip().lower()
    if not (url and anon and owner and token):
        return False
    try:
        r = requests.get(f"{url}/auth/v1/user",
                         headers={"apikey": anon, "Authorization": f"Bearer {token}"}, timeout=10)
        return r.ok and (r.json().get("email") or "").lower() == owner
    except Exception:
        return False


# ===========================================================================
# Vercel HTTP handler
# ===========================================================================
class handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if _is_cron(self.headers):
            caller, default_mode = "cron", "single"
        elif _is_owner(self.headers):
            caller, default_mode = "owner", "spread"
        else:
            return self._send(401, {"error": "unauthorized"})

        length = int(self.headers.get("content-length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except Exception:
            body = {}
        mode = (body.get("mode") or default_mode).lower()

        try:
            result = run_generation(mode=mode, respect_window=(caller == "cron"), dry_run=False)
            self._send(200, result)
        except Exception as e:
            log(f"ERROR: {type(e).__name__}: {e}")
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


# ===========================================================================
# Local CLI (testing only; not used by Vercel)
# ===========================================================================
if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    cli_mode = "single"
    for i, a in enumerate(sys.argv):
        if a == "--mode" and i + 1 < len(sys.argv):
            cli_mode = sys.argv[i + 1]
        elif a.startswith("--mode="):
            cli_mode = a.split("=", 1)[1]
    print(run_generation(mode=cli_mode, respect_window=False, dry_run=dry))
