# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "h3>=4.1",
#   "shapely>=2.0",
#   "geopandas>=1.0",
#   "pyogrio>=0.9",
#   "pandas>=2.2",
#   "numpy>=1.26",
#   "scikit-learn>=1.4",
# ]
# ///
"""Geocircle data pipeline.

Reads raw downloads from data-raw/ and writes everything the game loads into public/data/.
Run from the project root:  uv run scripts/pipeline/build.py [step ...]
Steps: land, geo, population, stats, points, puzzles (default: all, in that order).

Formats (see lib/cells.ts and lib/land.ts for the readers):
  fine-cell file  <name>.bin           16-byte records: u32 lo, u32 hi, f32 value, u16 country, u16 0
                  <name>.index.json    {"res": r, "groups": {r3: [offset, count, total, maxR_km]}}
  land bitmaps    land-r{5,6,7}.bin    one bit per child (sorted ids) of each parent cell
                  land-r{5,6,7}.json   {"res", "parentRes", "groups": {parent: [byteOffset, nBytes]}, "full": [parent...]}
"""
from __future__ import annotations

import gzip
import json
import math
import os
import shutil
import sys
import zipfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import geopandas as gpd
import h3.api.basic_int as h3
import numpy as np
import pandas as pd
import shapely
import warnings

warnings.filterwarnings("ignore", message="Geometry is in a geographic CRS")
from shapely.geometry import Point, Polygon, box

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data-raw"
OUT = ROOT / "public" / "data"
CACHE = ROOT / "scripts" / "pipeline" / ".cache"
EARTH_R = 6371.0088
REC = np.dtype([("lo", "<u4"), ("hi", "<u4"), ("value", "<f4"), ("country", "<u2"), ("pad", "<u2")])


def log(*a):
    print("·", *a, flush=True)


def hav_km(lat1, lng1, lat2, lng2):
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_R * np.arcsin(np.minimum(1, np.sqrt(h)))


def write_json(path: Path, obj, round_floats: int | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    log(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.2f} MB)")


def all_cells(res: int) -> list[int]:
    out = []
    for c in h3.get_res0_cells():
        out.extend(h3.cell_to_children(c, res))
    return out


def cell_poly(c: int) -> Polygon:
    return Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)])


def crosses_dateline_or_pole(c: int) -> bool:
    b = h3.cell_to_boundary(c)
    lngs = [p[1] for p in b]
    lat = h3.cell_to_latlng(c)[0]
    return (max(lngs) - min(lngs) > 180) or abs(lat) > 80 or min(abs(abs(l) - 180) for l in lngs) < 3


# ---------------------------------------------------------------- land

def load_land_pieces() -> list:
    """Natural Earth land + minor islands, split into pieces of at most ~256 vertices for fast tests."""
    land = gpd.read_file(f"zip://{RAW / 'ne' / 'ne_10m_land.zip'}")
    isl = gpd.read_file(f"zip://{RAW / 'ne' / 'ne_10m_minor_islands.zip'}")
    geoms = shapely.make_valid(np.concatenate([land.geometry.values, isl.geometry.values]))
    polys = []
    for g in geoms:
        polys.extend(shapely.get_parts(g))
    polys = [p for p in polys if p.geom_type == "Polygon" and not p.is_empty]
    pieces = []

    def split(g):
        if g.is_empty:
            return
        if shapely.get_num_coordinates(g) <= 256:
            pieces.append(g)
            return
        x0, y0, x1, y1 = g.bounds
        if x1 - x0 >= y1 - y0:
            m = (x0 + x1) / 2
            halves = [box(x0, y0, m, y1), box(m, y0, x1, y1)]
        else:
            m = (y0 + y1) / 2
            halves = [box(x0, y0, x1, m), box(x0, m, x1, y1)]
        for h in halves:
            part = shapely.clip_by_rect(g, *h.bounds)
            for p in shapely.get_parts(part):
                if p.geom_type == "Polygon" and p.area > 0:
                    split(p)

    for p in polys:
        split(p)
    log(f"land: {len(polys)} polygons -> {len(pieces)} pieces")
    return pieces


_TREE = None
_PIECES = None


def _init_worker(pieces):
    global _TREE, _PIECES
    _PIECES = pieces
    _TREE = shapely.STRtree(pieces)


