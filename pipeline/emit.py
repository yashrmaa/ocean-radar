"""Write the static assets the web app loads (web/public/data/).

  index.json            catalog: sources, species, photos, one compact row per animal
  chunks/YYYY.json      per-year header: [[animal index, start, length], ...]
  chunks/YYYY.bin       per-year Float32 columns: lon[n] | lat[n] | day[n]   (days since EPOCH)
  animals/NNN.json      detail shards of SHARD_SIZE animals: facts, text, track, sources
  photos/*              species photo thumbnails

Longitudes are unwrapped per animal (continuous across ±180°) so paths never jump across the map.
Each year's chunk also carries the last position before that year, so trails stay continuous.
"""
import json
import shutil
from array import array
from collections import defaultdict
from datetime import datetime

from .policy import load_config
from .util import EPOCH, RAW, WEB_DATA, log, to_days

SHARD_SIZE = 100
KIND = {"fix": 0, "modeled": 1}


def _unwrap(points: list[tuple]) -> list[tuple]:
    out, prev = [], None
    for t, lon, lat, *rest in points:
        if prev is not None:
            while lon - prev > 180:
                lon -= 360
            while lon - prev < -180:
                lon += 360
        prev = lon
        out.append((t, lon, lat, *rest))
    return out


def _write_json(path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str))


def build(con, now: datetime) -> None:
    if WEB_DATA.exists():
        shutil.rmtree(WEB_DATA)
    sources_cfg, sens = load_config()

    animals = con.execute("""
        SELECT a.animal_id, a.aphia_id, a.sex, a.stage, a.source_id, a.program, a.site, a.citation,
               a.dataset_url, st.noisy, st.score, st.first_t, st.last_t, c.summary, c.story
        FROM published_animals a JOIN animal_stats st USING (animal_id) JOIN card_text c USING (animal_id)
        ORDER BY st.score DESC
    """).fetchall()
    idx = {a[0]: i for i, a in enumerate(animals)}

    tracks = defaultdict(list)
    for animal_id, t, lon, lat, kind, err, coarse in con.execute("""
            SELECT animal_id, t, lon, lat, kind, err_km, coarsened FROM published_positions
            ORDER BY animal_id, t""").fetchall():
        tracks[animal_id].append((t, lon, lat, kind, err, coarse))
    tracks = {a: _unwrap(p) for a, p in tracks.items()}

    facts = defaultdict(dict)
    for animal_id, fid, value, display, evidence in con.execute(
            "SELECT animal_id, fact_id, value, display, evidence FROM facts").fetchall():
        facts[animal_id][fid] = {"v": json.loads(value), "d": display, "e": json.loads(evidence)}

    # --- per-year chunks ---
    by_year = defaultdict(list)  # year -> [(animal index, [points])]
    for animal_id, pts in tracks.items():
        years = sorted({p[0].year for p in pts})
        for y in years:
            inside = [p for p in pts if p[0].year == y]
            before = [p for p in pts if p[0].year < y]
            seg = ([before[-1]] if before else []) + inside
            by_year[y].append((idx[animal_id], seg))
    chunk_years = []
    for y, entries in sorted(by_year.items()):
        lon, lat, day, header = array("f"), array("f"), array("f"), []
        for i, seg in sorted(entries):
            header.append([i, len(day), len(seg)])
            for t, x, yy, *_ in seg:
                lon.append(x)
                lat.append(yy)
                day.append(to_days(t))
        (WEB_DATA / "chunks").mkdir(parents=True, exist_ok=True)
        with open(WEB_DATA / "chunks" / f"{y}.bin", "wb") as f:
            lon.tofile(f)
            lat.tofile(f)
            day.tofile(f)
        _write_json(WEB_DATA / "chunks" / f"{y}.json", {"year": y, "n": len(day), "animals": header})
        chunk_years.append(y)

    # --- detail shards ---
    for s in range(0, len(animals), SHARD_SIZE):
        shard = {}
        for a in animals[s:s + SHARD_SIZE]:
            animal_id = a[0]
            shard[animal_id] = {
                "facts": facts[animal_id], "summary": a[13], "story": json.loads(a[14]) if a[14] else None,
                "program": a[5], "citation": a[7], "dataset_url": a[8],
                "track": [[round(to_days(t), 3), round(x, 4), round(y, 4), KIND.get(k, 1),
                           None if e is None else round(e, 2), int(bool(c))]
                          for t, x, y, k, e, c in tracks[animal_id]],
            }
        _write_json(WEB_DATA / "animals" / f"{s // SHARD_SIZE:03d}.json", shard)

    # --- photos ---
    photos = defaultdict(dict)
    (WEB_DATA / "photos").mkdir(parents=True, exist_ok=True)
    for aphia, variant, thumb, file_url, credit, lic, lic_url, wiki in con.execute(
            "SELECT * FROM photos").fetchall():
        src = RAW / "photos" / thumb
        if src.exists():
            shutil.copy(src, WEB_DATA / "photos" / thumb)
            photos[aphia][variant] = {"src": f"photos/{thumb}", "file": file_url, "credit": credit,
                                      "license": lic, "license_url": lic_url, "wiki": wiki}

    species = {r[0]: {"common": r[1], "sci": r[2], "grp": r[3], "iucn": r[4], "sensitive": r[5]}
               for r in con.execute("""SELECT aphia_id, common_name, scientific_name, grp, iucn_status, sensitive
                                       FROM species WHERE aphia_id IN (SELECT aphia_id FROM published_animals)""").fetchall()}
    data_end = max(a[12] for a in animals)
    index = {
        "generated": now.isoformat(timespec="seconds"),
        "epoch": EPOCH.date().isoformat(),
        "data_start": min(a[11] for a in animals).date().isoformat(),
        "data_end": data_end.date().isoformat(),
        "embargo_days": sens["embargo_days"],
        "years": chunk_years,
        "sources": {k: {f: v.get(f) for f in ("name", "url", "license", "license_url")}
                    for k, v in sources_cfg.items() if v.get("enabled", True)},
        "species": species,
        "photos": photos,
        # One row per animal, ordered by storyworthiness (index = position in this list).
        "animals": [{"id": a[0], "sp": a[1], "sex": a[2], "stage": a[3], "src": a[4], "site": a[6],
                     "s": round(to_days(a[11]), 2), "e": round(to_days(a[12]), 2),
                     "shard": i // SHARD_SIZE, "noisy": bool(a[9]), "story": a[14] is not None,
                     "ll": [round(((tracks[a[0]][-1][1] + 180) % 360) - 180, 3), round(tracks[a[0]][-1][2], 3)]}
                    for i, a in enumerate(animals)],
    }
    _write_json(WEB_DATA / "index.json", index)
    total = sum(p.stat().st_size for p in WEB_DATA.rglob("*") if p.is_file())
    biggest = max((p for p in WEB_DATA.rglob("*") if p.is_file()), key=lambda p: p.stat().st_size)
    log(f"emit: {len(animals)} animals, {len(species)} species, years {chunk_years[0]}-{chunk_years[-1]}; "
        f"{total / 1e6:.1f} MB total, largest {biggest.name} {biggest.stat().st_size / 1e6:.1f} MB")
