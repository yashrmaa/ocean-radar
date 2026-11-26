"""Quality control and display cadence.

`display_positions`: one position per animal per UTC day (best error estimate that day),
with implausibly fast Argos fixes removed. Modeled (SSM) positions are never dropped, only
counted toward the noisy flag, because the model already smoothed them.

`animal_qc`: per-animal noisy flag (> NOISY_SHARE of daily steps faster than FAST_KM_PER_DAY).
"""
import itertools
from collections import defaultdict

from .util import haversine_km, log, replace_table

# Upper bound on sustained travel per day by group, used to drop bad Argos fixes.
MAX_KM_PER_DAY = {"fish": 200, "shark": 200, "seal": 150, "whale": 250, "turtle": 120, "bird": 1000,
                  "mammal": 150, "bat": 600, "reptile": 60, "amphibian": 20, "insect": 400, "other": 300}
FAST_KM_PER_DAY = 150
NOISY_SHARE = 0.10


def build(con) -> None:
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _daily AS
        SELECT p.animal_id, s.grp || CASE WHEN a.coarse THEN '|coarse' ELSE '' END AS grp, date_trunc('day', p.t) AS day, p.t, p.lon, p.lat, p.kind, p.err_km
        FROM positions p JOIN animals a USING (animal_id) JOIN species s USING (taxon_key)
        QUALIFY row_number() OVER (PARTITION BY p.animal_id, date_trunc('day', p.t)
                                   ORDER BY coalesce(p.err_km, 1e9), p.t) = 1
        ORDER BY p.animal_id, p.t
    """)
    tracks = defaultdict(list)
    for row in con.execute("SELECT animal_id, grp, t, lon, lat, kind, err_km FROM _daily").fetchall():
        tracks[row[0]].append(row)

    kept, flags = [], []
    dropped = 0
    for animal_id, rows in tracks.items():
        grp, _, coarse = rows[0][1].partition('|')
        cap = MAX_KM_PER_DAY.get(grp, 300)
        out = [rows[0]]
        for r in rows[1:]:
            prev = out[-1]
            days = max((r[2] - prev[2]).total_seconds() / 86400, 0.25)
            if r[5] == "fix" and haversine_km(prev[3], prev[4], r[3], r[4]) / days > cap:
                dropped += 1
                continue
            out.append(r)
        fast = 0
        for a, b in itertools.pairwise(out):
            days = max((b[2] - a[2]).total_seconds() / 86400, 0.25)
            fast += haversine_km(a[3], a[4], b[3], b[4]) / days > FAST_KM_PER_DAY
        steps = max(len(out) - 1, 1)
        flags.append((animal_id, bool(coarse) or fast / steps > NOISY_SHARE, len(out)))
        kept.extend((r[0], r[2], r[3], r[4], r[5], r[6]) for r in out)

    replace_table(con, "display_positions", ["animal_id", "t", "lon", "lat", "kind", "err_km"], kept)
    replace_table(con, "animal_qc", ["animal_id", "noisy", "n_days"], flags)
    noisy = sum(f[1] for f in flags)
    log(f"qc: {len(kept):,} daily positions; {dropped:,} implausible fixes dropped; {noisy} noisy tracks")