def _land_children(args):
    """Return (r3, land r7 children as sorted list, is_full) for one r3 cell."""
    r3, cap_wkb = args
    cap = shapely.from_wkb(cap_wkb)
    cand = _TREE.query(cap, predicate="intersects")
    kids = sorted(h3.cell_to_children(r3, 7))
    if len(cand) == 0:
        return r3, [], False
    if any(_PIECES[i].contains(cap) for i in cand):
        return r3, kids, True
    if crosses_dateline_or_pole(r3):
        pts = [Point(lng, lat) for lat, lng in (h3.cell_to_latlng(k) for k in kids)]
        hit = np.unique(_TREE.query(pts, predicate="intersects")[0])
    else:
        polys = [cell_poly(k) for k in kids]
        hit = np.unique(_TREE.query(polys, predicate="intersects")[0])
    land = [kids[i] for i in hit]
    return r3, land, len(land) == len(kids)


def cap_polygon(c: int, factor: float) -> Polygon:
    """Polygon around a cell that safely holds all its descendants: the hex scaled up from its centre."""
    lat, lng = h3.cell_to_latlng(c)
    pts = []
    for blat, blng in h3.cell_to_boundary(c):
        dl = blng - lng
        if dl > 180:
            dl -= 360
        if dl < -180:
            dl += 360
        pts.append((lng + dl * factor, lat + (blat - lat) * factor))
    return Polygon(pts)


def descendant_factor() -> float:
    """How far r7 descendants of an r3 cell reach past the r3 hex, as a scale factor (measured, plus margin)."""
    worst = 1.0
    for c in all_cells(3)[::997][:40]:
        lat, lng = h3.cell_to_latlng(c)
        circ = max(hav_km(lat, lng, b[0], b[1]) for b in h3.cell_to_boundary(c))
        far = 0
        for k in h3.cell_to_children(c, 7):
            for b in h3.cell_to_boundary(k):
                far = max(far, hav_km(lat, lng, b[0], b[1]))
        worst = max(worst, far / circ)
    return worst * 1.15


def pack_bitmaps(parent_res: int, res: int, land_by_parent: dict[int, set[int]], name: str):
    groups, full, chunks, off = {}, [], [], 0
    for p in sorted(land_by_parent):
        land = land_by_parent[p]
        kids = sorted(h3.cell_to_children(p, res))
        if len(land) == len(kids):
            full.append(h3.int_to_str(p))
            continue
        bits = np.zeros(len(kids), dtype=bool)
        idx = {k: i for i, k in enumerate(kids)}
        for k in land:
            bits[idx[k]] = True
        b = np.packbits(bits, bitorder="little").tobytes()
        groups[h3.int_to_str(p)] = [off, len(b)]
        chunks.append(b)
        off += len(b)
    (OUT / f"{name}.bin").write_bytes(b"".join(chunks))
    write_json(OUT / f"{name}.json", {"res": res, "parentRes": parent_res, "groups": groups, "full": full})
    log(f"{name}: {len(groups)} partial + {len(full)} full parents, {off / 1e6:.2f} MB bitmap")


