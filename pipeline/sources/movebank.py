"""Movebank Data Repository: published tracking datasets (birds, land mammals, bats, reptiles...).

Each dataset has one or more location CSVs (sometimes zipped) plus a reference CSV describing the
animals (sex, life stage, study site). Datasets are immutable once published, so each one is
downloaded once, shrunk to one position per animal per day (first fix of the day), and saved as a
small Parquet file in data/raw/movebank/daily/. The large raw files are then deleted.
"""
import json
import re
import shutil
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
import yaml

from ..util import CONFIG, RAW, USER_AGENT, cached_download, log

SOURCE_ID = "movebank"
API = "https://datarepository.movebank.org/server/api"
DIR = RAW / SOURCE_ID
DAILY = DIR / "daily"
INVENTORY = DIR / "inventory.json"


def _cfg() -> dict:
    return yaml.safe_load((CONFIG / "movebank.yaml").read_text())


def _get_json(url: str) -> dict:
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception:
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _inventory(refresh: bool) -> list[dict]:
    """All "Data from:" items with license, species, citation and file list (cached)."""
    if INVENTORY.exists() and not refresh:
        return json.loads(INVENTORY.read_text())
    items, page = [], 0
    while True:
        d = _get_json(f"{API}/discover/search/objects?dsoType=ITEM&size=100&page={page}&query=%22Data%20from%22")
        s = d["_embedded"]["searchResult"]
        for o in s["_embedded"]["objects"]:
            it = o["_embedded"]["indexableObject"]
            m = it["metadata"]
            def v(k, m=m):
                return [x["value"] for x in m.get(k, [])]
            items.append({"uuid": it["uuid"], "name": it["name"], "rights": (v("dc.rights") or [None])[0],
                          "rights_uri": (v("dc.rights.uri") or [None])[0],
                          "handle": (v("dc.identifier.uri") or [None])[0],
                          "doi": [x for x in v("dc.identifier") if "doi" in x.lower()][:1],
                          "species": v("dwc.ScientificName"),
                          "citation": (v("dc.identifier.citation") or [None])[0],
                          "issued": (v("dc.date.issued") or [None])[0]})
        if page + 1 >= s["page"]["totalPages"]:
            break
        page += 1
    for it in items:
        bundles = _get_json(f"{API}/core/items/{it['uuid']}/bundles")["_embedded"]["bundles"]
        orig = [b for b in bundles if b["name"] == "ORIGINAL"]
        it["files"] = []
        if orig:
            bs = _get_json(orig[0]["_links"]["bitstreams"]["href"] + "?size=100")["_embedded"]["bitstreams"]
            it["files"] = [{"name": b["name"], "size": b["sizeBytes"], "url": b["_links"]["content"]["href"]}
                           for b in bs]
        time.sleep(0.2)
    DIR.mkdir(parents=True, exist_ok=True)
    INVENTORY.write_text(json.dumps(items, indent=1))
    return items


def dataset_id(it: dict) -> str:
    """Stable short id from the handle, e.g. 'move.240'."""
    return (it.get("handle") or it["uuid"]).rstrip("/").rsplit("/", 1)[-1]


def _is_data(f: dict) -> bool:
    n = f["name"].lower()
    return n.endswith((".csv", ".zip")) and "reference" not in n and not n.startswith("readme")


def _is_reference(f: dict) -> bool:
    n = f["name"].lower()
    return n.endswith(".csv") and "reference" in n


def selected(items: list[dict]) -> list[dict]:
    cfg = _cfg()
    exclude = {s.lower() for s in cfg["exclude_species"]}
    out = []
    for it in items:
        if it["rights"] not in cfg["licenses"]:
            continue
        size = sum(f["size"] for f in it["files"] if _is_data(f))
        if not size or size > cfg["max_dataset_mb"] * 1e6:
            continue
        if it["species"] and all(s.lower() in exclude for s in it["species"]):
            continue
        out.append(it)
    return out


def _csv_sources(path: Path, work: Path) -> list[Path]:
    """CSV files to read for one downloaded file (unzipping if needed)."""
    if path.suffix.lower() != ".zip":
        return [path]
    out = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            low = name.lower()
            if low.endswith(".csv") and "reference" not in low and not low.startswith("__macosx"):
                target = work / re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)
                with z.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                out.append(target)
    return out


