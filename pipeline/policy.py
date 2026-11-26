"""Publication policy: license gate, embargo, coordinate coarsening.

Everything published comes from `published_positions`, `published_dives` and `published_animals`,
which this step creates. Nothing downstream reads `positions` or `display_positions` directly.
"""
from datetime import datetime, timedelta

import yaml

from .util import CONFIG, log


def load_config() -> tuple[dict, dict]:
    sources = yaml.safe_load((CONFIG / "sources.yaml").read_text())
    sens = yaml.safe_load((CONFIG / "sensitivity.yaml").read_text())
    return sources, sens


def build(con, now: datetime) -> None:
    sources, sens = load_config()
    publishable = [k for k, v in sources.items()
                   if v.get("enabled", True) and v["redistribution"] in ("allowed", "non_commercial")]
    cut_default = now - timedelta(days=sens["embargo_days"]["default"])
    cut_sensitive = now - timedelta(days=sens["embargo_days"]["sensitive"])
    coarsen_until = cut_sensitive - timedelta(days=sens["coarsen"]["buffer_days"])
    grid = float(sens["coarsen"]["degrees"])
    naive = lambda d: d.replace(tzinfo=None)

    con.execute("""
        CREATE OR REPLACE TEMP TABLE _animal_policy AS
        SELECT a.animal_id, s.sensitive
        FROM animals a JOIN species s USING (taxon_key)
        WHERE list_contains(?, a.source_id)
    """, [publishable])
    con.execute(f"""
        CREATE OR REPLACE TABLE published_positions AS
        SELECT d.animal_id, d.t,
               CASE WHEN p.sensitive AND d.t > ? THEN round(d.lon / {grid}) * {grid} ELSE d.lon END AS lon,
               CASE WHEN p.sensitive AND d.t > ? THEN round(d.lat / {grid}) * {grid} ELSE d.lat END AS lat,
               d.kind, d.err_km,
               (p.sensitive AND d.t > ?) AS coarsened
        FROM display_positions d JOIN _animal_policy p USING (animal_id)
        WHERE d.t <= CASE WHEN p.sensitive THEN ?::TIMESTAMP ELSE ?::TIMESTAMP END
    """, [naive(coarsen_until)] * 3 + [naive(cut_sensitive), naive(cut_default)])
    con.execute("""
        CREATE OR REPLACE TABLE published_dives AS
        SELECT d.* FROM dives d JOIN _animal_policy p USING (animal_id)
        WHERE d.t <= CASE WHEN p.sensitive THEN ?::TIMESTAMP ELSE ?::TIMESTAMP END
    """, [naive(cut_sensitive), naive(cut_default)])
    con.execute("""
        CREATE OR REPLACE TABLE published_animals AS
        SELECT a.* FROM animals a
        WHERE animal_id IN (SELECT animal_id FROM published_positions GROUP BY 1 HAVING count(*) >= 2)
    """)
    con.execute("DELETE FROM published_positions WHERE animal_id NOT IN (SELECT animal_id FROM published_animals)")
    n_a, n_p, n_c = con.execute("""SELECT (SELECT count(*) FROM published_animals),
        (SELECT count(*) FROM published_positions), (SELECT count(*) FROM published_positions WHERE coarsened)""").fetchone()
    log(f"policy: {n_a} animals, {n_p:,} positions published ({n_c:,} coarsened); "
        f"cut {cut_default:%Y-%m-%d} (sensitive {cut_sensitive:%Y-%m-%d}); sources {publishable}")
