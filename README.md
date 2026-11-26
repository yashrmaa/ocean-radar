# Ocean Radar

A replay map of publicly tracked marine animals: 2,029 tagged animals across 39 species,
2002–2025, from NOAA's Animal Telemetry Network and Australia's IMOS Animal Tracking Facility.
Press play, click an animal, read its record. See `spec.md` (what and why) and `plan.md` (how).

## Run it

```bash
uv sync                                         # Python deps (pipeline)
uv run python -m pipeline.build                 # download (cached in data/raw) + build web/public/data
npm --prefix web install
npm --prefix web run dev                        # http://localhost:5190
```

- `uv run python -m pipeline.build --offline` rebuilds from the cache without network access.
- `--now YYYY-MM-DD` pins "today" for the embargo cut (reproducible builds).
- `uv run pytest` runs the checks, including the scan of every published file for positions inside the embargo. Run it after a build.
- `npm --prefix web run build` writes the static site to `web/dist/` (deploy that folder anywhere static).

## Where things are

| Path | What |
|---|---|
| `pipeline/sources/` | One adapter per data source (fetch + load into staging tables) |
| `pipeline/config/sources.yaml` | License registry: which sources may be published; switch one off with `enabled: false` |
| `pipeline/config/sensitivity.yaml` | Embargo days and coordinate rounding for threatened species |
| `pipeline/config/species_overrides.yaml` | Fixes for species labels, display names, and per-sex photos |
| `pipeline/policy.py` | Embargo, rounding and license gate. Everything published passes through it |
| `stories/factsheets/` | Generated each build for the 50 top-ranked animals: the facts a story may use |
| `stories/*.json` | Hand-written stories (13 so far). Numbers only as `{fact}` placeholders |
| `stories/species_context.yaml` | General species knowledge stories may cite, each with a source |
| `web/src/` | The map app (MapLibre + deck.gl, TypeScript) |
| `spike/` | The original one-file prototype |

## Writing a story

1. Pick a fact sheet in `stories/factsheets/`.
2. Write `stories/<same file name>.json` (copy an existing story for the format). Every sentence must
   either use `{fact_id}` placeholders (listed in `facts`), cite a `context` id from
   `species_context.yaml`, or be marked `"framing": true`. No digits outside placeholders.
3. Rebuild. The build rejects any story that breaks the rules or was written for older data, and the card
   falls back to the template summary.

No API key is needed: stories are written by hand (or in a Claude Code session) and committed.