def _shrink(it: dict, refresh: bool) -> None:
    """Download one dataset and write daily/<id>.parquet (+ <id>.reference.json)."""
    did = dataset_id(it)
    out = DAILY / f"{did}.parquet"
    if out.exists() and not refresh:
        return
    work = DIR / "work" / did
    work.mkdir(parents=True, exist_ok=True)
    try:
        csvs = []
        for f in it["files"]:
            if _is_data(f):
                p = cached_download(f["url"], work / re.sub(r"[^A-Za-z0-9._-]", "_", f["name"]))
                csvs += _csv_sources(p, work)
            elif _is_reference(f):
                p = cached_download(f["url"], work / "reference.csv")
        con = duckdb.connect()
        parts = []
        for c in csvs:
            cols = {r[0] for r in con.execute(
                "DESCRIBE SELECT * FROM read_csv(?, all_varchar=true, sample_size=2000)", [str(c)]).fetchall()}
            if not {"timestamp", "location-long", "location-lat"} <= cols:
                continue  # e.g. accelerometer-only files
            id_col = next((c for c in ("individual-local-identifier", "tag-local-identifier", "animal-id",
                                       "individual-id", "tag-id") if c in cols), None)
            if not id_col:
                continue
            ind = f'"{id_col}"'
            taxon = '"individual-taxon-canonical-name"' if "individual-taxon-canonical-name" in cols else "NULL"
            sensor = '"sensor-type"' if "sensor-type" in cols else "NULL"
            visible = """AND coalesce("visible", 'true') <> 'false'""" if "visible" in cols else ""
            parts.append(f"""
                SELECT {ind} AS ind, {taxon} AS taxon, {sensor} AS sensor,
                       try_cast("timestamp" AS TIMESTAMP) AS t,
                       try_cast("location-long" AS DOUBLE) AS lon, try_cast("location-lat" AS DOUBLE) AS lat
                FROM read_csv('{c}', all_varchar=true)
                WHERE "location-long" IS NOT NULL AND "location-long" <> '' {visible}""")
        if parts:
            con.execute(f"""
                COPY (
                  SELECT ind, any_value(taxon) AS taxon, mode(sensor) AS sensor,
                         min(t) AS t, arg_min(lon, t) AS lon, arg_min(lat, t) AS lat, count(*) AS n_fixes
                  FROM ({' UNION ALL '.join(parts)})
                  WHERE t IS NOT NULL AND lon BETWEEN -180 AND 180 AND lat BETWEEN -90 AND 90
                    AND NOT (lon = 0 AND lat = 0)
                  GROUP BY ind, date_trunc('day', t)
                ) TO '{out}' (FORMAT parquet)""")
        else:
            con.execute(f"COPY (SELECT NULL::VARCHAR ind LIMIT 0) TO '{out}' (FORMAT parquet)")
        ref = work / "reference.csv"
        refs = []
        if ref.exists():
            cur = con.execute(f"SELECT * FROM read_csv('{ref}', all_varchar=true)")
            names = [d[0] for d in cur.description]
            refs = [dict(zip(names, row)) for row in cur.fetchall()]
        (DAILY / f"{did}.reference.json").write_text(json.dumps(refs))
        con.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def fetch(refresh: bool) -> None:
    items = selected(_inventory(refresh))
    DAILY.mkdir(parents=True, exist_ok=True)
    # Published datasets never change: --refresh re-reads the catalogue (new datasets) but doesn't
    # re-download datasets already shrunk.
    todo = [it for it in items if not (DAILY / f"{dataset_id(it)}.parquet").exists()]
    log(f"{SOURCE_ID}: {len(items)} datasets selected, {len(todo)} to download")
    failed = []

    def run(it):
        try:
            _shrink(it, False)
        except Exception as e:  # noqa: BLE001  (one bad dataset shouldn't stop the build)
            failed.append((dataset_id(it), str(e)[:200]))

    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, _ in enumerate(pool.map(run, todo), 1):
            if i % 25 == 0:
                log(f"  {i}/{len(todo)}")
    for did, err in failed:
        log(f"  skipped {did}: {err}")


def _title(it: dict) -> str:
    name = it["name"].removeprefix("Data from: ").strip()
    return name.removeprefix('Study "').rstrip('"') if name.startswith('Study "') else name


def load(con) -> None:
    cfg = _cfg()
    exclude = {s.lower() for s in cfg["exclude_species"]}
    coarse = set(cfg["coarse_sensors"])
    items = [it for it in selected(_inventory(False)) if (DAILY / f"{dataset_id(it)}.parquet").exists()]
    animal_rows = []
    for it in items:
        did = dataset_id(it)
        path = DAILY / f"{did}.parquet"
        refs = json.loads((DAILY / f"{did}.reference.json").read_text()) if (DAILY / f"{did}.reference.json").exists() else []
        ref_by = {}
        for r in refs:
            key = r.get("animal-id") or r.get("tag-id")
            if key and key not in ref_by:
                ref_by[key] = r
        cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()}
        if "lon" not in cols:  # dataset had no location files
            continue
        inds = con.execute(f"""SELECT ind, any_value(taxon), mode(sensor) FROM read_parquet('{path}')
                               WHERE ind IS NOT NULL GROUP BY ind""").fetchall()
        default_taxon = it["species"][0] if len(it["species"]) == 1 else None
        lic = cfg["licenses"][it["rights"]]
        url = (f"https://doi.org/{it['doi'][0].split('doi.org/')[-1]}" if it["doi"] else it["handle"])
        kept = []
        for ind, taxon, sensor in inds:
            r = ref_by.get(ind, {})
            taxon = taxon or r.get("animal-taxon") or default_taxon
            if not taxon or taxon.lower() in exclude:
                continue
            animal_id = f"movebank:{did}:{ind}"
            animal_rows.append((animal_id, SOURCE_ID, f"{did}:{ind}", _title(it), taxon, taxon,
                                r.get("animal-sex"), r.get("animal-life-stage"), r.get("study-site"),
                                it["citation"] or _title(it), url, lic, it["rights_uri"],
                                sensor in coarse))
            kept.append(ind)
        con.execute(f"""
            INSERT INTO stg_position
            SELECT 'movebank:{did}:' || ind, t, lon, lat, 'fix', NULL FROM read_parquet('{path}')
            WHERE list_contains(?, ind)
        """, [kept])
    con.executemany("""INSERT INTO stg_animal (animal_id, source_id, native_id, program, species_label,
                        scientific_name, sex, stage, site, citation, dataset_url, license, license_url, coarse)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", animal_rows)
    log(f"{SOURCE_ID}: {len(animal_rows)} animals from {len(items)} datasets")
