# %% [markdown]
# # 07 · Generate running routes from the Park Centre
#
# Our runs start and finish at the Park Centre (the ranger station on
# Cleveland Trail, just north of W 16th Ave). This makes a library of routes
# from there for the website to pick from:
#
# - **loops**: start -> A -> B -> start, where each leg avoids ground already
#   run, so there's (almost) no doubling back;
# - **out-and-backs**: out to a trail junction and back the same way.
#
# Trails are preferred: a metre of street costs 3x a metre of trail (see
# `routing.COST_PER_M`), so streets only appear where they link trails up.
# We keep routes that are mostly trail and drop near-duplicates.
#
# Each route also gets its elevation profile and total climb (from step 03),
# a cue sheet, and written turn-by-turn directions for people who'd rather
# run with a printed card than a GPS.
#
# Inputs:  `data/raw/osm_highways.json` (02), `data/raw/node_elevation.csv` (03),
#          `data/raw/osm_amenities.geojson` (02), `data/processed/ways.gpkg` (04)
# Output:  `data/processed/routes.geojson`: one feature per route, with its
#          distance, climb, trail/street share, profile, cue sheet, directions

# %%
import hashlib
import json

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import pyproj
from shapely import LineString

import psp
import routing

# OSM "Park Centre" information point (node 317125595), by the toilets and
# drinking water at Cleveland Trail and W 16th Ave.
START_LAT_LON = (49.25918, -123.22248)
MIN_KM, MAX_KM = 4, 12  # the whole library (the website slider's range)
FOCUS_KM = (7, 10)  # what we usually run: the website's default, so try harder
LOOP_TRIES = 1500  # over MIN_KM-MAX_KM
FOCUS_TRIES = 12000  # over FOCUS_KM: ~100 distinct 7-10 km loops
MIN_TRAIL = 0.80  # at least this share of the distance on park trails
MAX_STREET = 0.10  # at most this share on streets and sidewalks
MAX_REPEAT = 0.15  # loops: at most this share run twice
MAX_OVERLAP = 0.75  # routes sharing more of their edges than this count as the same
PROFILE_STEP_M = 50  # elevation profile resolution on the website

# %% The network: OSM + our edits, main connected piece only
raw = json.loads((psp.DATA_RAW / "osm_highways.json").read_text())
ways = gpd.read_file(psp.DATA_PROCESSED / "ways.gpkg")
elevation = pd.read_csv(psp.DATA_RAW / "node_elevation.csv", index_col="node")
G = psp.walk_graph(raw, ways, elevation["elevation_m"])
G = G.subgraph(max(nx.connected_components(G), key=len)).copy()

# %% Snap the start to the nearest node
to_metric = pyproj.Transformer.from_crs(psp.CRS_WGS84, psp.CRS_METRIC, always_xy=True)
start_xy = to_metric.transform(START_LAT_LON[1], START_LAT_LON[0])
node_ids = list(G.nodes)
gaps = np.hypot(*(np.array([(G.nodes[n]["x"], G.nodes[n]["y"]) for n in node_ids]) - start_xy).T)  # fmt: skip
start = node_ids[gaps.argmin()]
on = {G.edges[start, n]["name"] for n in G[start]}
print(f"Start: node {start}, {gaps.min():.0f} m from the Park Centre, on {on}")

# %% Contract: junction-to-junction edges
H = routing.contract(G, keep=[start])
print(f"{H.number_of_nodes():,} junctions, {H.number_of_edges():,} edges")

# %% Generate candidates
loops = routing.make_loops(
    H, start, MIN_KM * 1000, MAX_KM * 1000, tries=LOOP_TRIES, seed=1
) + routing.make_loops(
    H, start, FOCUS_KM[0] * 1000, FOCUS_KM[1] * 1000, tries=FOCUS_TRIES, seed=2
)
out_and_backs = routing.make_out_and_backs(H, start, MIN_KM * 1000, MAX_KM * 1000)
STATS = ["type", "length_m", "trail_share", "street_share", "repeat_share"]
candidates = pd.DataFrame([{k: r[k] for k in STATS} for r in loops + out_and_backs])
candidates.groupby("type").describe().T.round(2)