def step_land():
    pieces = load_land_pieces()
    factor = descendant_factor()
    log(f"land: descendant cap factor {factor:.3f}")
    r3s = all_cells(3)
    tasks = [(c, shapely.to_wkb(cap_polygon(c, factor))) for c in r3s]
    r7_by_r3: dict[int, list[int]] = {}
    with ProcessPoolExecutor(max_workers=os.cpu_count(), initializer=_init_worker, initargs=(pieces,)) as ex:
        for i, (r3, land, _full) in enumerate(ex.map(_land_children, tasks, chunksize=64)):
            if land:
                r7_by_r3[r3] = land
            if i % 5000 == 0:
                log(f"land: {i}/{len(tasks)} r3 cells")
    r7_count = sum(len(v) for v in r7_by_r3.values())
    log(f"land: {r7_count:,} r7 land cells under {len(r7_by_r3):,} r3 parents")
    assert 20e6 < r7_count < 45e6, r7_count

    # r7 by r3 parent; r6 by r2; r5 by r1 ("any child is land" upward).
    pack_bitmaps(3, 7, {k: set(v) for k, v in r7_by_r3.items()}, "land-r7")
    r6_by_r2: dict[int, set[int]] = defaultdict(set)
    r5_by_r1: dict[int, set[int]] = defaultdict(set)
    count7 = {4: defaultdict(int), 3: defaultdict(int), 2: defaultdict(int)}
    for r3, kids in r7_by_r3.items():
        for k in kids:
            r6 = h3.cell_to_parent(k, 6)
            r6_by_r2[h3.cell_to_parent(r6, 2)].add(r6)
            r5 = h3.cell_to_parent(k, 5)
            r5_by_r1[h3.cell_to_parent(r5, 1)].add(r5)
            count7[4][h3.cell_to_parent(k, 4)] += 1
        count7[3][r3] += len(kids)
        count7[2][h3.cell_to_parent(r3, 2)] += len(kids)
    pack_bitmaps(2, 6, r6_by_r2, "land-r6")
    pack_bitmaps(1, 5, r5_by_r1, "land-r5")

    # Zoomed-out layers: a hex is land when at least 25% of its r7 descendants are.
    for res in (2, 3, 4):
        n_desc = 7 ** (7 - res)
        cells = sorted(h3.int_to_str(c) for c, n in count7[res].items() if n >= 0.25 * n_desc)
        write_json(OUT / f"land-r{res}.json", cells)
        log(f"land-r{res}: {len(cells):,} cells")

    # Self-check: decode a few bitmaps back.
    idx = json.loads((OUT / "land-r7.json").read_text())
    blob = (OUT / "land-r7.bin").read_bytes()
    for p_str in list(idx["groups"])[::500][:20]:
        o, n = idx["groups"][p_str]
        kids = sorted(h3.cell_to_children(h3.str_to_int(p_str), 7))
        bits = np.unpackbits(np.frombuffer(blob[o : o + n], dtype=np.uint8), bitorder="little")[: len(kids)]
        assert {kids[i] for i in np.flatnonzero(bits)} == set(r7_by_r3[h3.str_to_int(p_str)])
    log("land: bitmap round-trip ok")


# ---------------------------------------------------------------- geo layers (coast, borders, cities)

def round_geom(g, nd=3):
    return shapely.set_precision(g, 10 ** -nd)


def step_geo():
    coast = gpd.read_file(f"zip://{RAW / 'ne' / 'ne_10m_coastline.zip'}")
    coast["geometry"] = coast.geometry.simplify(0.004)
    coast[["geometry"]].to_file(CACHE / "coast.geojson", driver="GeoJSON", COORDINATE_PRECISION=3)
    shutil.copy(CACHE / "coast.geojson", OUT / "coast-10m.json")
    log(f"coast: {(OUT / 'coast-10m.json').stat().st_size / 1e6:.2f} MB")

    b = gpd.read_file(f"zip://{RAW / 'ne' / 'ne_10m_admin_0_boundary_lines_land.zip'}")
    b["geometry"] = b.geometry.simplify(0.004)
    b[["geometry"]].to_file(CACHE / "borders.geojson", driver="GeoJSON", COORDINATE_PRECISION=3)
    shutil.copy(CACHE / "borders.geojson", OUT / "borders-10m.json")
    log(f"borders: {(OUT / 'borders-10m.json').stat().st_size / 1e6:.2f} MB")

    pl = gpd.read_file(f"zip://{RAW / 'ne' / 'ne_10m_populated_places_simple.zip'}")
    feats = []
    for _, r in pl.sort_values("pop_max", ascending=False).iterrows():
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(r.geometry.x, 3), round(r.geometry.y, 3)]},
                "properties": {"n": r["name"], "p": int(r["pop_max"]), "r": int(r["scalerank"])},
            }
        )
    write_json(OUT / "places.json", {"type": "FeatureCollection", "features": feats})


# ---------------------------------------------------------------- countries

def load_countries() -> gpd.GeoDataFrame:
    c = gpd.read_file(f"zip://{RAW / 'ne' / 'ne_10m_admin_0_countries.zip'}")
    c = c[["ADMIN", "ISO_A3_EH", "ISO_N3_EH", "CONTINENT", "SUBREGION", "geometry"]].reset_index(drop=True)
    c.index = c.index + 1  # country index 0 means "unknown"
    return c


