"""Deterministic facts per animal, each with evidence. Never written by a model.

Table `facts(animal_id, fact_id, value, display, evidence)`; value/evidence are JSON strings.
Table `animal_stats(animal_id, ..., score)` for ranking and the index.
"""
import itertools
import json
from collections import defaultdict
from datetime import datetime

from .util import haversine_km, log, replace_table


def fmt_date(t: datetime) -> str:
    return f"{t:%b} {t.day}, {t.year}"


def fmt_km(km: float) -> str:
    return f"{round(km):,} km" if km < 1000 else f"{round(km, -1):,.0f} km"


PROPER = {"Pacific", "Atlantic", "Galapagos", "California", "Weddell", "Blainville's"}


def in_sentence(common: str) -> str:
    """Species common name for mid-sentence use: lowercase, except proper nouns."""
    return " ".join(w if w in PROPER else w.lower() for w in common.split())


def _percentiles(values: dict) -> dict:
    order = sorted(values, key=values.get)
    n = max(len(order) - 1, 1)
    return {k: i / n for i, k in enumerate(order)}


def build(con) -> None:
    meta = {r[0]: r for r in con.execute("""
        SELECT a.animal_id, a.taxon_key, s.common_name, s.grp, a.sex, a.stage, a.site, q.noisy
        FROM published_animals a JOIN species s USING (taxon_key) JOIN animal_qc q USING (animal_id)
    """).fetchall()}
    tracks = defaultdict(list)
    for animal_id, t, lon, lat in con.execute(
            "SELECT animal_id, t, lon, lat FROM published_positions ORDER BY animal_id, t").fetchall():
        tracks[animal_id].append((t, lon, lat))
    dives = {r[0]: r[1:] for r in con.execute("""
        SELECT animal_id, arg_max(t, max_depth_m), max(max_depth_m), arg_max(t, max_dive_min), max(max_dive_min)
        FROM published_dives GROUP BY animal_id
    """).fetchall()}

    facts, stats = [], {}
    for animal_id, pts in tracks.items():
        _, taxon, common, grp, sex, stage, site, noisy = meta[animal_id]
        first, last = pts[0], pts[-1]
        steps = [haversine_km(a[1], a[2], b[1], b[2]) for a, b in itertools.pairwise(pts)]
        path_km = sum(steps)
        disp = [(haversine_km(first[1], first[2], p[1], p[2]), p) for p in pts]
        max_disp, far = max(disp, key=lambda x: x[0])
        gaps = [((b[0] - a[0]).total_seconds() / 86400, a, b) for a, b in itertools.pairwise(pts)]
        gap_days, gap_a, gap_b = max(gaps, key=lambda g: g[0])
        days = (last[0] - first[0]).total_seconds() / 86400

        def add(fid, value, display, evidence, animal_id=animal_id):
            facts.append((animal_id, fid, json.dumps(value), display, json.dumps(evidence, default=str)))

        add("first_date", first[0].date().isoformat(), fmt_date(first[0]), {"position": first[0]})
        add("last_date", last[0].date().isoformat(), fmt_date(last[0]), {"position": last[0]})
        add("days_tracked", round(days), f"{round(days)} days", {"from": first[0], "to": last[0]})
        add("path_km", round(path_km), fmt_km(path_km),
            {"method": "sum of great-circle distances between daily positions", "positions": len(pts),
             "may_overstate": bool(noisy)})
        add("max_displacement_km", round(max_disp), fmt_km(max_disp),
            {"from": first[0], "to": far[0], "method": "great-circle distance from first position"})
        add("farthest_date", far[0].date().isoformat(), fmt_date(far[0]), {"position": far[0]})
        add("northernmost_lat", round(max(p[2] for p in pts), 1), f"{abs(max(p[2] for p in pts)):.1f}°"
            + ("N" if max(p[2] for p in pts) >= 0 else "S"), {"method": "max latitude of published positions"})
        add("southernmost_lat", round(min(p[2] for p in pts), 1), f"{abs(min(p[2] for p in pts)):.1f}°"
            + ("N" if min(p[2] for p in pts) >= 0 else "S"), {"method": "min latitude of published positions"})
        if gap_days >= 5:
            add("longest_gap_days", round(gap_days), f"{round(gap_days)} days",
                {"from": gap_a[0], "to": gap_b[0]})
        if site:
            add("release_site", site, site, {"field": "release_site"})
        if sex:
            add("sex", sex, sex, {"field": "sex"})
        if stage:
            add("stage", stage, stage, {"field": "life stage at tagging"})
        if animal_id in dives and dives[animal_id][1]:
            t_d, depth, t_m, minutes = dives[animal_id]
            add("deepest_dive_m", round(depth), f"{round(depth):,} m", {"dive_summary": t_d})
            if minutes:
                add("longest_dive_min", round(minutes), f"{round(minutes)} minutes", {"dive_summary": t_m})
        stats[animal_id] = {"taxon": taxon, "common": common, "grp": grp, "days": days, "path_km": path_km,
                                "max_disp": max_disp, "noisy": bool(noisy), "rich": int(bool(sex)) + int(bool(stage))
                                + int(animal_id in dives), "first": first[0], "last": last[0]}

    # Catalog-relative records (non-noisy tracks only).
    by_species = defaultdict(list)
    for a, s in stats.items():
        if not s["noisy"]:
            by_species[s["taxon"]].append(a)
    for taxon, ids in by_species.items():
        if len(ids) < 5:
            continue
        for key, fid in (("days", "species_rank_days"), ("max_disp", "species_rank_distance")):
            ranked = sorted(ids, key=lambda a: -stats[a][key])
            for rank, a in enumerate(ranked[:3], 1):
                label = {1: "the longest", 2: "the second longest", 3: "the third longest"}[rank]
                what = "track" if key == "days" else "distance from its starting point"
                facts.append((a, fid, json.dumps({"rank": rank, "of": len(ids)}),
                              f"{label} {what} among the {len(ids)} {in_sentence(stats[a]['common'])} tracks on this map",
                              json.dumps({"compared_with": len(ids), "metric": key})))

    # Storyworthiness: long, far-ranging, well-described tracks; rarer species get a nudge.
    p_days = _percentiles({a: s["days"] for a, s in stats.items()})
    p_disp = _percentiles({a: s["max_disp"] for a, s in stats.items()})
    species_n = defaultdict(int)
    for s in stats.values():
        species_n[s["taxon"]] += 1
    rows = []
    for a, s in stats.items():
        rarity = 1 / (1 + species_n[s["taxon"]] / 20)
        score = 0.35 * p_days[a] + 0.35 * p_disp[a] + 0.05 * s["rich"] + 0.15 * rarity
        if s["noisy"]:
            score *= 0.5
        rows.append((a, s["taxon"], s["grp"], round(s["days"]), round(s["path_km"]), round(s["max_disp"]),
                     s["noisy"], s["first"], s["last"], round(score, 4)))

    replace_table(con, "facts", ["animal_id", "fact_id", "value", "display", "evidence"], facts)
    replace_table(con, "animal_stats", ["animal_id", "taxon_key", "grp", "days", "path_km", "max_disp_km",
                                        "noisy", "first_t", "last_t", "score"], rows)
    log(f"derive: {len(facts):,} facts for {len(stats)} animals")
