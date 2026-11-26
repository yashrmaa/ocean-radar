# Ocean Radar: a flight radar for marine animals

*Product & design brief, v2. Working name: **Ocean Radar**. Replaces the whale-biography brief (kept as `spec-biography.md`). Grounded in a data spike: see `spike/` (prototype `radar.html`, pipeline `build.py`).*

---

## 0. Instructions for the planner

You are receiving a product brief, not a plan. Please produce an **implementation plan** covering:

1. Architecture and stack (with reasoning and rejected alternatives)
2. Data model: animals, deployments, positions, sightings, species, sources, photos, generated text
3. A phased build plan: a **weekend MVP**, then later phases; tasks ordered, sized (S/M/L), each with a definition of done
4. The data pipeline in detail (ingest → normalize → QC → derive → enrich → publish), per source
5. Map rendering at scale (thousands of animals, millions of positions) on a static host
6. LLM usage, if any: where it adds value, grounding, cost estimate
7. Risks, unknowns, and anything you'd verify before building (flag anything in this brief you think is wrong)

The builder is one experienced software engineer (dev tools / AI infra background), working evenings and weekends, on a budget. Optimize for: a striking MVP fast, near-zero running cost (static hosting), and respect for data licenses and the animals.

---

## 1. One-line pitch

A live-feeling world map of every publicly tracked marine animal (sharks, turtles, seals, whales, tunas, seabirds). Press play to watch two decades of journeys sweep across the oceans; click any animal to see who it is, where it has been, and its story.

## 2. Why this, why now

- Thousands of marine animals have been satellite-tagged or photo-identified by research programs, and much of that data is **public**. It sits in research portals built for scientists: dataset lists, download forms, one study at a time.
- Consumer trackers exist but are **single-program**: OCEARCH shows its own few hundred tagged animals; Movebank's Animal Tracker app spans all taxa but leans terrestrial/birds and is research-styled; NOAA's ATN portal is a data portal. Happywhale has rich per-whale profiles but no live-style map.
- **Nobody aggregates across programs into one beautiful, public "radar" with a story for each animal.** That aggregation + design + honest storytelling is the product.
- The spike proved feasibility: two sources merged in an evening into **1,663 animals, 33 species, 2002 → Jan 2026**, rendered as an animated globe with click-through cards.

## 3. Target users

1. **Primary:** curious general audience and ocean lovers; people who'd open a flight-radar app just to watch.
2. **Secondary:** educators (a ready-made classroom visual), science communicators, whale-watch and dive operators.
3. **Later:** the research programs themselves (an outreach surface for their data, with attribution).

## 4. Principles (non-negotiable)

1. **Honest about time.** Public data is not live. Newest public positions lag weeks to ~a year (spike: newest IMOS position Jan 2026 while "today" was Nov 2026; Stanford/NOAA tracks end 2017). The product is a **replay radar**: every animal shows when it was last seen, and the UI never implies a position is current when it isn't. "Live" is only ever a label for data that genuinely is.
2. **Protect the animals.** No precise recent positions for sensitive species. Proposed defaults (planner to refine):
   - Positions younger than **90 days** are withheld for all animals; younger than **12 months** for IUCN VU/EN/CR species or populations.
   - For sensitive species, coordinates coarsened (e.g., 0.5°) for anything within the embargo-plus-buffer window, and breeding/haul-out sites shown as regions, not points.
   - Respect any embargo flags in the source; if a source marks data restricted, it is not shown.
3. **Respect licenses and sources.** Every animal card names its data source, program, and license, with a link back. Track sources by license: CC-BY / US-Gov public data can be redistributed; **CC-BY-NC** (e.g., Happywhale via OBIS-SEAMAP) keeps the project non-commercial; anything "by permission" (OCEARCH, many Movebank studies) is excluded until permission is granted. Reach out to programs early.
4. **Never invent.** Every fact about an individual comes from its records. Unknowns are shown as unknown: **birth date is almost never known** (tags record life stage at tagging; photo-ID only knows birth year if first seen as a calf), and sex/size are often missing. Gaps in a track are shown as gaps.
5. **Show uncertainty visibly.** Modeled positions (state-space model output) are estimates; light-level geolocation tracks (tunas, marlins, some seabirds) carry errors of tens to hundreds of km. The UI distinguishes observed fixes, modeled positions, and flagged-noisy tracks.
6. **Cheap to run.** Static site regenerated on a schedule; no backend in MVP.

