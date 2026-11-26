"""Resolve every species label to a GBIF taxon, a display name, a group and an IUCN status.

- Taxonomy: GBIF Backbone (species/match), which covers land and marine animals.
- Common name and IUCN status: Wikidata, looked up by GBIF taxon ID (P846): the English
  Wikipedia article title is used as the common name; P141 gives the Red List status.

Writes table `species` and `stg_animal_species(animal_id, taxon_key, source_name)`.
Lookups are cached in data/raw/species_cache.json, so reruns are offline.
"""
from __future__ import annotations

import json
import re
from collections import Counter

import yaml

from .util import CONFIG, RAW, http_json, log

CACHE = RAW / "species_cache.json"
GBIF_MATCH = "https://api.gbif.org/v1/species/match"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
SENSITIVE_STATUSES = {"VU", "EN", "CR"}
IUCN_CODES = {"least concern": "LC", "near threatened": "NT", "vulnerable": "VU", "endangered": "EN",
              "critically endangered": "CR", "data deficient": "DD", "extinct in the wild": "EW"}
SEAL_FAMILIES = {"Phocidae", "Otariidae", "Odobenidae"}
WHALE_FAMILIES = {"Balaenopteridae", "Balaenidae", "Physeteridae", "Kogiidae", "Delphinidae", "Phocoenidae",
                  "Monodontidae", "Ziphiidae", "Eschrichtiidae", "Neobalaenidae", "Platanistidae",
                  "Iniidae", "Pontoporiidae", "Lipotidae", "Trichechidae", "Dugongidae"}
SEA_TURTLE_FAMILIES = {"Cheloniidae", "Dermochelyidae"}


def group_of(rec: dict) -> str:
    cls, order, family = rec.get("class"), rec.get("order"), rec.get("family")
    if cls in ("Actinopterygii", "Actinopteri", "Teleostei", "Sarcopterygii"):
        return "fish"
    # GBIF's backbone leaves class empty for ray-finned fish; every other vertebrate has one.
    if cls is None and (order or "").endswith("iformes"):
        return "fish"
    if cls in ("Elasmobranchii", "Chondrichthyes"):
        return "shark"
    if cls == "Aves":
        return "bird"
    if cls == "Mammalia":
        if family in SEAL_FAMILIES:
            return "seal"
        if family in WHALE_FAMILIES:
            return "whale"
        if order == "Chiroptera":
            return "bat"
        return "mammal"
    if family in SEA_TURTLE_FAMILIES:
        return "turtle"
    if cls in ("Reptilia", "Testudines", "Squamata", "Crocodylia") or order == "Testudines":
        return "reptile"
    if cls == "Amphibia":
        return "amphibian"
    if cls in ("Insecta", "Arachnida"):
        return "insect"
    return "other"


def _gbif(name: str) -> dict | None:
    try:
        m = http_json(GBIF_MATCH, {"name": name, "kingdom": "Animalia"})
    except OSError:
        return None
    if m.get("matchType") == "NONE" or not m.get("speciesKey"):
        return None
    # speciesKey is the accepted species (subspecies and synonyms collapse to it).
    return {"key": m["speciesKey"], "scientificname": m.get("species") or m.get("canonicalName"),
            "class": m.get("class"), "order": m.get("order"), "family": m.get("family"),
            "match": m.get("matchType")}


def _wikidata(keys: list[int]) -> dict[str, dict]:
    """GBIF species key -> {title: English Wikipedia article, iucn: Red List code}."""
    out: dict[str, dict] = {}
    for i in range(0, len(keys), 150):
        values = " ".join(f'"{k}"' for k in keys[i:i + 150])
        query = f"""
        SELECT ?gbif ?title ?statusLabel ?rank WHERE {{
          VALUES ?gbif {{ {values} }}
          ?item wdt:P846 ?gbif .
          OPTIONAL {{ ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> ; schema:name ?title . }}
          OPTIONAL {{ ?item p:P141 ?st . ?st ps:P141 ?status ; wikibase:rank ?rank . }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
        }}"""
        res = http_json(WIKIDATA_SPARQL, {"query": query, "format": "json"}, timeout=180)
        for b in res["results"]["bindings"]:
            rec = out.setdefault(b["gbif"]["value"], {"title": None, "iucn": None, "_score": 0})
            if "title" in b and not rec["title"]:
                rec["title"] = b["title"]["value"]
            if "statusLabel" in b:
                code = IUCN_CODES.get(b["statusLabel"]["value"].lower())
                score = 2 if b["rank"]["value"].endswith("PreferredRank") else 1
                if code and score > rec["_score"]:
                    rec["iucn"], rec["_score"] = code, score
    return out


