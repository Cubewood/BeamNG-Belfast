"""DfI widths (T-205): names, stretches, matching and confidence on synthetic data. No network, no real data.

    uv run --no-project --python 3.12 --with pytest --with pyproj --with numpy --with pyyaml --with shapely python -m pytest tests/pipeline/test_dfi.py -q
"""
import csv
import json
import math
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import dfi  # noqa: E402


# ---- names ----

@pytest.mark.parametrize("raw, tokens", [
    ("DOWNSHIRE RD1: MAXWELL RD TO END HO 36 RHS", ["DOWNSHIRE", "ROAD"]),
    ("7020A0002_02 BELFAST RD: SHORE RD RBT TO END", ["BELFAST", "ROAD"]),
    ("M2 MOTORWAY1: PHONE CABINET M2/A2", ["M2", "MOTORWAY"]),
    ("ST COLUMBAS DR1: X TO Y", ["SAINT", "COLUMBAS", "DRIVE"]),
    ("KILWARLIN CRE1: BELVOIR DR", ["KILWARLIN", "CRESCENT"]),
    ("MOYLE WK1", ["MOYLE", "WALK"]),
    ("GRAY'S PK AV1: GRAY'S PK2 AT NO.7", ["GRAYS", "PARK", "AVENUE"]),
    ("Gray’s Park Avenue", ["GRAYS", "PARK", "AVENUE"]),
    ("OLD MILLTOWN RD2 : OLD MILLTOWN RD2 TO X", ["OLD", "MILLTOWN", "ROAD"]),
    ("", []),
])
def test_name_tokens(raw, tokens):
    assert dfi.name_tokens(raw) == tokens


def test_name_relation():
    rel = dfi.name_relation
    assert rel("HOSPITAL RD1: A TO B", ["Hospital Road"]) == "exact"
    assert rel("ORMEAU RD1: A TO B", ["Upper Ormeau Road"]) == "partial"      # subset
    assert rel("MILLTOWN HILL1: A", ["Milltown Road"]) == "partial"           # same core, other type word
    assert rel("MILLTOWN RD2: A TO B", ["Belvoir Road"]) == "conflict"
    assert rel("HOSPITAL RD1: A", [None, None]) == "unknown"                  # way has no name
    assert rel("", ["Hospital Road"]) == "unknown"
    assert rel("MILLTOWN RD2: A", ["Belvoir Road", "Milltown Road"]) == "exact"   # any alternative name will do


def test_ref_and_section_code():
    assert dfi.ref_class("A55") == {("A", 55)}
    assert dfi.ref_class("B23;A2") == {("B", 23), ("A", 2)}
    assert dfi.ref_class("X1") == set()
    assert dfi.section_ref("7035A0055_17") == ("A", 55)
    assert dfi.section_ref("7035U0500_01") is None                            # U numbers are not OSM refs


# ---- stretches ----

def item(sec, start, end, x, y, width=7.0, length=None, name="TEST RD1: A TO B", item_id=None):
    return {"item": item_id or f"{sec}-{start}", "section": sec, "road": "U1", "desc": name, "start": float(start),
            "end": float(end), "width": width, "length": float(end - start) if length is None else length,
            "surface": "", "e": 0.0, "n": 0.0, "x": float(x), "y": float(y)}


def test_stretch_goes_to_next_item_when_chainage_and_distance_agree():
    a, b = item("S1", 0, 100, 0, 0), item("S1", 100, 200, 100, 0)
    st = dfi.build_stretches([a, b])
    by = {s["item"]["item"]: s for s in st}
    assert by[a["item"]]["end_xy"] == (100.0, 0.0)
    assert by[b["item"]]["end_xy"] is None            # last item: a lone point


def test_stretch_refuses_a_jump():
    a, b = item("S1", 0, 100, 0, 0), item("S1", 100, 200, 900, 0)     # 900 m away for 100 m of chainage
    assert all(s["end_xy"] is None for s in dfi.build_stretches([a, b]))


def test_stretch_refuses_a_chainage_gap():
    a, b = item("S1", 0, 100, 0, 0), item("S1", 160, 200, 100, 0)
    assert all(s["end_xy"] is None for s in dfi.build_stretches([a, b]))


