"""NOAA IOOS ATN: Stanford TOPP state-space-model tracks (daily, 2002-2017)."""
from ..util import RAW, cached_download, log

SOURCE_ID = "atn_topp"
BASE = "https://atn.ioos.us/erddap/tabledap/stanford_import_ssm_public"
DIR = RAW / SOURCE_ID


def fetch(refresh: bool) -> None:
    cached_download(BASE + ".csv?topp_id,time,latitude,longitude", DIR / "points.csv", refresh)
    cached_download(BASE + ".csv?topp_id,common_name,genus,species,taxon,workgroup&distinct()",
                    DIR / "animals.csv", refresh)


def load(con) -> None:
    # ERDDAP CSVs have a second header row of units, hence skip=2 with explicit names.
    con.execute(f"""
        INSERT INTO stg_animal
        SELECT DISTINCT 'atn_topp:' || topp_id, '{SOURCE_ID}', topp_id::VARCHAR, 'Stanford TOPP',
               common_name, nullif(trim(genus || ' ' || species), ''), NULL, NULL, NULL,
               'Tagging of Pacific Predators (TOPP), Stanford University, via U.S. IOOS Animal Telemetry Network',
               '{BASE}.html'
        FROM read_csv('{DIR / "animals.csv"}', skip=2, header=false, all_varchar=true,
                      names=['topp_id','common_name','genus','species','taxon','workgroup'])
    """)
    con.execute(f"""
        INSERT INTO stg_position
        SELECT 'atn_topp:' || topp_id, time::TIMESTAMP, longitude::DOUBLE, latitude::DOUBLE, 'modeled', NULL
        FROM read_csv('{DIR / "points.csv"}', skip=2, header=false, all_varchar=true,
                      names=['topp_id','time','latitude','longitude'])
        WHERE latitude <> 'NaN' AND longitude <> 'NaN'
    """)
    n = con.execute("SELECT count(*) FROM stg_animal WHERE source_id = ?", [SOURCE_ID]).fetchone()[0]
    log(f"{SOURCE_ID}: {n} animals")
