"""Card text: a templated summary for every animal, plus hand-written stories for top animals.

Stories live in stories/<safe id>.json and are written from the fact sheets this step exports to
stories/factsheets/. A story is a list of sentences. Each sentence's text may reference facts only
through placeholders like {days_tracked}; the web app fills them in from the facts table, so a
story can never state a number the data doesn't contain. The checks below enforce that:

  - every placeholder names a fact this animal has, and is listed in the sentence's `facts`
  - no digits appear outside placeholders (numbers and dates must come from facts)
  - `context` ids exist in stories/species_context.yaml for this animal's species
  - the story was written for the current data (`based_on.last_date` matches), else it is stale

A story that fails is not published; the card falls back to the summary.
"""
import json
import re

import yaml

from .util import STORIES, log, replace_table

TOP_N = 50
PER_SPECIES = 3
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def safe_id(animal_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", animal_id)


def summary(f: dict, common: str) -> str:
    desc = " ".join(x for x in (f.get("stage"), f.get("sex"), common.lower()) if x)
    s = ("An " if desc[0] in "aeiou" else "A ") + desc
    s += f", tagged at {f['release_site']}." if f.get("release_site") else "."
    s += (f" Tracked for {f['days_tracked']}, from {f['first_date']} to {f['last_date']},"
          f" reaching {f['max_displacement_km']} from where tracking began.")
    if f.get("deepest_dive_m"):
        s += f" Deepest recorded dive: {f['deepest_dive_m']}."
    return s


def check_story(story: dict, facts: dict, context_ids: set, last_date: str) -> list[str]:
    errors = []
    if story.get("based_on", {}).get("last_date") != last_date:
        errors.append(f"stale: written for last_date {story.get('based_on', {}).get('last_date')}, data has {last_date}")
    for i, s in enumerate(story.get("sentences", [])):
        text = s.get("text", "")
        used = set(PLACEHOLDER.findall(text))
        for p in used - set(facts):
            errors.append(f"sentence {i}: unknown fact {{{p}}}")
        for p in used - set(s.get("facts", [])):
            errors.append(f"sentence {i}: placeholder {{{p}}} not listed in facts")
        if re.search(r"\d", PLACEHOLDER.sub("", text)):
            errors.append(f"sentence {i}: digits outside placeholders")
        for c in s.get("context", []):
            if c not in context_ids:
                errors.append(f"sentence {i}: unknown context id {c}")
        if not s.get("facts") and not s.get("context") and not s.get("framing"):
            errors.append(f"sentence {i}: no facts or context cited (set framing: true for pure narration)")
    return errors


def build(con) -> None:
    animals = con.execute("""
        SELECT a.animal_id, s.common_name, s.scientific_name, st.score, st.noisy, s.aphia_id
        FROM published_animals a JOIN species s USING (aphia_id) JOIN animal_stats st USING (animal_id)
        ORDER BY st.score DESC
    """).fetchall()
    facts: dict[str, dict] = {}
    for animal_id, fid, display in con.execute("SELECT animal_id, fact_id, display FROM facts").fetchall():
        facts.setdefault(animal_id, {})[fid] = display
    raw_facts: dict[str, dict] = {}
    for animal_id, fid, value in con.execute("SELECT animal_id, fact_id, value FROM facts").fetchall():
        raw_facts.setdefault(animal_id, {})[fid] = json.loads(value)

    ctx_path = STORIES / "species_context.yaml"
    context = yaml.safe_load(ctx_path.read_text()) if ctx_path.exists() else {}
    context = context or {}

    # Pick story candidates: best scores, at most PER_SPECIES per species, no noisy tracks.
    per_species: dict[int, int] = {}
    candidates = []
    for animal_id, common, sci, score, noisy, aphia in animals:
        if noisy or per_species.get(aphia, 0) >= PER_SPECIES:
            continue
        per_species[aphia] = per_species.get(aphia, 0) + 1
        candidates.append((animal_id, common, sci))
        if len(candidates) >= TOP_N:
            break

    sheets = STORIES / "factsheets"
    sheets.mkdir(parents=True, exist_ok=True)
    for old in sheets.glob("*.json"):
        old.unlink()
    for animal_id, common, sci in candidates:
        (sheets / f"{safe_id(animal_id)}.json").write_text(json.dumps({
            "animal_id": animal_id, "species": common, "scientific_name": sci,
            "based_on": {"last_date": raw_facts[animal_id]["last_date"]},
            "facts": facts[animal_id],
            "species_context": context.get(sci, []),
        }, indent=1, ensure_ascii=False))

    rows, published, failed = [], 0, []
    common_of = {a[0]: a[1] for a in animals}
    sci_of = {a[0]: a[2] for a in animals}
    for animal_id, _, _, _, _, _ in animals:
        story_json = None
        path = STORIES / f"{safe_id(animal_id)}.json"
        if path.exists():
            story = json.loads(path.read_text())
            ctx_ids = {c["id"] for c in context.get(sci_of[animal_id], [])}
            errors = check_story(story, facts[animal_id], ctx_ids, raw_facts[animal_id]["last_date"])
            if errors:
                failed.append((animal_id, errors))
            else:
                ctx_text = {c["id"]: c for c in context.get(sci_of[animal_id], [])}
                story["context_text"] = {c: ctx_text[c] for s in story["sentences"] for c in s.get("context", [])}
                story_json = json.dumps(story, ensure_ascii=False)
                published += 1
        rows.append((animal_id, summary(facts[animal_id], common_of[animal_id]), story_json))
    replace_table(con, "card_text", ["animal_id", "summary", "story"], rows)
    for animal_id, errors in failed:
        log(f"  story rejected for {animal_id}: " + "; ".join(errors))
    log(f"stories: {len(candidates)} fact sheets written; {published} stories published; {len(failed)} rejected")