def test_samples_weight_adds_up_to_item_length():
    a, b = item("S1", 0, 100, 0, 0), item("S1", 100, 200, 100, 0)
    st = {s["item"]["item"]: s for s in dfi.build_stretches([a, b])}[a["item"]]
    samples = dfi.sample_stretch(st)
    assert samples[0][3] is True and samples[0][:2] == (0.0, 0.0)
    assert sum(s[2] for s in samples) == pytest.approx(100.0)
    assert len(samples) == math.ceil(100 / 8)


# ---- matching ----

def way(xy, **tags):
    return {"tags": {"highway": "residential", **tags}, "geometry": [(0, 0), (0, 1)], "xy": xy}


def run(items, ways, sections=None):
    return dfi.match_area(items, sections or {}, ways)


def two_roads():
    """An east-west road named Alpha Road, 40 m north of an unnamed one; items run along Alpha."""
    return {
        1: way([(0, 0), (300, 0)], name="Alpha Road"),
        2: way([(0, 40), (300, 40)]),
    }


def test_width_is_length_weighted_median_and_ignores_junction_items():
    items = [item("S1", 0, 100, 0, 0, width=7.0, name="ALPHA RD1: X TO Y"),
             item("S1", 100, 110, 100, 0, width=22.0, name="ALPHA RD1: X TO Y"),     # junction mouth, ignored
             item("S1", 110, 200, 110, 0, width=7.4, name="ALPHA RD1: X TO Y"),
             item("S1", 200, 290, 200, 0, width=7.4, name="ALPHA RD1: X TO Y"),
             item("S1", 290, 300, 290, 0, width=7.4, name="ALPHA RD1: X TO Y")]
    r = run(items, two_roads())
    assert set(r["ways"]) == {1}
    w = r["ways"][1]
    assert w["width_m"] == 7.4                         # 180 m at 7.4 outweighs 100 m at 7.0
    assert w["name_relation"] == "exact"
    assert r["stats"]["items_ignored_width"] == 1
    assert w["offset_median_m"] < 1.0


def test_name_beats_a_nearer_way_that_says_nothing():
    ways = {1: way([(0, 8), (300, 8)], name="Alpha Road"), 2: way([(0, 2), (300, 2)])}
    items = [item("S1", 0, 100, 0, 0, name="ALPHA RD1: X TO Y"), item("S1", 100, 200, 100, 0, name="ALPHA RD1: X TO Y")]
    r = run(items, ways)
    assert list(r["ways"]) == [1]


def test_a_conflicting_name_is_not_matched():
    ways = {1: way([(0, 3), (300, 3)], name="Beta Street")}
    items = [item("S1", 0, 100, 0, 0, name="ALPHA RD1: X TO Y"), item("S1", 100, 200, 100, 0, name="ALPHA RD1: X TO Y")]
    r = run(items, ways)
    assert r["ways"] == {}
    assert r["stats"]["samples_unmatched_name"] > 0


def test_unnamed_way_matches_on_position_with_low_name_factor():
    ways = {1: way([(0, 3), (300, 3)])}
    items = [item("S1", 0, 100, 0, 0), item("S1", 100, 200, 100, 0)]
    r = run(items, ways)
    assert r["ways"][1]["name_relation"] == "unknown"
    assert r["ways"][1]["parts"]["name"] == pytest.approx(0.55)


def test_far_item_is_not_matched():
    ways = {1: way([(0, 60), (300, 60)], name="Alpha Road")}
    r = run([item("S1", 0, 100, 0, 0, name="ALPHA RD1: X TO Y")], ways)
    assert r["ways"] == {}
    assert r["stats"]["samples_unmatched_no_way"] == 1


def test_road_number_rescues_a_name_difference():
    ways = {1: way([(0, 2), (300, 2)], name="Belvoir Road", ref="A55", highway="trunk")}
    items = [item("7035A0055_17", 0, 100, 0, 0, name="MILLTOWN RD2: X TO Y"),
             item("7035A0055_17", 100, 200, 100, 0, name="MILLTOWN RD2: X TO Y")]
    r = run(items, ways)
    assert r["ways"][1]["name_relation"] == "ref"


