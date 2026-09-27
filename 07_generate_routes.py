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
# We keep routes that are mostly trail and drop near-duplicates, then add
# loops until the 7-10 km routes, run as a set, cover every trail they can.
#
# Each route also gets its elevation profile and total climb (from step 03),
# a cue sheet, and written turn-by-turn directions for people who'd rather
# run with a printed card than a GPS.
#
# Inputs:  `data/raw/osm_highways.json` (02), `data/raw/node_elevation.csv` (03),
#          `data/raw/osm_amenities.geojson` (02), `data/processed/ways.gpkg` (04)
# Output:  `data/processed/routes.geojson`: one feature per route, with its
#          distance, climb, trail/street share, profile, cue sheet, directions
#          `data/processed/coverage.json`: how much trail the 7-10 km routes
#          cover, and the bits they miss (for the About page)

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

MIN_KM, MAX_KM = 4, 12  # the whole library (the website slider's range)
FOCUS_KM = (7, 10)  # what we usually run: the website's default, so try harder
LOOP_TRIES = 1500  # over MIN_KM-MAX_KM
FOCUS_TRIES = 12000  # over FOCUS_KM: ~100 distinct 7-10 km loops
MIN_TRAIL = 0.80  # at least this share of the distance on park trails
MAX_STREET = 0.10  # at most this share on streets and sidewalks
MAX_REPEAT = 0.15  # loops: at most this share run twice
MAX_OVERLAP = 0.75  # routes sharing more of their edges than this count as the same
PROFILE_STEP_M = 50  # elevation profile resolution on the website
START_FINISH_M = 250  # toilets / water passed this near the start or finish go unmarked

# %% The network: OSM + our edits, main connected piece only
raw = json.loads((psp.DATA_RAW / "osm_highways.json").read_text())
ways = gpd.read_file(psp.DATA_PROCESSED / "ways.gpkg")
elevation = pd.read_csv(psp.DATA_RAW / "node_elevation.csv", index_col="node")
G = psp.walk_graph(raw, ways, elevation["elevation_m"])
G = G.subgraph(max(nx.connected_components(G), key=len)).copy()

# %% Start at the Park Centre (the nearest node to it)
start = psp.nearest_node(G, *psp.PARK_CENTRE_LAT_LON)
on = {G.edges[start, n]["name"] for n in G[start]}
print(f"Start: node {start}, on {on}")

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


# %% Fill the gaps: loops so that the 7-10 km routes, run as a set, cover every
# trail a good 7-10 km loop can reach (random turn points miss trails close to
# the start and at the far north end).
def trail_km(selected):
    run = {routing.edge_id(*s) for r in selected for s in r["steps"]}
    return sum(p[2] for e in run for p in H.edges[e]["pieces"] if p[1] == "park trail") / 1000  # fmt: skip


focus = [r for r in routes if FOCUS_KM[0] * 1000 <= r["length_m"] <= FOCUS_KM[1] * 1000]
fillers = routing.cover_gaps(H, start, routes, FOCUS_KM[0] * 1000, FOCUS_KM[1] * 1000, good)  # fmt: skip
all_trail_km = trail_km([{"steps": list(H.edges(keys=True))}])
print(
    f"7-10 km routes cover {trail_km(focus):.1f} of {all_trail_km:.1f} km of park trail; "
    f"{len(fillers)} added loops take that to {trail_km(focus + fillers):.1f} km"
)
routes += fillers
routes.sort(key=lambda r: (r["type"] != "loop", r["length_m"]))


# %% Coverage report for the About page: how much the 7-10 km routes run, and
# the trail bits they don't (with where to find them)
def stretch_name(e):
    return next((p[0] for p in H.edges[e]["pieces"] if p[0]), "(unnamed)")


run_710 = {routing.edge_id(*s) for r in focus + fillers for s in r["steps"]}
run_all = {routing.edge_id(*s) for r in routes for s in r["steps"]}
to_lon_lat = pyproj.Transformer.from_crs(psp.CRS_METRIC, psp.CRS_WGS84, always_xy=True)
uncovered = []
for u, v, k in H.edges(keys=True):
    e = routing.edge_id(u, v, k)
    metres = sum(p[2] for p in H.edges[e]["pieces"] if p[1] == "park trail")
    if metres > 0 and e not in run_710:
        middle = H.edges[e]["nodes"][len(H.edges[e]["nodes"]) // 2]
        lon, lat = to_lon_lat.transform(G.nodes[middle]["x"], G.nodes[middle]["y"])
        uncovered.append(
            {
                "trail": routing.clean_name(stretch_name(e)),
                "m": round(metres),
                "dead_end": H.degree(u) == 1 or H.degree(v) == 1,
                "in_other_routes": e in run_all,
                "lat": round(lat, 5),
                "lon": round(lon, 5),
            }
        )
coverage = {
    "park_trail_km": round(all_trail_km, 1),
    "band_km": list(FOCUS_KM),
    "band_routes": len(focus) + len(fillers),
    "band_trail_km": round(trail_km(focus + fillers), 1),
    "added_for_coverage": len(fillers),
    "all_routes_trail_km": round(trail_km(routes), 1),
    "uncovered": sorted(uncovered, key=lambda x: -x["m"]),
}
pd.DataFrame(coverage["uncovered"])

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
# ... and as sites (toilets / water / both) to mark on each route's profile
sites = [((p.x, p.y), kind) for p, kind in zip(*psp.amenity_sites(amenities)[["geometry", "kind"]].T.values)]  # fmt: skip

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
    passes_by = []  # toilets and water on the way (not at the start or finish)
    for site, kind in sites:
        kms, off = routing.site_passes(site, np.c_[x, y], at)
        passes_by += [
            [km, kind, off]
            for km in kms
            if START_FINISH_M <= km * 1000 <= at[-1] - START_FINISH_M
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
                "passes_by": sorted(passes_by),  # [km, toilets / water / both, m off]
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
(psp.DATA_PROCESSED / "coverage.json").write_text(json.dumps(coverage))
print(
    f"Saved {len(features)} routes ({counters['loop']} loops, "
    f"{counters['out-and-back']} out-and-backs), {out.stat().st_size / 1e6:.2f} MB"
)
