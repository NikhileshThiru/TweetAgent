"""Unit tests for the pure generation logic — no network, no API keys.

    pip install -r ui/requirements.txt pytest
    pytest
"""
import importlib.util
from pathlib import Path

_MOD = Path(__file__).resolve().parent.parent / "ui" / "api" / "generate.py"
_spec = importlib.util.spec_from_file_location("generate", _MOD)
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)


def _item(title, url, bucket="tech"):
    return {"title": title, "url": url, "source": "Test", "bucket": bucket}


# --- candidate validation --------------------------------------------------
def test_is_valid_accepts_normal_tweet():
    assert g.is_valid("a perfectly normal short take about the news today", [])


def test_is_valid_rejects_too_short():
    assert not g.is_valid("too short", [])


def test_is_valid_rejects_over_280():
    assert not g.is_valid("x" * 281, [])


def test_is_valid_rejects_emoji():
    assert not g.is_valid("great take about shipping software today \U0001F680", [])


def test_is_valid_rejects_near_duplicate():
    prev = "shipping beats planning every single time, no exceptions"
    assert not g.is_valid("shipping beats planning every single time, no exception", [prev])


def test_clean_candidate_strips_wrapping_quotes():
    assert g.clean_candidate('"a quoted tweet"') == "a quoted tweet"
    assert g.clean_candidate("no quotes here") == "no quotes here"


def test_pick_valid_keeps_n_and_drops_dupes_and_short():
    cands = [
        "a genuinely fine standalone take about the news",
        "a genuinely fine standalone take about the news!",   # near-dup of #1
        "x",                                                  # too short
        "a second clearly different and acceptable take here",
    ]
    kept = g.pick_valid(cands, [], keep=2)
    assert len(kept) == 2
    assert kept[0] != kept[1]


# --- Hacker News topic routing (word-boundary, not substring) --------------
def test_route_hn_ai():
    assert g._route_hn("OpenAI ships a new LLM") == "ai"


def test_route_hn_markets():
    assert g._route_hn("The S&P 500 hits a record on earnings") == "markets"


def test_route_hn_tech_default():
    assert g._route_hn("A new Rust web framework appears") == "tech"


def test_route_hn_word_boundary_not_substring():
    # "ai" lives inside "daily" but must not route this to the AI bucket.
    assert g._route_hn("My daily routine for staying sane") == "tech"


# --- image relevance filter ------------------------------------------------
def test_image_keeps_real_hero():
    assert g._is_relevant_image({"url": "https://cdn.x/hero.jpg", "width": 1200, "height": 630})


def test_image_rejects_avatar_by_url():
    assert not g._is_relevant_image(
        {"url": "https://avatars.githubusercontent.com/u/1", "width": 460, "height": 460}
    )


def test_image_rejects_logo_by_url():
    assert not g._is_relevant_image({"url": "https://x.com/logo.png", "width": 800, "height": 400})


def test_image_rejects_tiny():
    assert not g._is_relevant_image({"url": "https://x/photo.png", "width": 64, "height": 64})


def test_image_rejects_square():
    assert not g._is_relevant_image({"url": "https://x/photo.jpg", "width": 500, "height": 500})


def test_image_rejects_unknown_dimensions():
    assert not g._is_relevant_image({"url": "https://x/photo.jpg"})


# --- dedup / story selection -----------------------------------------------
def test_already_used_matches_url():
    recent = [{"topic_hint": "Test: Headline | https://x.com/a", "text": "t"}]
    assert g.already_used(_item("Other", "https://x.com/a"), recent)


def test_already_used_matches_title_across_sources():
    # Same story syndicated under a different URL must still count as used.
    recent = [{"topic_hint": "Test: Big AI News Today | https://x.com/a", "text": "t"}]
    assert g.already_used(_item("Big AI News Today", "https://y.com/b"), recent)


def test_already_used_false_when_new():
    recent = [{"topic_hint": "Test: Headline | https://x.com/a", "text": "t"}]
    assert not g.already_used(_item("Fresh", "https://x.com/b"), recent)


def test_choose_item_skips_used():
    by = {"ai": [], "markets": [], "running": [],
          "tech": [_item("A", "u-a"), _item("B", "u-b")]}
    recent = [{"topic_hint": "Test: A | u-a", "text": "t"}]
    assert g.choose_item(by, recent)["url"] == "u-b"


def test_choose_spread_one_per_bucket():
    by = {b: [_item(b.upper(), "u-" + b, b)] for b in g.ENABLED_BUCKETS}
    spread = g.choose_spread(by, [])
    assert {s["bucket"] for s in spread} == set(g.ENABLED_BUCKETS)


def test_rotation_cycles_every_bucket():
    # One run per 2h cron slot must give every bucket a turn at first pick.
    # (Regression: rotating by hour-of-day % n aliases on an every-2h schedule
    # and only ever landed on two of the four buckets.)
    n = len(g.ENABLED_BUCKETS)
    starts = {g._rotation_start(n, ts=slot * 7200) for slot in range(n)}
    assert starts == set(range(n))


# --- taste signals -----------------------------------------------------------
def test_taste_block_includes_all_sections():
    taste = {"steering": "no crypto", "notes": ["shorter"],
             "liked": ["good one"], "disliked": ["bad one"]}
    block = g._taste_block(taste)
    assert "STANDING INSTRUCTIONS" in block and "no crypto" in block
    assert "MY RECENT FEEDBACK" in block and "- shorter" in block
    assert "TWEETS I APPROVED" in block and "- good one" in block
    assert "TWEETS I TRASHED" in block and "- bad one" in block


def test_taste_block_empty_when_no_signals():
    assert g._taste_block({}) == ""
    assert g._taste_block(None) == ""


# --- prompt assembly -------------------------------------------------------
def test_system_prompt_uses_default_persona(monkeypatch):
    monkeypatch.delenv("AUTHOR_PROFILE", raising=False)
    p = g.build_system_prompt()
    assert "__PROFILE__" not in p
    assert "early 20s" in p  # the generic default persona


def test_system_prompt_env_override(monkeypatch):
    monkeypatch.setenv("AUTHOR_PROFILE", "- a backend engineer who loves databases")
    p = g.build_system_prompt()
    assert "backend engineer who loves databases" in p
    assert "early 20s" not in p


def test_user_prompt_injects_steering_and_keeps_json():
    item = _item("OpenAI ships X", "u", "ai")
    taste = {"steering": "Never tweet about crypto.", "liked": [], "disliked": [], "notes": []}
    p = g.build_user_prompt(item, ["a recent draft"], taste)
    assert "Never tweet about crypto" in p
    assert '{"tweets"' in p  # the JSON example survived (built via replace, not .format)
