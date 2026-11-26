from datetime import datetime

from pipeline.emit import _unwrap
from pipeline.species import _group
from pipeline.stories import summary
from pipeline.util import haversine_km


def test_groups_from_taxonomy():
    assert _group({"class": "Elasmobranchii"}) == "shark"
    assert _group({"class": "Mammalia", "family": "Phocidae"}) == "seal"
    assert _group({"class": "Mammalia", "family": "Balaenopteridae"}) == "whale"
    assert _group({"class": "Reptilia", "order": "Testudines"}) == "turtle"
    assert _group({"class": "Actinopteri"}) == "fish"


def test_unwrap_keeps_paths_continuous_across_antimeridian():
    t = datetime(2020, 1, 1)
    pts = _unwrap([(t, 179.0, 0.0), (t, -179.0, 0.0), (t, -170.0, 0.0)])
    assert [p[1] for p in pts] == [179.0, 181.0, 190.0]


def test_haversine_one_degree_at_equator():
    assert abs(haversine_km(0, 0, 1, 0) - 111.19) < 0.1


def test_summary_reads_naturally():
    f = {"stage": "adult", "sex": "female", "release_site": "Iles Kerguelen", "days_tracked": "236 days",
         "first_date": "Feb 6, 2022", "last_date": "Sep 30, 2022", "max_displacement_km": "2,710 km"}
    s = summary(f, "Southern elephant seal")
    assert s.startswith("An adult female southern elephant seal, tagged at Iles Kerguelen.")
    assert "236 days" in s