## 5. Data

### 5.1 Two shapes of individual data

Only **individually identified** animals belong on the radar. Generic occurrence records (GBIF/OBIS "a dolphin was here") are not individuals and are out of scope.

| Shape | What it looks like | Examples | Render as |
|---|---|---|---|
| **Telemetry tracks** | Positions every few hours to daily for weeks–years | Satellite tags (Argos, GPS, light-level) on sharks, turtles, seals, tunas, whales, seabirds | Continuous animated trail |
| **Photo-ID sightings** | Discrete sightings, months or years apart, over decades | Happywhale fluke matches (humpbacks, etc.) | Dots in time, dashed "route unknown" links |

Both must coexist on one map with honest, distinct styling.

### 5.2 Sources (verified in spike unless noted)

| Source | Access | Content (as found) | License |
|---|---|---|---|
| **NOAA IOOS Animal Telemetry Network (ATN)**: `atn.ioos.us/erddap` | ERDDAP (CSV, no key) | (a) `stanford_import_ssm_public`: Stanford TOPP, **1,243 animals, 30 species, 2002–2017**, daily state-space-model positions, ~256k points; species + tag ID only. (b) **402 per-deployment datasets**, almost all Alaska ice seals + false killer whales, 2004–2018, raw Argos fixes with QC flags, **sex, life stage, age, weight** | Free use & redistribution (US Gov) |
| **IMOS Animal Tracking Facility (AATAMS)**: AODN GeoServer WFS `geoserver-123.aodn.org.au` | WFS (CSV, no key) | `aatams_sattag_qc_dm_ssm_data` (delayed-mode, QC) + `..._nrt_ssm_data` (near-real-time; overlaps 208 of 219 devices): **420 animals, 2018–Jan 2026**, 6-hourly modeled positions with standard errors; southern elephant & Weddell seals, olive ridley/flatback/green turtles; **sex, life stage, release site**. Also dive summaries (max depth, dive time), raw locations, NT hawksbill/olive ridley, penguin & shearwater biologging layers | CC BY 4.0 (verify per dataset) |
| **Happywhale via OBIS-SEAMAP / GBIF** *(not yet ingested)* | CSV download / GBIF API | Photo-ID sightings per species per ocean basin; `organism_id` = individual; dates of varying precision | **CC-BY-NC**; photos per-record licensed |
| **OBIS-SEAMAP telemetry** *(not yet ingested)* | CSV / API | Many tag datasets incl. seaturtle.org STAT | Per dataset |
| **Movebank public studies** *(not yet ingested)* | REST API (account) | Huge, multi-taxa; per-study terms; sensitive species restricted | Per study; many disallow redistribution |
| **OCEARCH** | Permission only | Sharks etc. | Excluded unless granted |

Known gaps in the spike's coverage: Atlantic (apart from tuna) and Indian Ocean are nearly empty; the NOAA portal UI (`portal.atn.ioos.us`) returned 502 during the spike, so ATN may expose more deployments there than ERDDAP does. **Verify with the ATN team.**

### 5.3 Normalization (lessons from the spike)