def assign_country(lats: np.ndarray, lngs: np.ndarray, countries: gpd.GeoDataFrame) -> np.ndarray:
    pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy(lngs, lats), crs=countries.crs)
    j = gpd.sjoin(pts, countries[["geometry"]], how="left", predicate="within")
    j = j[~j.index.duplicated()]
    out = j["index_right"].to_numpy(dtype=float, copy=True)
    miss = np.isnan(out)
    if miss.any():
        near = gpd.sjoin_nearest(pts[miss], countries[["geometry"]], how="left")
        near = near[~near.index.duplicated()]
        out[miss] = near["index_right"].to_numpy()
    return out.astype(np.uint16)


# ---------------------------------------------------------------- fine-cell files

def write_cells(name: str, cells: np.ndarray, values: np.ndarray, country: np.ndarray, res: int):
    """cells: uint64 H3 ints at `res`. Groups by r3 parent, writes .bin + .index.json."""
    order = np.lexsort((cells, np.array([h3.cell_to_parent(int(c), 3) for c in cells], dtype=np.uint64)))
    cells, values, country = cells[order], values[order], country[order]
    parents = np.array([h3.cell_to_parent(int(c), 3) for c in cells], dtype=np.uint64)
    rec = np.zeros(len(cells), dtype=REC)
    rec["lo"] = (cells & 0xFFFFFFFF).astype(np.uint32)
    rec["hi"] = (cells >> np.uint64(32)).astype(np.uint32)
    rec["value"] = values.astype(np.float32)
    rec["country"] = country
    path = OUT / f"{name}.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec.tofile(path)
    latlng = np.array([h3.cell_to_latlng(int(c)) for c in cells])
    groups = {}
    starts = np.flatnonzero(np.r_[True, parents[1:] != parents[:-1]])
    ends = np.r_[starts[1:], len(cells)]
    for s, e in zip(starts, ends):
        p = int(parents[s])
        plat, plng = h3.cell_to_latlng(p)
        maxr = float(hav_km(plat, plng, latlng[s:e, 0], latlng[s:e, 1]).max())
        groups[h3.int_to_str(p)] = [int(s), int(e - s), float(rec["value"][s:e].astype(np.float64).sum()), round(maxr, 3)]
    write_json(OUT / f"{name}.index.json", {"res": res, "groups": groups})
    log(f"{name}: {len(cells):,} cells in {len(groups):,} r3 groups, {path.stat().st_size / 1e6:.2f} MB")
    total = sum(g[2] for g in groups.values())
    assert abs(total - rec["value"].astype(np.float64).sum()) < 1e-6 * max(1, total), "r3 totals don't add up"


def read_cells(name: str):
    idx = json.loads((OUT / f"{name}.index.json").read_text())
    rec = np.fromfile(OUT / f"{name}.bin", dtype=REC)
    cells = rec["lo"].astype(np.uint64) | (rec["hi"].astype(np.uint64) << np.uint64(32))
    return idx, cells, rec["value"].astype(np.float64), rec["country"].astype(np.int64)


# ---------------------------------------------------------------- population

def step_population():
    gz = RAW / "kontur" / "kontur_r6.gpkg.gz"
    gpkg = CACHE / "kontur_r6.gpkg"
    if not gpkg.exists():
        log("population: unzipping Kontur")
        with gzip.open(gz, "rb") as fi, open(gpkg, "wb") as fo:
            shutil.copyfileobj(fi, fo, length=1 << 24)
    import pyogrio

    df = pyogrio.read_dataframe(gpkg, columns=["h3", "population"], read_geometry=False)
    log(f"population: {len(df):,} Kontur r6 cells, total {df.population.sum() / 1e9:.2f} B")
    r5 = np.array([h3.cell_to_parent(h3.str_to_int(s), 5) for s in df["h3"]], dtype=np.uint64)
    agg = pd.Series(df["population"].to_numpy(dtype=np.float64)).groupby(r5).sum()
    cells = agg.index.to_numpy(dtype=np.uint64)
    pop = agg.to_numpy()
    total = pop.sum()
    assert 7.5e9 < total < 8.5e9, total
    latlng = np.array([h3.cell_to_latlng(int(c)) for c in cells])
    countries = load_countries()
    cidx = assign_country(latlng[:, 0], latlng[:, 1], countries)
    write_cells("population", cells, pop, cidx, 5)
    write_json(
        OUT / "countries.json",
        [{"i": int(i), "name": r.ADMIN, "iso3": r.ISO_A3_EH, "m49": r.ISO_N3_EH, "continent": r.CONTINENT} for i, r in countries.iterrows()],
    )


