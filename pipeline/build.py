"""Ocean Radar pipeline entry point.

  uv run python -m pipeline.build              # fetch (cached) + full build
  uv run python -m pipeline.build --refresh    # re-download all sources
  uv run python -m pipeline.build --offline    # never touch the network (fail if a cache is missing)
  uv run python -m pipeline.build --now 2026-11-25   # pin "today" (embargo cut), for reproducible builds
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

import duckdb

from . import derive, emit, normalize, photos, policy, qc, species, stories
from .sources import ALL as SOURCES
from .util import DATA, WAREHOUSE, log

STAGING = """
CREATE OR REPLACE TABLE stg_animal (
  animal_id VARCHAR, source_id VARCHAR, native_id VARCHAR, program VARCHAR, species_label VARCHAR,
  scientific_name VARCHAR, sex VARCHAR, stage VARCHAR, site VARCHAR, citation VARCHAR, dataset_url VARCHAR);
CREATE OR REPLACE TABLE stg_position (
  animal_id VARCHAR, t TIMESTAMP, lon DOUBLE, lat DOUBLE, kind VARCHAR, err_km DOUBLE);
CREATE OR REPLACE TABLE stg_dive (
  animal_id VARCHAR, t TIMESTAMP, max_depth_m DOUBLE, n_dives DOUBLE, max_dive_min DOUBLE);
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--now", help="ISO date treated as today for the embargo")
    args = ap.parse_args()
    now = (datetime.fromisoformat(args.now).replace(tzinfo=UTC) if args.now
           else datetime.now(UTC))

    if not args.offline:
        for src in SOURCES:
            log(f"fetch {src.SOURCE_ID}")
            src.fetch(args.refresh)

    DATA.mkdir(exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE))
    con.execute("SET TimeZone = 'UTC'")
    con.execute(STAGING)
    for src in SOURCES:
        src.load(con)

    species.build(con, offline=args.offline)
    normalize.build(con)
    qc.build(con)
    policy.build(con, now)
    derive.build(con)
    photos.build(con, offline=args.offline)
    stories.build(con)
    emit.build(con, now)
    con.close()


if __name__ == "__main__":
    main()
