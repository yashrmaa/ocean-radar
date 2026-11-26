"""NOAA IOOS ATN: per-deployment Argos datasets (raw fixes with QARTOD flags, sex, life stage).

One ERDDAP dataset per tag deployment. We keep fixes that passed QARTOD (flag 1) with Argos
location class 3/2/1/0/A (class B is too imprecise to show).
"""
import csv
import json
from concurrent.futures import ThreadPoolExecutor

from ..util import RAW, cached_download, log

SOURCE_ID = "atn_deployments"
ERDDAP = "https://atn.ioos.us/erddap"
DIR = RAW / SOURCE_ID
COLUMNS = "time,latitude,longitude,type,location_class,error_radius,qartod_rollup_flag,animal_sex,animal_life_stage"
META_KEYS = ("animal_common_name", "animal_scientific_name", "animal_id", "citation", "creator_name",
             "institution", "deployment_start_datetime", "ptt_id", "license")
# Typical Argos error by location class (km), used when error_radius is missing.
CLASS_ERR_KM = {"3": 0.25, "2": 0.5, "1": 1.5, "0": 5.0, "A": 5.0}


def _dataset_ids(refresh: bool) -> list[str]:
    path = cached_download(f"{ERDDAP}/tabledap/allDatasets.csv?datasetID", DIR / "_datasets.csv", refresh)
    with open(path) as f:
        return [r[0] for r in csv.reader(f) if r and r[0].startswith("atn_") and "_trajectory_" in r[0]]


def _fetch_one(ds: str, refresh: bool) -> None:
    cached_download(f"{ERDDAP}/tabledap/{ds}.csv?{COLUMNS}", DIR / f"{ds}.csv", refresh)
    meta_path = DIR / f"{ds}.meta.json"
    if meta_path.exists() and not refresh:
        return
    info = cached_download(f"{ERDDAP}/info/{ds}/index.csv", DIR / f"{ds}.info.csv", refresh)
    meta = {}
    with open(info) as f:
        for row in csv.reader(f):
            if len(row) >= 5 and row[0] == "attribute" and row[1] == "NC_GLOBAL" and row[2] in META_KEYS:
                meta[row[2]] = row[4]
    meta_path.write_text(json.dumps(meta))


def fetch(refresh: bool) -> None:
    ids = _dataset_ids(refresh)
    log(f"{SOURCE_ID}: {len(ids)} deployment datasets")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda ds: _fetch_one(ds, refresh), ids))


def load(con) -> None:
    ids = _dataset_ids(False)
    animals = []
    for ds in ids:
        meta = json.loads((DIR / f"{ds}.meta.json").read_text())
        animals.append((f"{SOURCE_ID}:{ds}", ds, meta.get("institution") or meta.get("creator_name"),
                        meta.get("animal_common_name"), meta.get("animal_scientific_name"),
                        meta.get("citation"), f"{ERDDAP}/tabledap/{ds}.html"))
    con.executemany(f"""
        INSERT INTO stg_animal (animal_id, source_id, native_id, program, species_label, scientific_name, sex, stage, site, citation, dataset_url) VALUES (?, '{SOURCE_ID}', ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
    """, animals)

    files = [str(DIR / f"{ds}.csv") for ds in ids]
    con.execute("""
        CREATE TEMP TABLE _atn_raw AS
        SELECT regexp_extract(filename, '(atn_[^/]+)\\.csv$', 1) AS ds, *
        FROM read_csv(?, skip=2, header=false, all_varchar=true, filename=true, union_by_name=false,
                      names=['time','latitude','longitude','type','location_class','error_radius',
                             'qartod_rollup_flag','animal_sex','animal_life_stage'])
    """, [files])
    # Sex and life stage are repeated on every row; take the most common value per deployment.
    con.execute(f"""
        UPDATE stg_animal a SET sex = s.sex, stage = s.stage
        FROM (SELECT ds, mode(nullif(animal_sex, 'nan')) AS sex, mode(nullif(animal_life_stage, 'nan')) AS stage
              FROM _atn_raw GROUP BY ds) s
        WHERE a.animal_id = '{SOURCE_ID}:' || s.ds
    """)
    con.execute(f"""
        INSERT INTO stg_position
        SELECT '{SOURCE_ID}:' || ds, time::TIMESTAMP, longitude::DOUBLE, latitude::DOUBLE, 'fix',
               coalesce(try_cast(nullif(error_radius, 'NaN') AS DOUBLE) / 1000.0,
                        CASE location_class {' '.join(f"WHEN '{k}' THEN {v}" for k, v in CLASS_ERR_KM.items())} END)
        FROM _atn_raw
        WHERE type = 'Argos' AND qartod_rollup_flag = '1'
          AND location_class IN ({', '.join(repr(k) for k in CLASS_ERR_KM)})
          AND latitude <> 'NaN' AND longitude <> 'NaN'
    """)
    con.execute("DROP TABLE _atn_raw")
    log(f"{SOURCE_ID}: {len(animals)} animals")