# ---------------------------------------------------------------- food stats

FOOD = {
    "rice": "Rice and products",
    "beer": "Beer",
    "coffee": "Coffee and products",
    "chocolate": "Cocoa Beans and products",
    "wine": "Wine",
    "potatoes": "Potatoes and products",
    "fish": "Fish, Seafood",
}


def step_stats():
    countries = load_countries()
    m49_to_idx: dict[int, list[int]] = defaultdict(list)  # several NE rows share a code (France + Clipperton)
    for i, r in countries.iterrows():
        try:
            n = int(r.ISO_N3_EH)
        except (TypeError, ValueError):
            continue
        if n > 0:
            m49_to_idx[n].append(i)
    with zipfile.ZipFile(RAW / "fao" / "fbs.zip") as z:
        name = next(n for n in z.namelist() if n.endswith(".csv") and "All_Data" in n and "Flags" not in n)
        fbs = pd.read_csv(z.open(name), encoding="latin-1", low_memory=False)
    fbs = fbs[(fbs["Element Code"] == 645)]
    fbs["m49"] = pd.to_numeric(fbs["Area Code (M49)"].astype(str).str.lstrip("'"), errors="coerce")
    fbs = fbs[fbs["Area Code"] != 351]  # "China" aggregate; mainland (41), Taiwan, HK, Macao are listed separately
    # Countries FAO reports at all (any item). A listed country without an item eats ~none of it.
    in_fao = set()
    for m in fbs.loc[fbs["Year"] >= 2018, "m49"].dropna().unique():
        in_fao.update(m49_to_idx.get(int(m), []))
    idx, cells, pop, cidx = read_cells("population")
    parents = np.array([h3.cell_to_parent(int(c), 3) for c in cells], dtype=np.uint64)
    pop_by_country = pd.Series(pop).groupby(cidx).sum()
    continent = countries["CONTINENT"].to_dict()
    for key, item in FOOD.items():
        rows = fbs[fbs["Item"] == item]
        assert len(rows), f"FAOSTAT item not found: {item}"
        # Latest year per country (FAO publishes some countries a year or two later), not older than 2018.
        rows = rows[rows["Year"] >= 2018].dropna(subset=["Value"]).sort_values("Year").groupby("m49").tail(1)
        year = int(rows["Year"].max())
        kg = np.zeros(len(countries) + 1)
        for _, r in rows.iterrows():
            for i in m49_to_idx.get(int(r.m49), []) if not math.isnan(r.m49) else []:
                kg[i] = float(r.Value)
        # Countries FAO doesn't cover at all (Japan, Sudan...) get their subregion's population-weighted average.
        sub = countries["SUBREGION"]
        reported = [i for i in pop_by_country.index if i in in_fao]
        imputed = []
        for i in pop_by_country.index:
            if i == 0 or i in in_fao:
                continue
            peers = [j for j in reported if sub[j] == sub[i] and kg[j] > 0]
            if peers and pop_by_country[peers].sum() > 0:
                kg[i] = float((kg[peers] * pop_by_country[peers]).sum() / pop_by_country[peers].sum())
                if pop_by_country[i] > 5e6:
                    imputed.append(countries.loc[i, "ADMIN"])
        if imputed:
            log(f"stat {key}: imputed from subregion for {', '.join(imputed)}")
        w = pop * kg[cidx]
        r3 = pd.Series(w).groupby(parents).sum()
        covered = pop_by_country[[i for i in reported]].sum() / pop.sum()
        by_cont = pd.Series(w).groupby([continent.get(int(i), "?") for i in cidx]).sum() / w.sum()
        by_country = pd.Series(w).groupby(cidx).sum().sort_values(ascending=False) / w.sum()
        log(f"stat {key}: {year}, population covered {covered:.1%}, top continents {by_cont.sort_values(ascending=False).head(3).round(3).to_dict()}")
        assert covered > 0.9, f"{key}: only {covered:.0%} of people are in FAO's data"
        if key == "rice":
            assert by_cont.get("Asia", 0) > 0.8, by_cont
        top = [{"name": countries.loc[int(i), "ADMIN"], "share": round(float(s), 4)} for i, s in by_country.head(5).items() if i > 0]
        write_json(
            OUT / "stats" / f"{key}.json",
            {
                "item": item,
                "year": year,
                "kg": [round(float(x), 3) for x in kg],
                "r3": {h3.int_to_str(int(k)): round(float(v), 1) for k, v in r3.items() if v > 0},
                "top": top,
            },
        )


