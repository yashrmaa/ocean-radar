# Fluke — Biographies of Individual Whales

*Product & design brief for implementation planning. Working name: **Fluke** (whales are identified by the unique pattern on their tail fluke).*

---

## 0. Instructions for the planner

You are receiving a product brief, not a plan. Please produce an **implementation plan** covering:

1. Architecture and stack (with reasoning and rejected alternatives)
2. Data model for animals, sightings, derived journeys, enrichment, and generated stories
3. A phased build plan: a **weekend MVP**, then later phases; tasks ordered, sized (S/M/L), each with a definition of done
4. The data pipeline in detail (ingest → clean/dedupe → derive → enrich → score → narrate → publish)
5. LLM usage: which steps use which model tier, prompting/grounding strategy, caching, and a **cost estimate** (per biography and for the full MVP catalog)
6. How to keep stories factually grounded and clearly separate fact from species-level context
7. Risks, unknowns, and anything you'd verify before building (flag anything in this brief you think is wrong)

The builder is one experienced software engineer (dev tools / AI infra background), working evenings and weekends, on a budget (Claude Pro plan for coding help; small API spend is fine). Optimize for: a delightful MVP fast, low running cost (ideally static hosting), and respect for data licenses and the animals.

---

## 1. One-line pitch

Pick a real whale, identified by its tail, and read the story of its life — decades of sightings across oceans, stitched into a narrative with maps — built from public citizen-science photo-ID data.

## 2. Why this, why now

- **Happywhale** uses AI fluke recognition plus photos submitted by whale watchers and researchers to identify individual whales (especially humpbacks) across the world, often tracking the same animal across many years and multiple oceans.
- A large portion of that data is published openly via **OBIS-SEAMAP** (Duke University) and mirrored to **GBIF**, with a stable per-animal identifier.
- Today, that data is shown as encounter lists and maps for researchers and contributors. **Nobody turns an individual's sighting history into a story** a general audience wants to read and follow.
- People already love following individual animals (shark trackers, stork trackers); a narrative layer makes it far more engaging.

## 3. Target users

1. **Primary:** curious general audience, ocean lovers, whale-watch customers who want to know "who" they saw.
2. **Secondary:** educators (lesson material), whale-watch operators (stories about local regulars).
3. **Later:** Happywhale contributors who want to see the story of "their" whale.

## 4. Principles (non-negotiable)

1. **Grounded storytelling.** Every factual claim about an individual must come from its sighting records or derived facts. Species-level context (e.g., typical humpback migration behavior) is allowed but must be **visibly labeled** as general knowledge, never presented as something this whale was observed doing.
2. **Respect the license and the source.** The Happywhale datasets on OBIS-SEAMAP are **CC-BY-NC** (verify per dataset). The project must be non-commercial unless permission is obtained, must attribute Happywhale, its contributors, and OBIS-SEAMAP per their citation guidance, and should link back to Happywhale encounter pages rather than rehosting photos. Reaching out to Happywhale early to collaborate is strongly encouraged.
3. **Protect the animals.** Some species are critically endangered (e.g., North Atlantic right whale). **Do not publish precise recent locations**: delay recent sightings (e.g., hide the last N days/months) and coarsen coordinates for sensitive species. The planner should propose concrete rules.
4. **Honest about gaps.** Sightings are opportunistic: absence of sightings ≠ absence of the whale. Gaps in the record should be framed as mystery ("where was it for those four years?"), not invented.
5. **Cheap to run.** Prefer a static site regenerated periodically over a live backend.

## 5. Data

### 5.1 Primary source: Happywhale data via OBIS-SEAMAP
- Datasets are split **per species per ocean basin** (e.g., "Happywhale – Humpback whale in North Pacific Ocean"). Downloadable as CSV (and GIS formats) after agreeing to OBIS-SEAMAP terms; also discoverable via GBIF (which has a download/occurrence API — evaluate which is easier to automate).
- Known attributes include: `occurrence_id`, `catalog_number` (link to Happywhale), `recorded_by` (contributor), `individual_count`, `sex` (if known), `organism_id` (**individual animal ID**), `organism_name` (name, if given), `organism_remarks`, `event_id`, `event_date`, `event_time` (local), `higher_geography` (ocean), `water_body`, `locality`, plus latitude/longitude.
- Known quirks: possible duplicate records across Happywhale and other datasets; **date precision varies (some records are year-only)**; datasets are updated roughly monthly; coverage is heavily biased toward places with whale-watching and researchers.
- An individual may appear in multiple datasets (e.g., a humpback in North Pacific and Arctic datasets) → must merge by `organism_id` across datasets.

### 5.2 Enrichment (phase 2+)
- **Geography:** reverse-geocode localities; distance from coast; named regions (feeding grounds, breeding grounds); great-circle distances between consecutive sightings.
- **Ocean conditions:** sea-surface temperature or anomalies at the time/place of sightings (NOAA / Copernicus), e.g., "seen during the 2015 marine heatwave."
- **Species context:** a small curated knowledge base per species (migration seasons, typical routes, lifespan, behavior) used only as labeled context.
- **Social context:** other identified individuals photographed in the **same encounter** (`event_id`) → associations ("often seen with #XYZ").

### 5.3 Later sources (optional)
- Satellite-tagged animals (e.g., seaturtle.org STAT telemetry on OBIS-SEAMAP; OCEARCH requires permission) for "tracked" biographies with continuous paths rather than discrete sightings.