def test_systematic_offset_is_reported():
    # DfI points 1.5 m east and 0.5 m north of the way
    ways = {1: way([(0, 0), (300, 0)], name="Alpha Road")}
    items = [item("S1", 0, 100, 1.5, 0.5, name="ALPHA RD1: X TO Y"), item("S1", 100, 200, 101.5, 0.5, name="ALPHA RD1: X TO Y")]
    off = run(items, ways)["offset"]
    assert off["n"] == 2
    assert off["median_dy_m"] == pytest.approx(0.5)
    assert off["median_dx_m"] == pytest.approx(0.0)         # east-west road: the east shift is along the line


def test_non_adopted_items_are_not_in_the_stats_if_unreachable():
    r = run([item("S1", 0, 100, 5000, 5000)], {1: way([(0, 0), (300, 0)], name="Alpha Road")})
    assert r["ways"] == {}


# ---- confidence ----

def test_confidence_orders_sensibly():
    best = dfi.confidence(0.5, 1.0, 1.0, 0.0, 0)["score"]
    assert best == 1.0
    assert dfi.confidence(10.0, 1.0, 1.0, 0.0, 0)["score"] < best               # far from the line
    assert dfi.confidence(0.5, 0.55, 1.0, 0.0, 0)["score"] < best               # no name to check
    assert dfi.confidence(0.5, 1.0, 0.1, 0.0, 0)["score"] < best                # little of the way covered
    assert dfi.confidence(0.5, 1.0, 1.0, 3.0, 0)["score"] < best                # widths all over the place
    assert dfi.confidence(0.5, 1.0, 1.0, 0.0, 3)["score"] < best                # class disagrees
    assert 0.0 <= dfi.confidence(dfi.SNAP_M, 0.55, 0.0, 9.0, 4)["score"] <= 1.0
    assert dfi.tier(0.9) == "high" and dfi.tier(0.5) == "medium" and dfi.tier(0.1) == "low"


def test_weighted_quantile():
    assert dfi.weighted_quantile([1, 2, 3], [1, 1, 1], 0.5) == 2
    assert dfi.weighted_quantile([1, 10], [1, 9], 0.5) == 10
    assert math.isnan(dfi.weighted_quantile([], [], 0.5))


# ---- file readers ----