# ---------------------------------------------------------------- point datasets

def points_to_cells(name: str, lats: np.ndarray, lngs: np.ndarray):
    r7 = np.array([h3.latlng_to_cell(float(a), float(b), 7) for a, b in zip(lats, lngs)], dtype=np.uint64)
    counts = pd.Series(1.0, index=r7).groupby(level=0).sum()
    cells = counts.index.to_numpy(dtype=np.uint64)
    latlng = np.array([h3.cell_to_latlng(int(c)) for c in cells])
    countries = load_countries()
    cidx = assign_country(latlng[:, 0], latlng[:, 1], countries)
    write_cells(f"points/{name}", cells, counts.to_numpy(), cidx, 7)
    by_country = pd.Series(counts.to_numpy()).groupby(cidx).sum().sort_values(ascending=False) / counts.sum()
    top = [{"name": countries.loc[int(i), "ADMIN"], "share": round(float(s), 4)} for i, s in by_country.head(5).items() if i > 0]
    write_json(OUT / "points" / f"{name}.top.json", top)


def step_points():
    frames = [pd.read_csv(p) for p in sorted((RAW / "usgs").glob("*.csv"))]
    eq = pd.concat(frames)
    log(f"earthquakes: {len(eq):,} events M4.5+")
    assert len(eq) > 100_000, len(eq)
    points_to_cells("earthquakes", eq["latitude"].to_numpy(), eq["longitude"].to_numpy())
    mcd_path = RAW / "overture" / "mcdonalds.csv"
    if mcd_path.exists():
        mcd = pd.read_csv(mcd_path).dropna(subset=["lat", "lng"])
        log(f"mcdonalds: {len(mcd):,} places")
        assert len(mcd) > 10_000, len(mcd)
        points_to_cells("mcdonalds", mcd["lat"].to_numpy(), mcd["lng"].to_numpy())
    else:
        log("mcdonalds: no Overture extract yet, skipping")


# ---------------------------------------------------------------- best possible shapes

def fine_points(cells_name: str, stat: str | None):
    idx, cells, value, cidx = read_cells(cells_name)
    w = value
    if stat:
        kg = np.array(json.loads((OUT / "stats" / f"{stat}.json").read_text())["kg"])
        w = value * kg[cidx]
    latlng = np.array([h3.cell_to_latlng(int(c)) for c in cells])
    keep = w > 0
    return latlng[keep], w[keep]


def unit(latlng):
    la, lo = np.radians(latlng[:, 0]), np.radians(latlng[:, 1])
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=1)


def best_circle(latlng, w, radius_km):
    from sklearn.neighbors import BallTree

    rad = np.radians(latlng)
    tree = BallTree(rad, metric="haversine")
    r = radius_km / EARTH_R
    # Coarse pass: candidate centres on a ~25 km thinned subset of heavy cells.
    order = np.argsort(-w)
    cand = order[: min(len(order), 4000)]
    sums = np.array([w[ix].sum() for ix in tree.query_radius(rad[cand], r)])
    top = cand[np.argsort(-sums)[:15]]
    best_c, best_s = None, -1.0
    for t in top:  # local refinement on a small grid around each top centre
        for dlat in np.linspace(-0.6, 0.6, 7):
            for dlng in np.linspace(-0.6, 0.6, 7):
                c = np.radians([[latlng[t, 0] + dlat, latlng[t, 1] + dlng]])
                s = w[tree.query_radius(c, r)[0]].sum()
                if s > best_s:
                    best_s, best_c = s, (latlng[t, 1] + dlng, latlng[t, 0] + dlat)
    return best_c, 100 * best_s / w.sum()