## 6. Derived facts (computed deterministically, never by the LLM)

Per individual:
- First and most recent sighting (respecting the recency delay), total span in years
- Number of sightings, distinct years seen, distinct localities, distinct oceans
- Number of distinct contributors who photographed it ("photographed by 47 different people")
- Longest gap between sightings; longest distance between consecutive sightings and the implied minimum average speed (flag implausible values as likely data errors)
- Seasonal pattern (which months/places recur → likely feeding vs. breeding areas, labeled as inference)
- Return fidelity (e.g., "returned to the same bay in 9 different summers")
- Firsts and records relative to the catalog (e.g., top 1% for span or distance)
- Associations with other individuals (co-occurrence in the same encounter)
- Sex, if recorded; name, if given

## 7. Choosing whose stories to tell: "storyworthiness"

The MVP should not generate stories for every animal. Score individuals and pick the best, e.g., weighting: span of years, number of sightings, ocean crossings / distance, distinct places, return fidelity, presence of a name, associations. The planner should propose a scoring function and a way to sanity-check results (e.g., manually review the top 50).

## 8. The biography (main deliverable)

Each individual gets a page with:
1. **Header:** name or ID, species, sex (if known), years known, a link to its Happywhale profile/encounters, attribution.
2. **Animated journey map:** sightings in order, with gaps visualized honestly (dashed lines labeled "unknown route").
3. **Chaptered narrative** (roughly 600–1,200 words): e.g., "First Sighting (2009)", "The Hawaii Winters", "Four Missing Years", "The Long Crossing", "Still Out There". Written warmly, non-anthropomorphizing beyond reason, never inventing observed behavior.
4. **Fact cards:** the derived facts in section 6, each traceable to records.
5. **"What we know vs. what's typical" panel:** separating this whale's observed record from species-level context.
6. **People behind the story:** a count of contributors and a general thank-you; link out rather than listing individual names prominently (follow the dataset's citation guidance).
7. **Follow:** subscribe to updates when new sightings appear in a future data refresh (phase 2).

Also:
- **Home page:** featured "Whale of the Week," gallery of top biographies, search by ID/name/region.
- **Regional pages (phase 2):** "the regulars of Monterey Bay," useful for whale-watch customers.

## 9. Narrative generation

- Deterministic code produces a compact, structured **fact sheet** per individual (sightings timeline, derived facts, enrichment, species context snippets explicitly marked as such).
- A frontier model writes the story from the fact sheet only, outputting structured JSON (chapters with text + references to fact IDs) so the UI can link claims to evidence.
- A second, cheaper pass verifies that each sentence's factual claims map to fact IDs or labeled species context; failing stories are regenerated or flagged for review.
- Tone guide: curious, warm, precise; wonder over drama; uncertainty stated plainly. No invented names, emotions, or behaviors.
- Cache by `(organism_id, data_version, prompt_version)`; only regenerate when new sightings arrive.

## 10. Architecture preferences

- **Batch pipeline** (e.g., Python with DuckDB/pandas) that ingests the datasets, computes facts, scores individuals, generates stories, and emits a **static site** (JSON + HTML) deployable for free/cheap.
- Map rendering client-side (e.g., MapLibre / deck.gl or similar); planner to recommend.
- Scheduled refresh (e.g., monthly, matching dataset update cadence) that detects new sightings for followed whales and regenerates affected pages.
- No user accounts in MVP; "follow" in phase 2 could be email or RSS.

## 11. Scope

### MVP (one focused weekend + a few evenings)
- Ingest humpback datasets for one or two ocean basins
- Clean, dedupe, merge by `organism_id`; handle date precision
- Compute derived facts; storyworthiness scoring; pick top ~20 individuals
- Generate grounded biographies with verification pass
- Static site: home + biography pages with a journey map and fact cards
- Attribution and license compliance; recency delay and coordinate coarsening rules in place

### Phase 2
- All Happywhale species/basins available on OBIS-SEAMAP
- Enrichment: ocean conditions, regions, associations ("social network" view)
- Follow/subscribe to updates; monthly refresh
- Regional "regulars" pages

### Phase 3
- Tagged-animal biographies (turtles, sharks with continuous tracks, where licenses allow)
- Educator mode (lesson plans per biography)
- Collaboration features with Happywhale if they're interested (e.g., embed stories on their site)

### Out of scope
- Commercial use without permission
- Rehosting contributor photos without permission
- Publishing precise recent locations of endangered animals
- Any claims about individual health, causes of death, or behavior not in the record

## 12. Success criteria

- MVP: 20 biographies that the builder (and a few friends) find genuinely moving and accurate; every factual sentence traceable to a record.
- Verification pass catches injected fake claims in testing (planner: propose a test method).
- Full MVP generation costs only a few dollars in API spend; re-runs with no new data cost ~$0.
- Site hosts statically at near-zero cost.

## 13. Risks & open questions

1. License terms and attribution requirements per dataset; whether Happywhale would want to collaborate (or object).
2. How much of the full Happywhale catalog is actually in the public datasets (it's described as "a portion").
3. Data quality: duplicates, misidentifications, year-only dates, implausible jumps.
4. Location sensitivity rules for endangered species.
5. Avoiding anthropomorphism while keeping stories engaging.
6. Sampling bias: stories may over-represent whales near popular whale-watching spots — acknowledge it in the product.
7. Photo display: linking vs. embedding vs. permission.