- **Species:** labels are inconsistent even within one source ("Olive Ridley Turtle" vs "olive ridley turtle"; "Juvenile white shark" listed as a species). Normalize to a **species table keyed by scientific name** (WoRMS AphiaID), with common name, group (fish / shark / whale / seal / turtle / bird), IUCN status.
- **Life stage / sex:** map to controlled vocabularies (fix "juvenille", "NA", "m/f").
- **Duplicates across feeds:** prefer delayed-mode QC over near-real-time for the same device; dedupe the same animal across portals (e.g., an ATN dataset also mirrored to OBIS-SEAMAP) by program + tag ID + time overlap.
- **Cadence:** normalize to a display cadence (spike: one position per UTC day) while keeping originals for detail views.
- **Antimeridian:** unwrap longitudes per track so Pacific paths don't jump across the map.
- **Quality flags:** flag tracks where >10% of daily steps exceed 150 km/day (spike: 26 of 1,663 flagged, mostly Atlantic bluefin and marlins). Planner: propose a per-group speed model and whether to smooth rather than just flag.

### 5.4 Photos

Tracking data contains **no photos of individuals** (photo-ID data does, but under per-record licenses).

- **MVP (built in spike):** a **representative species photo** on every card: the lead image of the species' English Wikipedia article, resolved to Wikimedia Commons, with **credit + license + link**, accepting only CC / public-domain licenses. Captioned **"A ⟨species⟩, not this individual."** Spike result: 33/33 species covered.
- **Improve:** prefer photos matching the animal's **sex and life stage** where it matters visually (spike: a female elephant seal's card showed a bull); allow a curated override per species; cache images rather than hot-linking Wikimedia at scale.
- **Individual photos (later):** Happywhale fluke photos only by **linking out** or with explicit permission; tagging programs may share deployment photos ("this seal, on the day she was tagged"), which is a strong collaboration ask.

## 6. Derived facts (deterministic, never by an LLM)

Per animal:
- Species, group, sex, life stage at tagging, tagging/release site, program, source, license
- First and last position (after embargo), days tracked, number of positions
- Path length (with a "may be overstated" note when flagged), max distance from release site, bounding region(s)
- Oceans / named regions / EEZs visited; crossings (e.g., "crossed the Pacific")
- Deepest dive and typical dive depth, where dive data exists (IMOS)
- Gaps (longest stretch without fixes); "last seen" age relative to today
- Records relative to the catalog and to its species ("longest track of any leatherback here")
- For photo-ID animals: years known, sightings, distinct contributors, return fidelity (see `spec-biography.md` §6)

## 7. The product

### 7.1 The radar (home)
- Full-screen dark globe/map. Every animal is a dot at its position **at the current replay time**, with a fading trail.
- **Time bar:** play/pause, speed, scrubber across the full range (2002→latest), year ticks, "N animals at sea".
- Default view: replay at a pleasing speed from a curated start; option to jump to "latest" which shows each animal's **last known** position, faded by age, with "last seen X ago".
- **Filters:** group (sharks, turtles, seals, whales, fish, birds), species, program, ocean/region; search by name/tag ID/species.
- Performance target: smooth on a mid-range laptop and recent phone with **all animals** loaded.

### 7.2 The animal card (click)
1. **Photo** (species-representative, credited; see §5.4)
2. **Identity:** species, name if the program gave one, sex + life stage, tag ID, program, tagging site and date
3. **Track:** full path highlighted on the map; dashed where gaps; noisy-track warning if flagged
4. **Fact list** (§6), each fact traceable to records
5. **Story** (short, see §8)
6. **"What we know vs. what's typical"**: this animal's record vs. species-level context (labeled as general knowledge)
7. **Source & license** line with link back; "Birth date unknown" etc. stated plainly
8. Share link (deep-link to animal + time)

### 7.3 Later surfaces
- Species pages ("all 296 southern elephant seals, overlaid"), program pages (credit to the research teams), region pages ("who passes through Monterey Bay").
- Curated "journeys of the week".

## 8. Stories