# %% Keep the good ones, and only one of each near-duplicate
def good(r):
    return (
        r["trail_share"] >= MIN_TRAIL
        and r["street_share"] <= MAX_STREET
        and (r["type"] == "out-and-back" or r["repeat_share"] <= MAX_REPEAT)
    )


routes = routing.pick_distinct(
    [r for r in loops if good(r)], MAX_OVERLAP
) + routing.pick_distinct([r for r in out_and_backs if good(r)], MAX_OVERLAP)
routes.sort(key=lambda r: (r["type"] != "loop", r["length_m"]))

summary = pd.DataFrame(
    [{"type": r["type"], "km": r["length_m"] / 1000} for r in routes]
).assign(band=lambda d: pd.cut(d["km"], range(MIN_KM, MAX_KM + 1)))
summary.groupby(["type", "band"], observed=False).size().unstack(0)

# %% Landmarks for the directions: toilets and drinking water
amenities = gpd.read_file(psp.DATA_RAW / "osm_amenities.geojson").to_crs(psp.CRS_METRIC)
LANDMARK = {"toilets": "toilets", "drinking_water": "water fountain"}
landmarks = [
    (p.x, p.y, LANDMARK[a]) for p, a in zip(amenities.geometry, amenities["amenity"])
]

# %% One route in detail, to check the directions read well
example = next(r for r in routes if 8000 <= r["length_m"] <= 9000)
routing.route_name(H, example["steps"])
routing.directions(G, H, example["steps"], landmarks=landmarks)

# %% As GeoJSON: the line in running order, plus stats, profile and directions
to_lon_lat = pyproj.Transformer.from_crs(psp.CRS_METRIC, psp.CRS_WGS84, always_xy=True)
counters = {"loop": 0, "out-and-back": 0}
features = []
for r in routes:
    counters[r["type"]] += 1
    nodes = routing.route_nodes(H, r["steps"])
    line = LineString([(G.nodes[n]["x"], G.nodes[n]["y"]) for n in nodes]).simplify(2)
    lons, lats = to_lon_lat.transform(*line.xy)
    at, z, x, y = routing.elevation_profile(G, nodes)
    gain, loss = routing.climb(z)
    every = slice(None, None, PROFILE_STEP_M // 10)  # profile samples are 10 m
    p_lons, p_lats = to_lon_lat.transform(x[every], y[every])
    profile = [
        [round(d / 1000, 2), round(h, 1), round(lo, 5), round(la, 5)]
        for d, h, lo, la in zip(at[every], z[every], p_lons, p_lats)
    ]
    features.append(
        {
            "type": "Feature",
            "properties": {
                # Position in the library (L01 = shortest loop): changes when
                # the library does. `key` is a fingerprint of the exact path,
                # so a shared link keeps finding the same route.
                "id": f"{'L' if r['type'] == 'loop' else 'OB'}{counters[r['type']]:02d}",
                "key": hashlib.sha1(" ".join(map(str, nodes)).encode()).hexdigest()[:8],
                "type": r["type"],
                "name": routing.route_name(H, r["steps"]),
                "km": round(r["length_m"] / 1000, 2),
                "trail_pct": round(100 * r["trail_share"]),
                "street_pct": round(100 * r["street_share"]),
                "repeat_pct": round(100 * r["repeat_share"]),
                "gain_m": round(gain),
                "loss_m": round(loss),
                "low_m": round(float(z.min())),
                "high_m": round(float(z.max())),
                "profile": profile,  # [km, m, lon, lat]
                "cues": routing.cue_sheet(H, r["steps"]),
                "directions": routing.directions(G, H, r["steps"], landmarks=landmarks),
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[round(x, 5), round(y, 5)] for x, y in zip(lons, lats)],
            },
        }
    )

# %% Save
psp.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
out = psp.DATA_PROCESSED / "routes.geojson"
out.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
print(
    f"Saved {len(features)} routes ({counters['loop']} loops, "
    f"{counters['out-and-back']} out-and-backs), {out.stat().st_size / 1e6:.2f} MB"
)
