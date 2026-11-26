# Ocean Radar: implementation plan

*Answers `spec.md` §0. Builds on the spike in `spike/` (`build.py`, `template.html`). Numbers marked "spike" were measured on 2026-11-25.*

---

## 1. Architecture and stack

```
 sources (ERDDAP, WFS, CSV, APIs)
        │  fetch (cached raw, per source)
        ▼
 pipeline/  Python + DuckDB  ──►  data/warehouse/*.parquet   (normalized, QC'd, derived)
        │  policy (embargo, coarsening, license gate)
        │  emit
        ▼
 web/public/data/   index.json · chunks/YYYY.bin · animals/shard-NN.json · photos/*.webp
        │
 web/  Vite + TypeScript + MapLibre + deck.gl  ──►  static build  ──►  Cloudflare Pages
        ▲
 GitHub Actions: weekly cron → rebuild → deploy
```

| Layer | Choice | Why | Rejected |
|---|---|---|---|
| Pipeline language | **Python 3.12+, managed with `uv`** | Spike already in Python; best ecosystem for geodata (shapely, pyproj) and source formats | Node (weaker geo tooling); Rust (overkill for batch) |
| Intermediate store | **DuckDB over Parquet files** | Columnar, fast SQL on millions of points, single file per table, no server, diffs between runs are easy | Pandas only (memory, no SQL); Postgres/PostGIS (a server to run, cost); SQLite (slow for analytics) |
| Map rendering | **MapLibre GL JS + deck.gl (current major, via npm)** | Spike proved `TripsLayer` gives the animated-trail look; binary attributes scale to millions of points; open source, no token | Mapbox GL (token + paid at scale); Cesium (heavy, 3D-first); Leaflet (no WebGL, can't animate thousands of trails) |
| Front-end framework | **Vite + TypeScript + Preact** for panels/filters; the map is imperative | Small bundle; the hard part is the map, not the UI tree | React (heavier, no gain); Svelte (fine, but one more thing to learn); no framework (filters + card + search get messy) |
| Basemap | **CARTO Dark Matter** for MVP; **Protomaps PMTiles** (self-hosted low-zoom world basemap) as fallback | CARTO matched the spike look with zero setup; PMTiles removes the third-party dependency if CARTO's free-use terms don't fit | MapTiler/Mapbox (keys, quotas) |
| Hosting | **Cloudflare Pages** | Free, unmetered bandwidth, global CDN, deploy from CI | GitHub Pages (bandwidth soft limits, no headers control); Netlify/Vercel (bandwidth caps on free tier) |
| Large assets | Cloudflare R2 only if needed | Pages has a per-file size limit (25 MiB) and a file-count limit; chunking (below) should keep us under both | — |
| Scheduling | **GitHub Actions cron (weekly)** | Free for this size; logs and secrets built in | A server cron (cost, upkeep) |
| LLM | Anthropic API via the official `anthropic` Python SDK, **Message Batches** | Stories are offline, so batches (50% price) are a free win | Real-time calls (twice the cost, no benefit) |

**Repo layout**

```
pipeline/
  sources/atn_topp.py  atn_deployments.py  imos.py      # one module per source: fetch() + load() → raw frames
  species.py      # species table, name → AphiaID resolution, overrides.yaml
  normalize.py    # vocabularies, dedupe, unify to the warehouse schema
  qc.py           # speed filter, noisy flags, cadence
  policy.py       # embargo, coarsening, license gate (runs last, always)
  derive.py       # facts per animal
  photos.py       # species photos + cached thumbnails
  stories/        # factsheet.py, prompts/, generate.py, verify.py
  emit.py         # write web/public/data/*
  config/         # sources.yaml (license registry), species_overrides.yaml, sensitivity.yaml
tests/            # pytest: unit, policy, emitted-asset checks, story injection test
web/              # Vite app
data/             # gitignored: raw/ cache + warehouse/ parquet
```

---

## 2. Data model

All tables are Parquet in `data/warehouse/`, keyed by stable string IDs. `src` + native ID always round-trips to the source record.

**`sources`** (from `config/sources.yaml`, hand-maintained): `source_id`, `name`, `url`, `license`, `license_url`, `redistribution` (`allowed` / `non_commercial` / `permission_required`), `citation`, `fetched_at`, `data_version` (hash of raw files).

**`programs`**: `program_id`, `source_id`, `name` (e.g. "Stanford TOPP", IMOS campaign `ct164`), `institution`, `contact_url`.

**`species`**: `aphia_id` (WoRMS), `scientific_name`, `common_name`, `group` (`fish`/`shark`/`whale`/`seal`/`turtle`/`bird`), `iucn_status`, `sensitive` (bool, from `sensitivity.yaml`), `wiki_title`.

**`animals`**: `animal_id` (`<source>:<native id>`, e.g. `imos:ct164-172-BAT2-15`), `aphia_id`, `program_id`, `name` (if the program named it), `sex`, `life_stage_at_tagging`, `release_site`, `release_date`, `data_kind` (`telemetry` / `photo_id`), `same_as` (list of animal_ids judged to be the same individual across portals).
  An animal may carry several tags over time; for MVP one tag = one animal, and `deployments` holds the tag detail.

**`deployments`**: `deployment_id`, `animal_id`, `tag_type` (Argos / GPS / light-level / archival), `ptt`, `start`, `end`, `position_method` (`observed` / `ssm_modeled`), `embargo_until` (if the source says so).

**`positions`**: `animal_id`, `t` (UTC timestamp), `lon`, `lat`, `kind` (`fix` / `modeled`), `err_km` (from Argos class, SSM standard errors, or a per-tag-type default), `qc` (`ok` / `suspect` / `dropped`), `source_row` (pointer back to the raw record). Partitioned by year.

**`dives`** (IMOS summaries; phase 1 evenings): `animal_id`, `t_start`, `t_end`, `n_dives`, `max_depth_m`, `avg_depth_m`, `max_dive_min`.

**`sightings`** (phase 2, photo-ID): `animal_id`, `t`, `t_precision` (`day`/`month`/`year`), `lon`, `lat`, `locality`, `contributor_hash`, `record_url`, `photo_license`.

**`facts`**: `animal_id`, `fact_id` (stable slug, e.g. `days_tracked`), `value` (JSON), `display` (rendered string), `evidence` (list of position/sighting row refs or a query description), `data_version`.

**`photos`**: `aphia_id`, `variant` (`default` / `female` / `male` / `juvenile`), `file_url`, `thumb_path`, `credit`, `license`, `license_url`, `wiki_url`.

**`stories`**: `animal_id`, `data_version`, `prompt_version`, `model`, `story_json` (paragraphs → sentences → fact_ids), `verify_status` (`pass` / `fail` / `review`), `cost_usd`, `created_at`.

**Published assets** (what the browser loads):

| File | Contents | Size target |
|---|---|---|
| `index.json` | One row per animal: id, species, group, name, start, end, last-seen, photo key, shard number | ~120 B/animal → 2,000 animals ≈ 240 KB (60 KB gzipped) |
| `chunks/YYYY.bin` + `YYYY.json` header | All positions in that calendar year, as binary columns (see §5) | ≤ 3 MB per year |
| `animals/shard-NN.json` | Full detail for ~100 animals: full-res track, facts, story, sources | ≤ 2 MB each |
| `photos/<aphia>-<variant>.webp` | Cached 480 px species photos | ~40 KB each |

---

## 3. Phased build plan

Sizes: **S** ≤ 2 h, **M** ≈ half a day, **L** ≈ a full day.

### Phase 0: verify before building (one evening)

| # | Task | Size | Done when |
|---|---|---|---|
| 0.1 | Email the NOAA ATN data team: is `stanford_import_ssm_public` the full public set? Are there newer deployments behind `portal.atn.ioos.us`? Embargo practice? | S | Email sent; answer logged in `config/sources.yaml` notes |
| 0.2 | Check IMOS license per dataset (dm/nrt SSM, dive summaries) and their citation wording | S | Each dataset's license + citation in `sources.yaml` |
| 0.3 | Read CARTO basemap terms for non-commercial traffic; decide CARTO vs PMTiles | S | Decision recorded |
| 0.4 | Build `sensitivity.yaml`: IUCN status for the ~35 spike species from the Red List website; mark VU/EN/CR as sensitive | S | File committed; every spike species has a status |
| 0.5 | Decide embargo numbers (default 90 days all / 12 months sensitive) and write them in `policy.py` config | S | Numbers in config with a comment on why |

### MVP weekend

**Saturday: pipeline**

| # | Task | Size | Done when |
|---|---|---|---|
| 1.1 | Repo skeleton: `uv` project, ruff, pytest, `pipeline/` modules, `Makefile` targets `fetch`, `build`, `test` | S | `make test` runs green on an empty test |
| 1.2 | Port spike sources into `sources/atn_topp.py` and `sources/imos.py` (fetch with cache + `data_version` hash) | M | `make fetch` reproduces spike raw files; re-run without `--refresh` makes no network calls |
| 1.3 | `species.py`: resolve every common name to a WoRMS AphiaID (WoRMS REST API, cached) with `species_overrides.yaml` for misses | M | 33/33 spike species resolved; "Juvenile white shark" maps to white shark + stage; casing variants merge |
| 1.4 | `normalize.py` → warehouse tables (`animals`, `deployments`, `positions`), dm-over-nrt dedupe, sex/stage vocab | M | Counts match spike: 1,663 animals; zero duplicate (animal_id, t) rows |
| 1.5 | `qc.py`: per-group max speed, mark `suspect` steps, flag noisy tracks; daily display cadence | S | Spike's 26 noisy tracks flagged; tests on synthetic jumps |
| 1.6 | `policy.py`: drop positions inside the embargo, coarsen sensitive species within the buffer, drop anything whose source isn't redistributable | M | Unit tests + an **emitted-asset test** that scans every published file and fails on any position newer than the embargo |
| 1.7 | `derive.py`: core facts (§6 of spec) with evidence refs | M | Every animal has `days_tracked`, `path_km`, `max_displacement_km`, `first/last`, `regions` (ocean basin via a Natural Earth polygon lookup) |

**Sunday: front end + deploy**

| # | Task | Size | Done when |
|---|---|---|---|
| 1.8 | `emit.py`: `index.json`, yearly binary chunks, animal shards | M | Assets written; total < 40 MB; largest file < 5 MB |
| 1.9 | `web/` Vite + TS app; port spike map; load index + chunks with binary attributes; lazy-load the previous/current/next year around the playhead | L | Smooth playback across 2002→2026 with all animals on a laptop; memory < 500 MB |
| 1.10 | Animal card: photo (cached thumbnail + credit), identity, facts, source/license, noisy warning, deep link `#/a/<id>?t=<date>` | M | Opening a deep link restores animal + time |
| 1.11 | Filters (group, species, source) + search (name/species/tag ID) over `index.json` | M | Filtering 2,000 animals updates in < 50 ms |
| 1.12 | Deploy to Cloudflare Pages via GitHub Actions on push | S | Public URL serves the site; CI runs `make test` first |

### MVP evenings (3–5 evenings)

| # | Task | Size | Done when |
|---|---|---|---|
| 1.13 | Ingest the 402 ATN per-deployment datasets (raw Argos + sex/stage/weight); SSM-smooth or show fixes with error circles | L | ≥ 2,000 animals total; Alaska seals appear with sex/stage |
| 1.14 | IMOS dive summaries → `dives` table → "deepest dive" fact | S | Elephant seal cards show max depth |
| 1.15 | Photos: cache thumbnails at build; sex/life-stage variants for species where it matters (elephant seals, sea lions) via overrides | S | Female elephant seal card shows a female; no hot-linking |
| 1.16 | Storyworthiness score + templated summary for every animal | S | Every card has a 1–2 sentence factual summary; top-50 list reviewed by hand |
| 1.17 | LLM stories for top 50 with verification (§6) | M | 50 stories pass verification; injection test ≥ 95% recall (§6) |
| 1.18 | Mobile layout, "last known" mode (each animal at its last position, faded by age), loading states | M | Usable on a phone; Lighthouse performance ≥ 80 |
| 1.19 | Weekly cron rebuild; per-animal diff so unchanged animals keep their stories | S | A no-change rebuild makes zero LLM calls and an identical deploy |

**MVP done:** ≥ 2,000 animals, ≥ 30 species, smooth replay on laptop and phone, every card sourced and licensed, embargo test green, ≈ $0 to rerun.

### Phase 2

| # | Task | Size |
|---|---|---|
| 2.1 | Happywhale photo-ID (OBIS-SEAMAP CSV or GBIF download API): sightings table, date precision, dashed "route unknown" rendering, CC-BY-NC gate | L |
| 2.2 | OBIS-SEAMAP telemetry datasets (one adapter, many datasets) | L |
| 2.3 | Movebank public marine studies whose terms allow redistribution (API, per-study license check) | L |
| 2.4 | Cross-portal dedupe (`same_as`) for animals mirrored in several portals | M |
| 2.5 | Species, program and region pages (static, generated) | M |
| 2.6 | Follow an animal: per-animal RSS feed generated at build | S |
| 2.7 | Ocean context: SST anomaly at time/place (NOAA OISST via ERDDAP), named regions/EEZs | M |

### Phase 3

Program partnerships (individual photos and names, embargo agreements), embeddable widget, educator mode, acoustic-receiver detections as a third data shape.

---

## 4. Data pipeline in detail

Each stage reads and writes Parquet; every stage is idempotent and can rerun alone.

1. **Fetch** (per source). HTTP with retries and a generic User-Agent; raw files cached under `data/raw/<source>/` with `fetched_at`. `data_version` = hash of the raw files. ERDDAP: request only needed columns, page large tables by time. WFS: `propertyName` to trim columns; page with `startIndex` if a layer exceeds the server's max features.
2. **Load**. Source adapter turns raw rows into a common frame: `native_id, species_label, sex, stage, site, t, lon, lat, kind, err`. ERDDAP's units row is skipped; timestamps parsed to UTC.
3. **Normalize**. Species label → AphiaID (cached WoRMS lookups + overrides). Sex/stage to vocabularies. Site names title-cased with an override list. Prefer delayed-mode over near-real-time per device. Build `animals`, `deployments`, `positions`.
4. **QC**. Per-step speed against a group maximum (e.g., seals 10 km/h sustained, tunas 15, birds 80); mark steps `suspect`; noisy-track flag (>10% suspect steps). Longitude unwrap per track. Daily display cadence derived, originals kept.
5. **Policy** (always runs before anything is written for the web). Embargo cut by species sensitivity; coarsening within buffer; drop non-redistributable sources; strip any field not meant for publication.
6. **Derive**. Facts with evidence refs; catalog-relative records ("longest leatherback track here"); storyworthiness score.
7. **Enrich**. Species photos; ocean basin/region lookup (Natural Earth polygons); dive summaries. Phase 2: SST, EEZs.
8. **Narrate**. Templated summary for every animal; LLM stories for top N (§6).
9. **Emit**. Index, binary chunks, shards, photos; then the emitted-asset tests; then deploy.

**Change detection.** Each animal gets a content hash of its published positions + facts. The weekly job compares to the last deploy: unchanged animals reuse their story and shard bytes; changed animals regenerate.

---

## 5. Map rendering at scale

- **Binary chunks per year.** For each year: a header listing animals (id, start index, length, group) and three typed arrays: `Float32 lon`, `Float32 lat`, `Float32 day` (days since chunk start). Tracks that cross a year boundary are split with a one-day overlap so trails stay continuous. deck.gl `TripsLayer` and `PathLayer` accept this directly as binary attributes with `startIndices`, so there are no per-point JS objects.
- **Size.** Daily cadence: ~12 bytes per point. Spike: ~320k display points ≈ 4 MB total. At 10,000 animals × ~150 days ≈ 1.5 M points ≈ 18 MB, ~1 MB per year gzipped. Fits comfortably.
- **Loading.** Load the year under the playhead plus the year before (the 90-day trail reaches back) and prefetch the next. "Last known" mode uses only `index.json`.
- **Heads.** Current positions computed per frame from the loaded chunk (binary search per animal, as in the spike), drawn with a `ScatterplotLayer` using typed arrays.
- **Selection.** Clicking loads that animal's shard and draws its full-resolution track with `PathLayer`; observed fixes vs modeled positions styled differently; gaps dashed.
- **Projection.** Start with Web Mercator (MapLibre default). Try MapLibre's globe projection at low zoom once deck.gl overlay support is confirmed for it.

---

## 6. LLM usage

**Where it adds value.** Only the short story on the card (≈80–200 words) for animals worth it. Facts, numbers, summaries for the long tail, QC, species resolution: all deterministic code.

**Models.**
- Writer: **Claude Opus 5.5** (`claude-opus-5-5`), adaptive thinking, effort `medium`, structured outputs (`output_config.format`) returning `{paragraphs: [{sentences: [{text, fact_ids[], context_ids[]}]}]}`. Numbers appear only as placeholders (`{fact:days_tracked}`) that the front end fills from facts.
- Verifier: **Claude Haiku 5.5** (`claude-haiku-5-5`), one call per story: for each sentence, check every claim maps to the cited fact IDs or labeled species-context snippets; return per-sentence pass/fail with reasons.
- Both run through the **Message Batches API** (50% off; results keyed by `custom_id`, never by position).

**Grounding.**
- Fact sheet per animal: identity, facts with IDs and display strings, a compact track summary (monthly positions + regions), gaps, and 3–6 species-context snippets from a curated `species_context.yaml` (each with an ID and a source).
- System prompt: tone guide, banned content (no invented names, emotions, behaviors, weather, birth dates; no causal claims; gaps framed as unknowns).
- Any sentence that fails verification → regenerate that story once with the failure reasons; a second failure → drop the story and show the templated summary.

**Verification test (spec §12 asks for one).** Take 30 verified stories; programmatically inject 1–3 false claims each from a library of edit types: wrong number in text, invented place, invented behavior ("fed on squid"), invented relationship ("with her calf"), species context presented as observed. Run the verifier; target **≥ 95% recall** on injected claims and **≤ 5% false alarms** on the clean originals. This becomes a regression test whenever the prompt or model changes.

**Caching and reruns.** Stories keyed by `(animal_id, data_version_of_that_animal, prompt_version, model)`. A rerun with no new data finds every key and makes no calls. The shared system prompt is likely below the minimum cacheable prefix length, so prompt caching saves little here; batching is the real saving.

**Cost estimate** (list prices: Opus 5.5 $4 / $20 per M input/output tokens; Haiku 5.5 $0.10 / $0.50; batches 50% off):

| Per story | Tokens (est.) | Cost |
|---|---|---|
| Writer input (system + fact sheet) | ~5,000 | $0.020 |
| Writer output (thinking + JSON) | ~2,000 | $0.040 |
| Verifier (in ~5,500 / out ~600) | | ~$0.001 |
| Regeneration allowance (+20%) | | $0.012 |
| **Total, standard** | | **≈ $0.073** |
| **Total, batched** | | **≈ $0.037** |

- MVP (top 50): **≈ $2 batched** (≈ $4 standard).
- If stories were generated for all 2,000 MVP animals: ≈ $75 batched. Not recommended; templated summaries cover the long tail.
- Development iterations (prompt tuning on ~20 animals, ~10 rounds): ≈ $15.
- Weekly reruns: ≈ $0 unless top animals get new data.

These are estimates; measure `usage` on the first 10 real stories and update the table.

---

## 7. Keeping facts separate from species context

- Facts live in the `facts` table with evidence; the story can only reference fact IDs and context IDs.
- The card renders the two visually differently: observed facts plain; species context in a labeled "Typical for ⟨species⟩" style.
- Hovering a sentence highlights its evidence on the map (positions, date ranges).
- Photos are captioned "A ⟨species⟩, not this individual."
- "Unknown" is a first-class value: birth date, sex, stage show as "not recorded" rather than being omitted.

---

## 8. Risks, unknowns, and corrections to the spec

**Verify first** (Phase 0): ATN completeness and embargo practice; IMOS per-dataset licenses; CARTO terms; IUCN statuses; embargo numbers with the programs.

**Risks**

| Risk | Impact | Mitigation |
|---|---|---|
| Coverage stays lumpy (North Pacific, Southern Ocean) | Doesn't feel like "every animal" | Phase 2 sources; say clearly on the page which regions are covered |
| A source objects to aggregation | Takedown, reputational | Contact programs before launch; prominent credit; easy per-source switch-off via `sources.yaml` |
| Light-level tracks look precise | Misleading | Error-aware styling; noisy flag; per-tag-type error defaults |
| Modeled SSM positions look like observations | Misleading | `kind` column; styling difference; card note |
| Static assets outgrow Pages limits | Deploy fails | Yearly chunks + shards; R2 if needed |
| Wikimedia images change or are deleted | Broken photos | Thumbnails cached at build with credit stored alongside |
| Basemap terms change | Outage | PMTiles fallback ready |

**Corrections and refinements to `spec.md`**

1. **"≥ 2,000 animals" needs task 1.13** (ATN per-deployment datasets). The two spike sources alone give 1,663.
2. **Embargo vs. this data:** with a 90-day embargo, current sources lose almost nothing (newest public position is ~11 months old). The embargo matters for future near-real-time sources; keep it, but it won't make the MVP feel less live.
3. **Positions per day for ATN deployments:** raw Argos fixes are irregular and noisy. Showing them needs either our own smoothing or error circles; the spec doesn't say which. Recommendation: show fixes with error styling in MVP, consider SSM smoothing (e.g., the `aniMotum` method, run offline) in phase 2.
4. **The spec's success metric "a few friends keep watching"** is fine for MVP, but add one measurable check: time-to-first-frame < 3 s on a mid-range phone.
5. **Photos:** the spec says prefer sex/stage-matched photos; for most species one default is enough. Limit variants to species with obvious differences (elephant seals, sea lions, some sharks by size).

---

## 9. Build status (2026-11-25)

MVP built in this session: tasks 1.1–1.11, 1.13–1.16, 1.18 done; 2,029 animals, 39 species, 2002–2025.

Where the build differs from the plan above:

- **No AI API calls.** Stories are written by hand (here, in a Claude Code session) from the generated fact sheets and committed to `stories/`. The build checks them deterministically instead of an LLM verifier: placeholders only, no digits, cited facts/context must exist, stale stories rejected (`tests/test_stories.py`). Section 6's API cost estimate applies only if you automate writing later. 13 of the 50 candidates have stories; the other fact sheets are ready.
- **Warehouse is one DuckDB file** (`data/warehouse.duckdb`) rather than a folder of Parquet files. Same role, simpler.
- **No Preact.** The UI is small enough for plain TypeScript.
- **Not deployed yet** (task 1.12) and no weekly cron (1.19): both need a Cloudflare account and a git repo.
- **Phase 0 reduced** to checks done in code: per-source license notes in `sources.yaml`; IUCN statuses pulled from Wikidata. Emails to programs are optional and deferred.
- **Task 1.13 shows fixes, not error circles:** ATN Argos fixes are filtered (QARTOD pass, class 3/2/1/0/A, speed cap) and one per day is shown.

Open items found while building:

- Wikidata lists the southern elephant seal as **Vulnerable**; the Red List may say Least Concern. It's treated as sensitive (stricter embargo, rounded recent positions) until checked.
- A few raw Argos fixes survive the filter (e.g. a ribbon seal reaching 79°N). Consider a stricter filter or SSM smoothing.
- IMOS near-real-time positions are ignored for devices that have delayed-mode data, which can drop the last few weeks of a track.
- The JS bundle is ~490 KB gzipped (mostly deck.gl + MapLibre); split it if load time matters.
