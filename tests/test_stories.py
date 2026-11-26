"""The story checker must reject invented numbers, unknown facts and stale stories."""
from pipeline.stories import check_story

FACTS = {"days_tracked": "236 days", "first_date": "Feb 6, 2022", "release_site": "Iles Kerguelen"}
CTX = {"seal.dives"}


def story(*sentences, last_date="2022-09-30"):
    return {"based_on": {"last_date": last_date}, "sentences": list(sentences)}


def test_clean_story_passes():
    s = story({"text": "Tagged on {first_date} at {release_site}.", "facts": ["first_date", "release_site"]},
              {"text": "Elephant seals dive deep.", "context": ["seal.dives"]},
              {"text": "What happened next isn't recorded.", "framing": True})
    assert check_story(s, FACTS, CTX, "2022-09-30") == []


def test_invented_number_is_rejected():
    s = story({"text": "She swam 12,000 km.", "facts": []})
    assert any("digits" in e for e in check_story(s, FACTS, CTX, "2022-09-30"))


def test_invented_year_is_rejected():
    s = story({"text": "Back in 2019 she was seen.", "framing": True})
    assert any("digits" in e for e in check_story(s, FACTS, CTX, "2022-09-30"))


def test_unknown_fact_is_rejected():
    s = story({"text": "She dove {deepest_dive_m}.", "facts": ["deepest_dive_m"]})
    assert any("unknown fact" in e for e in check_story(s, FACTS, CTX, "2022-09-30"))


def test_unlisted_placeholder_is_rejected():
    s = story({"text": "Tracked for {days_tracked}.", "facts": []})
    assert any("not listed" in e for e in check_story(s, FACTS, CTX, "2022-09-30"))


def test_unknown_context_is_rejected():
    s = story({"text": "Seals sing.", "context": ["seal.songs"]})
    assert any("context" in e for e in check_story(s, FACTS, CTX, "2022-09-30"))


def test_uncited_sentence_is_rejected():
    s = story({"text": "She loved the cold water."})
    assert any("no facts" in e for e in check_story(s, FACTS, CTX, "2022-09-30"))


def test_stale_story_is_rejected():
    s = story({"text": "Tracked for {days_tracked}.", "facts": ["days_tracked"]}, last_date="2022-01-01")
    assert any("stale" in e for e in check_story(s, FACTS, CTX, "2022-09-30"))