- The card's first job is facts + map; the story is a **short caption-style narrative** (≈80–200 words), not an essay.
- Generated from a structured fact sheet only; output references fact IDs so the UI can link claims to evidence; numbers rendered from facts via placeholders (`{fact:days_tracked}`), never typed by the model.
- A cheaper verification pass checks every claim maps to a fact or a labeled species-context snippet; failures are regenerated or dropped.
- Generate only for animals worth it (score by track length, duration, distance, crossings, data richness); others get a templated summary.
- Cache by `(animal_id, data_version, prompt_version)`.
- Tone: curious, warm, precise. No invented names, emotions, behaviors, weather, or birth dates.

## 9. Architecture preferences

- **Batch pipeline** (Python; the spike's `build.py` is the seed) per source: fetch → normalize → QC → derive → emit.
- **Static site** with precomputed assets. The spike inlined a 6 MB JSON; that won't scale past a few thousand animals. Planner: propose chunking (per time window and/or per region), binary formats (e.g., Arrow/flatbuffers/quantized coordinates), and lazy per-animal detail files.
- **Rendering:** spike used MapLibre GL + deck.gl `TripsLayer` (animated trails) + `ScatterplotLayer` (heads) + `PathLayer` (selection) on a CARTO dark basemap. Planner: confirm, consider globe projection, and check basemap terms for the intended traffic.
- **Refresh:** scheduled (weekly/monthly) rebuild; diff per animal so only changed animals' assets and stories regenerate.
- No accounts in MVP. "Follow an animal" (email/RSS on new data) is phase 2.

## 10. Scope

### MVP (one weekend + a few evenings)
- Sources: NOAA ATN (Stanford TOPP + the 402 Alaska deployments) and IMOS AATAMS (SSM tracks + dive summaries)
- Species table with scientific names; normalized sex/life stage; dedupe; noisy-track flags
- Embargo + coarsening rules applied in the pipeline (§4.2)
- Radar with replay, filters, search; animal card with species photo, facts, source/license
- Templated summaries for all; LLM stories for top ~50 with verification
- Static deploy

### Phase 2
- Happywhale photo-ID sightings (CC-BY-NC) rendered as sighting dots with "route unknown" links
- OBIS-SEAMAP telemetry datasets; Movebank public studies whose terms allow redistribution
- Follow/subscribe; species, program and region pages
- Ocean-condition context (SST anomalies at time/place), named regions/EEZs

### Phase 3
- Partnerships: program-supplied individual photos and names; embeddable widget for programs/aquariums
- Educator mode
- Acoustic-detection networks (receiver hits) as a third data shape

### Out of scope
- Commercial use of NC-licensed data without permission
- Rehosting individual-animal photos without permission
- Showing positions inside the embargo window, or precise sites for sensitive species
- Claims about health, cause of death, or behavior not in the record

## 11. Success criteria

- MVP shows **≥ 2,000 animals across ≥ 30 species** with smooth playback on laptop and phone.
- Every card's facts trace to source records; every card names its source and license.
- No position younger than the embargo is present in any published file (test it).
- A few friends open it and keep watching.
- Running cost ≈ static hosting + a few dollars of API spend per full regeneration; reruns with no new data cost ~$0.

## 12. Risks & open questions

1. **Coverage is lumpy.** Spike data is North Pacific (to 2017) + Southern Ocean/northern Australia (2018+). Fish are ~55% of animals. The "every marine animal" feel depends on adding sources, which is mostly license and normalization work.
2. **Licenses vary per dataset/study**; some sources (Movebank, OCEARCH) need permission. Keep a per-source license registry that drives what's published.
3. **Recency vs. safety:** stricter embargoes make the radar feel less live. Get input from the programs (and IUCN-listed species experts) on acceptable delays.
4. **Data quality:** light-level geolocation error, modeled positions that look more certain than they are, duplicates across portals.
5. **Photos:** species photos can mislead (sex/life stage mismatch); Wikimedia hot-linking at scale; individual photos need permission.
6. **Scale:** browser memory and rendering with millions of positions; asset chunking strategy.
7. **Positioning vs. OCEARCH / Animal Tracker:** the differentiator is breadth across programs + design + honesty. Make sure the research programs see it as outreach for their work, not a scrape of it.