def _clean_label(label: str | None) -> str:
    label = re.sub(r"\s+", " ", (label or "").strip().strip('"').strip())
    return re.sub(r"^juvenile ", "", label, flags=re.IGNORECASE)


def _common_from_title(title: str | None, sci: str) -> str | None:
    if not title:
        return None
    t = re.sub(r"\s*\(.*?\)$", "", title).strip()
    return None if t.lower() == sci.lower() else t[0].upper() + t[1:]


def build(con, offline: bool = False) -> None:
    cfg = yaml.safe_load((CONFIG / "species_overrides.yaml").read_text())
    by_label = {k.lower(): v for k, v in (cfg.get("label_to_scientific") or {}).items()}
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    for k in ("gbif", "wikidata"):
        cache.setdefault(k, {})

    sci_of: dict[str, str] = {}
    labels: dict[str, Counter] = {}
    for animal_id, label, sci in con.execute("SELECT animal_id, species_label, scientific_name FROM stg_animal").fetchall():
        clean = _clean_label(label)
        name = by_label.get(clean.lower()) or (sci or "").strip().strip('"').strip() or None
        if not name:
            raise SystemExit(f"No scientific name for label {label!r} ({animal_id}); add it to species_overrides.yaml")
        sci_of[animal_id] = name
        counter = labels.setdefault(name, Counter())
        if clean and clean.lower() != name.lower():  # the source gave a common name
            counter[clean[:1].upper() + clean[1:].lower()] += 1

    missing = [n for n in labels if n not in cache["gbif"]]
    if missing and offline:
        raise SystemExit(f"Offline but {len(missing)} species not cached, e.g. {missing[:5]}")
    for n in missing:
        cache["gbif"][n] = _gbif(n)
    unresolved = sorted(n for n in labels if not cache["gbif"].get(n))
    if unresolved:
        log(f"species: GBIF could not resolve {len(unresolved)} names (animals skipped): {', '.join(unresolved[:12])}")

    keys = sorted({cache["gbif"][n]["key"] for n in labels if cache["gbif"].get(n)})
    if not offline and any(str(k) not in cache["wikidata"] for k in keys):
        found = _wikidata(keys)
        for k in keys:
            cache["wikidata"][str(k)] = {kk: vv for kk, vv in found.get(str(k), {}).items() if kk != "_score"}
    CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True))

    sens_override = (yaml.safe_load((CONFIG / "sensitivity.yaml").read_text()) or {}).get("override") or {}
    common_override = cfg.get("common_name") or {}
    species_rows: dict[int, tuple] = {}
    for name, counter in labels.items():
        rec = cache["gbif"].get(name)
        if not rec or rec["key"] in species_rows:
            continue
        sci = rec["scientificname"]
        wd = cache["wikidata"].get(str(rec["key"]), {})
        status = wd.get("iucn")
        sensitive = sens_override.get(sci, sens_override.get(name, status in SENSITIVE_STATUSES))
        common = (common_override.get(sci) or common_override.get(name)
                  or (counter.most_common(1)[0][0] if counter else None)
                  or _common_from_title(wd.get("title"), sci) or sci)
        species_rows[rec["key"]] = (rec["key"], sci, common, group_of(rec), status, bool(sensitive),
                                    rec.get("family"), wd.get("title"))
    con.execute("""CREATE OR REPLACE TABLE species (taxon_key INTEGER, scientific_name VARCHAR, common_name VARCHAR,
                   grp VARCHAR, iucn_status VARCHAR, sensitive BOOLEAN, family VARCHAR, wiki_title VARCHAR)""")
    con.executemany("INSERT INTO species VALUES (?, ?, ?, ?, ?, ?, ?, ?)", list(species_rows.values()))
    con.execute("CREATE OR REPLACE TABLE stg_animal_species (animal_id VARCHAR, taxon_key INTEGER, source_name VARCHAR)")
    con.executemany("INSERT INTO stg_animal_species VALUES (?, ?, ?)",
                    [(a, cache["gbif"][n]["key"], n) for a, n in sci_of.items() if cache["gbif"].get(n)])
    groups = Counter(r[3] for r in species_rows.values())
    log(f"species: {len(species_rows)} resolved; groups {dict(groups)}; "
        f"{sum(1 for r in species_rows.values() if not r[4])} without IUCN status")