def tri_mask(U, A, B, C):
    m = (A + B + C)
    front = U @ m > 0
    s1 = U @ np.cross(A, B)
    s2 = U @ np.cross(B, C)
    s3 = U @ np.cross(C, A)
    return front & (((s1 >= 0) & (s2 >= 0) & (s3 >= 0)) | ((s1 <= 0) & (s2 <= 0) & (s3 <= 0)))


def best_triangle(latlng, w, budget_km, seed=1):
    rng = np.random.default_rng(seed)
    # Work on r3-level aggregates for speed, then score the winner on the fine cells.
    keys = np.array([h3.cell_to_parent(h3.latlng_to_cell(a, b, 5), 3) for a, b in latlng], dtype=np.uint64)
    agg = pd.Series(w).groupby(keys).sum()
    cl = np.array([h3.cell_to_latlng(int(k)) for k in agg.index])
    cw = agg.to_numpy()
    U = unit(cl)
    Uf = unit(latlng)

    def tri_ok(p):
        s = [hav_km(p[i][0], p[i][1], p[(i + 1) % 3][0], p[(i + 1) % 3][1]) for i in range(3)]
        return min(s) >= 10 and max(s) <= 8000 and sum(s) <= budget_km

    def score(p, U_, w_):
        A, B, C = unit(np.array(p))
        return w_[tri_mask(U_, A, B, C)].sum()

    heavy = np.argsort(-cw)[:300]
    best_p, best_s = None, -1.0
    for n in range(300):
        c = cl[heavy[n % 40]] if n < 120 else cl[rng.choice(heavy)]  # every top-40 cell gets 3 tries first
        r = budget_km / 3 / 1.732 / 111 * rng.uniform(0.7, 1.0)  # equilateral triangle radius in degrees, roughly
        ang = rng.uniform(0, 2 * np.pi) + np.array([0, 2.094, 4.189])
        p = [[c[0] + r * np.sin(a), c[1] + r * np.cos(a) / max(0.2, np.cos(np.radians(c[0])))] for a in ang]
        while not tri_ok(p):
            p = [[c[0] + (q[0] - c[0]) * 0.9, c[1] + (q[1] - c[1]) * 0.9] for q in p]
        s = score(p, U, cw)
        step = 3.0
        for _ in range(250):
            i = rng.integers(3)
            q = [list(x) for x in p]
            q[i][0] = float(np.clip(q[i][0] + rng.normal(0, step), -85, 85))
            q[i][1] = q[i][1] + rng.normal(0, step)
            if tri_ok(q):
                sq = score(q, U, cw)
                if sq >= s:
                    p, s = q, sq
            step = max(0.05, step * 0.985)
        if s > best_s:
            best_s, best_p = s, p
    fine = score(best_p, Uf, w)
    return [[q[1], q[0]] for q in best_p], 100 * fine / w.sum()


# ---------------------------------------------------------------- puzzles

