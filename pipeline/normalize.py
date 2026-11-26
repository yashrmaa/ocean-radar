"""Staging tables -> `animals`, `positions`, `dives`, with vocabularies and duplicates fixed."""
from .util import log

SEX = {"m": "male", "male": "male", "f": "female", "female": "female"}
STAGE = {"juvenille": "juvenile", "subadult": "subadult", "sub-adult": "subadult", "adult": "adult",
         "juvenile": "juvenile", "pup": "pup", "yearling": "yearling", "weaner": "weaner"}


def _map_sql(col: str, mapping: dict) -> str:
    cases = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in mapping.items())
    return f"CASE lower(trim({col})) {cases} ELSE NULL END"


def build(con) -> None:
    con.execute(f"""
        CREATE OR REPLACE TABLE animals AS
        SELECT a.animal_id, a.source_id, a.native_id, a.program, s.taxon_key,
               {_map_sql('a.sex', SEX)} AS sex,
               coalesce({_map_sql('a.stage', STAGE)},
                        CASE WHEN lower(a.species_label) LIKE 'juvenile %' THEN 'juvenile' END) AS stage,
               nullif(trim(a.site), '') AS site, a.citation, a.dataset_url, a.license, a.license_url,
               coalesce(a.coarse, false) AS coarse,
               CAST(NULL AS VARCHAR) AS same_as
        FROM stg_animal a JOIN stg_animal_species s USING (animal_id)
    """)
    con.execute("""
        CREATE OR REPLACE TABLE positions AS
        SELECT animal_id, t, any_value(lon) AS lon, any_value(lat) AS lat, any_value(kind) AS kind,
               min(err_km) AS err_km
        FROM stg_position
        WHERE lat BETWEEN -90 AND 90 AND lon BETWEEN -540 AND 540
        GROUP BY animal_id, t
    """)
    con.execute("UPDATE positions SET lon = ((lon + 180) % 360 + 360) % 360 - 180")

    # The same tag can appear in Stanford TOPP and as an ATN deployment. Treat two animals as the
    # same when species match, tracks start within 3 days, and first positions are within 100 km;
    # keep the deployment (it has sex/stage) and drop the TOPP copy.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _starts AS
        SELECT p.animal_id, a.source_id, a.taxon_key, p.t, p.lon, p.lat
        FROM positions p JOIN animals a USING (animal_id)
        QUALIFY row_number() OVER (PARTITION BY p.animal_id ORDER BY p.t) = 1
    """)
    dupes = con.execute("""
        SELECT x.animal_id, y.animal_id
        FROM _starts x JOIN _starts y ON x.taxon_key = y.taxon_key
         AND x.source_id = 'atn_topp' AND y.source_id = 'atn_deployments'
         AND abs(epoch(x.t) - epoch(y.t)) <= 3 * 86400
         AND 2 * 6371 * asin(sqrt(sin(radians(y.lat - x.lat) / 2)^2
             + cos(radians(x.lat)) * cos(radians(y.lat)) * sin(radians(y.lon - x.lon) / 2)^2)) <= 100
    """).fetchall()
    for topp_id, dep_id in dupes:
        con.execute("UPDATE animals SET same_as = ? WHERE animal_id = ?", [topp_id, dep_id])
    if dupes:
        ids = [d[0] for d in dupes]
        con.execute("DELETE FROM animals WHERE list_contains(?, animal_id)", [ids])
        con.execute("DELETE FROM positions WHERE list_contains(?, animal_id)", [ids])

    con.execute("""
        CREATE OR REPLACE TABLE dives AS
        SELECT d.* FROM stg_dive d SEMI JOIN animals a USING (animal_id)
    """)
    n_a, n_p = con.execute("SELECT (SELECT count(*) FROM animals), (SELECT count(*) FROM positions)").fetchone()
    log(f"normalize: {n_a} animals, {n_p:,} positions; {len(dupes)} TOPP/deployment duplicates merged")
