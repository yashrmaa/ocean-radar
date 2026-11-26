"""IMOS Animal Tracking Facility (AATAMS) satellite-relay tags via the AODN GeoServer.

Tracks: state-space-model (SSM) positions every 6 h with standard errors. The delayed-mode
QC layer ('dm') is preferred; near-real-time ('nrt') only fills in devices missing from dm.
Dives: 6-hourly dive summaries (max depth, number of dives).
"""
from ..util import RAW, cached_download, log

SOURCE_ID = "imos"
WFS = ("https://geoserver-123.aodn.org.au/geoserver/ows?service=WFS&version=1.0.0"
       "&request=GetFeature&outputFormat=csv&typeName=imos:{layer}&propertyName={props}")
DIR = RAW / SOURCE_ID
TRACK_PROPS = ("sattag_program,device_id,common_name,release_site,state_country,age_class,sex,"
               "timestamp,qc_ssm_lon,qc_ssm_lat,qc_ssm_x_se,qc_ssm_y_se")
DIVE_PROPS = "device_id,timestamp,maximum_depth,n_dives,max_divetime"
CITATION = ("Integrated Marine Observing System (IMOS) Animal Tracking Facility, "
            "AATAMS satellite relay tagging program, via the Australian Ocean Data Network")
PORTAL = "https://portal.aodn.org.au/"


def fetch(refresh: bool) -> None:
    for kind in ("dm", "nrt"):
        cached_download(WFS.format(layer=f"aatams_sattag_qc_{kind}_ssm_data", props=TRACK_PROPS),
                        DIR / f"ssm_{kind}.csv", refresh)
    cached_download(WFS.format(layer="aatams_sattag_qc_dm_summary_data", props=DIVE_PROPS),
                    DIR / "dives_dm.csv", refresh)


def load(con) -> None:
    con.execute(f"""
        CREATE TEMP TABLE _imos AS
        SELECT 'dm' AS layer, * FROM read_csv('{DIR / "ssm_dm.csv"}', header=true, all_varchar=true)
        UNION ALL BY NAME
        SELECT 'nrt' AS layer, * FROM read_csv('{DIR / "ssm_nrt.csv"}', header=true, all_varchar=true)
    """)
    # Keep dm for any device present in dm; nrt only for devices dm doesn't have.
    con.execute("""
        DELETE FROM _imos WHERE layer = 'nrt'
          AND device_id IN (SELECT DISTINCT device_id FROM _imos WHERE layer = 'dm')
    """)
    con.execute(f"""
        INSERT INTO stg_animal
        SELECT 'imos:' || device_id, '{SOURCE_ID}', device_id, 'IMOS AATAMS ' || any_value(sattag_program),
               mode(common_name), NULL, mode(sex), mode(age_class), mode(release_site),
               '{CITATION}', '{PORTAL}'
        FROM _imos GROUP BY device_id
    """)
    con.execute("""
        INSERT INTO stg_position
        SELECT 'imos:' || device_id, timestamp::TIMESTAMP, qc_ssm_lon::DOUBLE, qc_ssm_lat::DOUBLE, 'modeled',
               sqrt(coalesce(try_cast(qc_ssm_x_se AS DOUBLE), 0)^2 + coalesce(try_cast(qc_ssm_y_se AS DOUBLE), 0)^2)
        FROM _imos
        WHERE qc_ssm_lon IS NOT NULL AND qc_ssm_lat IS NOT NULL
    """)
    con.execute(f"""
        INSERT INTO stg_dive
        SELECT 'imos:' || device_id, timestamp::TIMESTAMP, try_cast(maximum_depth AS DOUBLE),
               try_cast(n_dives AS DOUBLE), try_cast(max_divetime AS DOUBLE) / 60.0
        FROM read_csv('{DIR / "dives_dm.csv"}', header=true, all_varchar=true)
        WHERE try_cast(maximum_depth AS DOUBLE) IS NOT NULL
    """)
    con.execute("DROP TABLE _imos")
    n = con.execute("SELECT count(*) FROM stg_animal WHERE source_id = ?", [SOURCE_ID]).fetchone()[0]
    log(f"{SOURCE_ID}: {n} animals")
