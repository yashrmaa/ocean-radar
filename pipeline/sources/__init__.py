"""Source adapters.

Each adapter exposes:
  SOURCE_ID: str
  fetch(refresh: bool) -> None          download raw files into data/raw/<SOURCE_ID>/
  load(con) -> None                     insert rows into the staging tables below

Staging tables (created by pipeline.build):
  stg_animal(animal_id, source_id, native_id, program, species_label, scientific_name,
             sex, stage, site, citation, dataset_url)
  stg_position(animal_id, t TIMESTAMP, lon, lat, kind, err_km)
     kind: 'fix' (an observed location) or 'modeled' (state-space model estimate)
  stg_dive(animal_id, t, max_depth_m, n_dives, max_dive_min)
"""
from . import atn_deployments, atn_topp, imos, movebank

ALL = [atn_topp, atn_deployments, imos, movebank]