PUZZLES = [
    dict(stat="people", cells="population", food=None, mechanic="circle", mode="target", target_pct=10,
         prompt="Draw a circle that holds exactly 10% of all people on Earth.", view=[95, 25, 1.6],
         credit="Kontur Population (CC BY 4.0)"),
    dict(stat="rice eaten", cells="population", food="rice", mechanic="circle", mode="target", target_pct=20,
         prompt="Draw a circle that holds exactly 20% of the world's rice eaten.", view=[100, 20, 1.6]),
    dict(stat="beer drunk", cells="population", food="beer", mechanic="triangle", mode="target", target_pct=25,
         prompt="Put down 3 pins. Your triangle must hold exactly 25% of the world's beer drunk.", view=[10, 35, 1.4]),
    dict(stat="coffee drunk", cells="population", food="coffee", mechanic="circle", mode="budget", radius_km=1500,
         prompt="You get a 1,500 km circle. Catch as much of the world's coffee drunk as you can.", view=[0, 30, 1.4]),
    dict(stat="chocolate eaten", cells="population", food="chocolate", mechanic="triangle", mode="budget", perimeter_km=6000,
         prompt="Your triangle can be at most 6,000 km around. Catch as much chocolate eaten as you can.", view=[0, 40, 1.4]),
    dict(stat="wine drunk", cells="population", food="wine", mechanic="circle", mode="target", target_pct=30,
         prompt="Draw a circle that holds exactly 30% of the world's wine drunk.", view=[10, 40, 1.6]),
    dict(stat="potatoes eaten", cells="population", food="potatoes", mechanic="triangle", mode="target", target_pct=20,
         prompt="Put down 3 pins. Your triangle must hold exactly 20% of the world's potatoes eaten.", view=[40, 40, 1.4]),
    dict(stat="fish eaten", cells="population", food="fish", mechanic="circle", mode="budget", radius_km=1000,
         prompt="You get a 1,000 km circle. Catch as much of the world's fish eaten as you can.", view=[120, 25, 1.6]),
    dict(stat="earthquakes", cells="points/earthquakes", food=None, mechanic="triangle", mode="budget", perimeter_km=8000,
         prompt="Your triangle can be at most 8,000 km around. Catch as many earthquakes (M4.5+, since 2000) as you can.",
         view=[150, 0, 1.3], credit="USGS Earthquake Catalog (public domain)"),
    dict(stat="mapped McDonald's", cells="points/mcdonalds", food=None, mechanic="circle", mode="target", target_pct=10,
         prompt="Draw a circle that holds exactly 10% of the world's mapped McDonald's.", view=[-40, 35, 1.4],
         credit="Overture Maps Places (CDLA Permissive 2.0)"),
]

FOOD_CREDIT = "FAO FAOSTAT Food Balances (CC BY 4.0); Kontur Population (CC BY 4.0)"


def pct_text(s):
    return f"{s * 100:.0f}%"


def step_puzzles():
    out = []
    for i, p in enumerate(PUZZLES, start=1):
        if not (OUT / f"{p['cells']}.index.json").exists():
            log(f"puzzle {i} ({p['stat']}): data missing, skipped")
            continue
        latlng, w = fine_points(p["cells"], p["food"])
        best = {"pct": None, "shape": None}
        if p["mode"] == "budget":
            if p["mechanic"] == "circle":
                c, pct = best_circle(latlng, w, p["radius_km"])
                best = {"pct": round(pct, 2), "shape": {"center": [round(c[0], 4), round(c[1], 4)], "radius_km": p["radius_km"]}}
            else:
                pins, pct = best_triangle(latlng, w, p["perimeter_km"])
                best = {"pct": round(pct, 2), "shape": {"pins": [[round(a, 4), round(b, 4)] for a, b in pins]}}
            log(f"puzzle {i} ({p['stat']}): best {best['pct']}%")
        if p["food"]:
            top = json.loads((OUT / "stats" / f"{p['food']}.json").read_text())["top"]
        elif p["cells"].startswith("points/"):
            top = json.loads((OUT / f"{p['cells']}.top.json").read_text())
        else:
            top = []
        if top:
            fact = f"{top[0]['name']} alone accounts for {pct_text(top[0]['share'])} of the world's {p['stat']}."
            if len(top) > 2:
                fact += f" {top[1]['name']} and {top[2]['name']} follow with {pct_text(top[1]['share'])} and {pct_text(top[2]['share'])}."
        else:
            fact = "Half of all people live in a circle about 4,000 km wide over Asia."
        out.append(
            {
                "id": i,
                "stat": p["stat"],
                "prompt": p["prompt"],
                "mechanic": p["mechanic"],
                "mode": p["mode"],
                "target_pct": p.get("target_pct"),
                "radius_km": p.get("radius_km"),
                "perimeter_km": p.get("perimeter_km"),
                "best": best,
                "cells": f"/data/{p['cells']}",
                "stat_file": f"/data/stats/{p['food']}.json" if p["food"] else None,
                "view": {"center": p["view"][:2], "zoom": p["view"][2]},
                "fact": fact,
                "credit": p.get("credit", FOOD_CREDIT),
            }
        )
    write_json(OUT / "puzzles.json", out)


STEPS = {"land": step_land, "geo": step_geo, "population": step_population, "stats": step_stats, "points": step_points, "puzzles": step_puzzles}

if __name__ == "__main__":
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for s in sys.argv[1:] or list(STEPS):
        log(f"=== {s}")
        STEPS[s]()