def make_xlsx(path: Path, rows: list[list]):
    """A tiny workbook in the shape of the DfI one: header row and rows, shared strings, one sheet."""
    headers = ["Road Name", "Section Code", "Item", "Start Metres", "End Metres", "Width", "Section Description",
               "Item Type", "Digital Length", "Surface Code", "Carriageway", "Section Office", "Sub Office",
               "Division", "Easting", "Northing"]
    strings: list[str] = []

    def sidx(s):
        if s not in strings:
            strings.append(s)
        return strings.index(s)

    cols = "ABCDEFGHIJKLMNOP"

    def cell(ref, v):
        if isinstance(v, str):
            return f'<c r="{ref}" t="s"><v>{sidx(v)}</v></c>'
        return f'<c r="{ref}"><v>{v}</v></c>'

    xml_rows = ["<row r=\"1\">" + "".join(cell(f"{cols[i]}1", h) for i, h in enumerate(headers)) + "</row>"]
    for n, row in enumerate(rows, start=2):
        xml_rows.append(f'<row r="{n}">' + "".join(cell(f"{cols[i]}{n}", v) for i, v in enumerate(row) if v is not None) + "</row>")
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    sheet = f'<?xml version="1.0"?><worksheet {ns}><sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    sst = f'<?xml version="1.0"?><sst {ns}>' + "".join(f"<si><t>{s.replace('&', '&amp;')}</t></si>" for s in strings) + "</sst>"
    wb = (f'<?xml version="1.0"?><workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<sheets><sheet name="Eastern" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", sheet)


def test_read_xlsx_items_and_parse(tmp_path):
    xlsx = tmp_path / "cd.xlsx"
    make_xlsx(xlsx, [
        ["U1", "7035U0001_01", "10", 0, 50, 6.5, "ALPHA RD1: X TO Y", "CARRIAGEWAY", 49.8, "Asphalt", "10", "O", "S", "EASTERN", 333000, 368000],
        ["U1", "7035U0001_01", "11", 50, 90, 5.5, "ALPHA RD1: X TO Y", "CARRIAGEWAY", 40, None, "11", "O", "S", "EASTERN", 333040, 368010],
        ["U1", "7035U0001_01", "12", 90, 99, "n/a", "ALPHA RD1: X TO Y", "CARRIAGEWAY", 9, None, "12", "O", "S", "EASTERN", 333080, 368020],
    ])
    rows = [r for _, r in dfi.read_xlsx_items(xlsx)]
    assert len(rows) == 3
    assert rows[0]["easting"] == "333000" and rows[0]["section_desc"] == "ALPHA RD1: X TO Y"
    assert rows[1].get("surface") is None            # an empty cell is simply absent

    hn = tmp_path / "HIGHWAY_NETWORK.CSV"
    hn.write_text('"SECTION_CODE","SECTION_NAME","SECTION_OFFICE_NAME","DIVISION_NAME","SECTION_TYPE_NAME",'
                  '"ADOPTION_STATUS_NAME","DIGITAL_LENGTH","CLASS"\n'
                  '"7035U0001_01","7035U0001_01 ALPHA RD1: X TO Y","O","EASTERN","Single Carriageway","Adopted","99.5","U"\n',
                  encoding="utf-8")
    manifest = {"files": {
        "carriageway_details": {"file": "cd.xlsx", "size_bytes": xlsx.stat().st_size, "sha256": dfi.file_sha256(xlsx)},
        "highway_network": {"file": "HIGHWAY_NETWORK.CSV", "size_bytes": hn.stat().st_size, "sha256": dfi.file_sha256(hn)},
    }}
    out = dfi.parse(tmp_path, manifest)
    assert out["items"] == 2 and out["items_dropped_no_number"] == 1 and out["sections"] == 1
    items = dfi.load_items(tmp_path / "items.csv")
    assert [i["width"] for i in items] == [6.5, 5.5]
    assert dfi.load_items(tmp_path / "items.csv", (333030, 367990, 333100, 368100))[0]["item"] == "11"
    assert dfi.load_sections(tmp_path / "sections.csv")["7035U0001_01"]["class"] == "U"


def test_verify_catches_a_changed_file(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"abc")
    good = {"files": {"x": {"file": "a.bin", "size_bytes": 3, "sha256": dfi.file_sha256(f), "url": "u"}}}
    dfi.verify(good, tmp_path)
    f.write_bytes(b"abd")
    with pytest.raises(dfi.DfiError, match="sha256"):
        dfi.verify(good, tmp_path)
    f.unlink()
    with pytest.raises(dfi.DfiError, match="missing"):
        dfi.verify(good, tmp_path)


def test_load_osm_ways_keeps_roads_only(tmp_path):
    els = [
        {"type": "node", "id": 1, "lat": 1, "lon": 1},
        {"type": "way", "id": 2, "tags": {"highway": "residential"}, "geometry": [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 1}]},
        {"type": "way", "id": 3, "tags": {"highway": "footway"}, "geometry": [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 1}]},
        {"type": "way", "id": 4, "tags": {"highway": "service", "area": "yes"}, "geometry": [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 1}]},
        {"type": "way", "id": 5, "tags": {"building": "yes"}, "geometry": [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 1}]},
    ]
    p = tmp_path / "cut.json"
    p.write_text(json.dumps({"elements": els}), encoding="utf-8")
    assert list(dfi.load_osm_ways(p)) == [2]


def test_manifest_if_present_is_well_formed():
    m = ROOT / "data" / "sources" / "dfi.json"
    if not m.exists():
        pytest.skip("manifest not written yet")
    d = json.loads(m.read_text(encoding="utf-8"))
    assert "Open Government Licence" in d["licence"]
    assert {"highway_network", "carriageway_details"} <= set(d["files"])
    for rec in d["files"].values():
        assert len(rec["sha256"]) == 64 and rec["size_bytes"] > 0 and rec["url"].startswith("https://")
