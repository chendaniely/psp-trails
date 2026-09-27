# %% [markdown]
# # 08 · Plan an ultra: every trail in the park, from the Park Centre
#
# One continuous route that runs every park trail at least once, starting and
# finishing at the Park Centre: the aid station, with free parking along
# W 16th Ave and toilets and water right there.
#
# Covering every edge of a network with the shortest closed walk is the route
# inspection ("Chinese postman") problem; `routing.postman_route` solves it
# for our case, where only trails must be run and streets may link them.
#
# Short dead-end offshoots (<= OFFSHOOT_M, run out and back) are optional:
# they're on the map and in the directions, and there's a GPX without them.
#
# The idea of running every Pacific Spirit trail as one ultra isn't ours:
# there's an existing Pacific Spirit Park ultra route, credited on the Ultra
# page. This is our version, computed from the trail data.
#
# Inputs:  `data/raw/osm_highways.json` (02), `data/raw/osm_amenities.geojson`
#          (02), `data/raw/node_elevation.csv` (03), `data/processed/ways.gpkg` (04)
# Output:  `data/processed/ultra.json`: stats, map layers, GPX lines,
#          elevation profile and directions for the Ultra page

# %%
import collections
import json
import math
from itertools import pairwise

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import pyproj
from shapely import LineString

import psp
import routing

OFFSHOOT_M = 400  # dead-end branches up to this long are optional
AID_RADIUS_M = 250  # passing this close to the Park Centre counts as an aid stop
AMENITY_RADIUS_M = 40  # toilets / water this close to the route are "on the way"
DETOUR_M = 100  # ... and this close, worth a short detour (at the closest pass)
SITE_M = 25  # toilets and water this close together are one stop
MARKER_KM = 5  # km markers on the map

# What each extra metre costs when joining the trails up. It's an ultra
# already: a bit of road beats running a trail a second time.
REPEAT_PER_M = 2.5  # a trail stretch run a second time
ULTRA_PER_M = {"park trail": 1, "connector": 1, "other path": 1.2, "sidewalk": 1.5, "street": 1.5}  # fmt: skip
OUR_ROADS = {
    "Imperial Drive",
    "West 29th Avenue",
}  # we run these anyway: as good as trail

# %% The network: OSM + our edits, main connected piece, with elevations
raw = json.loads((psp.DATA_RAW / "osm_highways.json").read_text())
ways = gpd.read_file(psp.DATA_PROCESSED / "ways.gpkg")
elevation = pd.read_csv(psp.DATA_RAW / "node_elevation.csv", index_col="node")
G = psp.walk_graph(raw, ways, elevation["elevation_m"])
G = G.subgraph(max(nx.connected_components(G), key=len)).copy()
start = psp.nearest_node(G, *psp.PARK_CENTRE_LAT_LON)
H = routing.contract(G, keep=[start])


# %% Every junction-to-junction stretch that's mostly park trail must be run
def trail_m(d):
    return sum(p[2] for p in d["pieces"] if p[1] == "park trail")


required = {
    routing.edge_id(u, v, k)
    for u, v, k, d in H.edges(keys=True, data=True)
    if trail_m(d) >= 0.5 * d["length"]
}
all_trail_km = sum(trail_m(d) for _, _, d in H.edges(data=True)) / 1000
print(f"{len(required)} stretches to run; {all_trail_km:.1f} km of park trail")

# %% The cost of each stretch, for the ultra (see REPEAT_PER_M)
for u, v, k, d in H.edges(keys=True, data=True):
    if routing.edge_id(u, v, k) in required:
        d["ultra_cost"] = d["length"] * REPEAT_PER_M
    else:
        d["ultra_cost"] = sum(
            m * (1 if name in OUR_ROADS else ULTRA_PER_M[kind])
            for name, kind, m in d["pieces"]
        )

# %% The route
steps = routing.postman_route(H, required, start, cost="ultra_cost")
stats = routing.route_stats(H, steps)
optional = routing.offshoots(H, OFFSHOOT_M) & {routing.edge_id(*s) for s in steps}
main_steps = [s for s in steps if routing.edge_id(*s) not in optional]
length = {
    "full": stats["length_m"],
    "main": sum(H.edges[s]["length"] for s in main_steps),
}
covered_km = sum(trail_m(H.edges[e]) for e in {routing.edge_id(*s) for s in steps}) / 1000  # fmt: skip
print(
    f"{length['full'] / 1000:.1f} km ({length['main'] / 1000:.1f} km without "
    f"optional offshoots); park trail covered {covered_km:.1f} of {all_trail_km:.1f} km"
)

# %% Positions along the route
to_lon_lat = pyproj.Transformer.from_crs(psp.CRS_METRIC, psp.CRS_WGS84, always_xy=True)
nodes = routing.route_nodes(H, steps)
xy = np.array([(G.nodes[n]["x"], G.nodes[n]["y"]) for n in nodes])
along = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])


def lon_lat_line(line_nodes, tolerance_m=2):
    line = LineString([(G.nodes[n]["x"], G.nodes[n]["y"]) for n in line_nodes])
    lons, lats = to_lon_lat.transform(*line.simplify(tolerance_m).xy)
    return [[round(x, 5), round(y, 5)] for x, y in zip(lons, lats)]


def passes(gap, within):
    """Metres along the route of each separate pass within `within` metres of
    something (`gap`: its distance from each route point), at the closest point."""
    near = gap <= within
    out, i = [], 0
    while i < len(near):
        if near[i]:
            j = i
            while j < len(near) and near[j]:
                j += 1
            out.append(float(along[i + np.argmin(gap[i:j])]))
            i = j
        else:
            i += 1
    return out


# %% Aid station passes (near the Park Centre)
centre = np.array([G.nodes[start]["x"], G.nodes[start]["y"]])
aid_km = [round(m / 1000, 1) for m in passes(np.hypot(*(xy - centre).T), AID_RADIUS_M)]

# %% Toilets and water: one site where they're within SITE_M of each other
amenities = gpd.read_file(psp.DATA_RAW / "osm_amenities.geojson").to_crs(psp.CRS_METRIC)
KIND = {"toilets": "toilets", "drinking_water": "water"}
sites = []
for point, amenity in zip(amenities.geometry, amenities["amenity"]):
    site = next((s for s in sites if math.dist(s["xy"], (point.x, point.y)) <= SITE_M), None)  # fmt: skip
    if site:
        site["kinds"].add(KIND[amenity])
    else:
        sites.append({"xy": (point.x, point.y), "kinds": {KIND[amenity]}})

# The ones on the way: every pass within AMENITY_RADIUS_M, or a detour up to DETOUR_M
on_the_way = []  # (km, kind), away from the Park Centre
for site in sites:
    gap = np.hypot(xy[:, 0] - site["xy"][0], xy[:, 1] - site["xy"][1])
    site["off_m"] = round(gap.min())
    if site["off_m"] <= AMENITY_RADIUS_M:
        site["km"] = [round(m / 1000, 1) for m in passes(gap, AMENITY_RADIUS_M)]
    elif site["off_m"] <= DETOUR_M:
        site["km"] = [round(along[np.argmin(gap)] / 1000, 1)]
    else:
        site["km"] = []
    site["aid"] = math.dist(site["xy"], centre) <= AID_RADIUS_M
    if not site["aid"]:
        on_the_way += [(km, kind) for km in site["km"] for kind in site["kinds"]]
on_the_way.sort()
pd.DataFrame([s for s in sites if s["km"]])


def merge(stops, within_km=1.0):
    """Passes within `within_km` of each other are one stop: [first km, last km, kinds]."""
    merged = []
    for km, kind in stops:
        if merged and km - merged[-1][1] <= within_km:
            merged[-1][1] = km
            merged[-1][2] |= {kind}
        else:
            merged.append([km, km, {kind}])
    return [[a, b, sorted(kinds)] for a, b, kinds in merged]


aid_stops = merge([(km, "aid") for km in aid_km], within_km=3.0)
water_stops = merge(on_the_way)
aid_stops, pd.DataFrame(water_stops, columns=["from km", "to km", "what"])

# %% Map layers: first time along a stretch, a repeat, or an optional offshoot
seen, segments = set(), []
for s in steps:
    e = routing.edge_id(*s)
    cls = "optional" if e in optional else "repeat" if e in seen else "first"
    seen.add(e)
    chain = routing.route_nodes(H, [s])
    if segments and segments[-1]["class"] == cls:
        segments[-1]["nodes"] += chain[1:]
    else:
        segments.append({"class": cls, "nodes": chain})
by_class = collections.Counter()
for seg in segments:
    by_class[seg["class"]] += sum(G.edges[a, b]["length"] for a, b in pairwise(seg["nodes"]))  # fmt: skip
{k: round(v / 1000, 1) for k, v in by_class.items()}

# %% km markers, and the elevation profile (every 100 m)
points = []
for km in range(MARKER_KM, int(along[-1] / 1000) + 1, MARKER_KM):
    x = np.interp(km * 1000, along, xy[:, 0])
    y = np.interp(km * 1000, along, xy[:, 1])
    points.append({"kind": "km", "label": str(km), "xy": (x, y)})
for site in sites:
    if site["km"]:
        kind = "both" if len(site["kinds"]) == 2 else next(iter(site["kinds"]))
        where = "at the Park Centre" if site["aid"] else f"km {', '.join(map(str, site['km']))}"  # fmt: skip
        if site["off_m"] > AMENITY_RADIUS_M:
            where += f", {site['off_m']} m off the route"
        points.append({"kind": kind, "label": where, "xy": site["xy"]})

at, z, px, py = routing.elevation_profile(G, nodes)
gain, loss = routing.climb(z)
every = slice(None, None, 10)  # profile samples are 10 m apart
p_lons, p_lats = to_lon_lat.transform(px[every], py[every])
profile = [
    [round(d / 1000, 2), round(h, 1), round(lo, 5), round(la, 5)]
    for d, h, lo, la in zip(at[every], z[every], p_lons, p_lats)
]

# %% Directions (turning around at dead ends, flagging the optional offshoots)
landmarks = [
    (p.x, p.y, {"toilets": "toilets", "drinking_water": "water fountain"}[a])
    for p, a in zip(amenities.geometry, amenities["amenity"])
]
directions = routing.directions(
    G, H, steps, landmarks=landmarks, stop_at_turnaround=False, optional=optional
)
len(directions)


# %% Save for the Ultra page
def point_feature(p):
    lon, lat = to_lon_lat.transform(*p["xy"])
    return {
        "type": "Feature",
        "properties": {"kind": p["kind"], "label": p["label"]},
        "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
    }


ultra = {
    "stats": {
        "km": round(length["full"] / 1000, 1),
        "km_without_offshoots": round(length["main"] / 1000, 1),
        "trail_km": round(covered_km, 1),
        "park_trail_km": round(all_trail_km, 1),
        "repeat_km": round(by_class["repeat"] / 1000, 1),
        "offshoot_km": round(by_class["optional"] / 1000, 1),
        "street_pct": round(100 * stats["street_share"]),
        "gain_m": round(gain),
        "loss_m": round(loss),
        "aid": [
            [a, b] for a, b, _ in aid_stops
        ],  # [from km, to km] near the Park Centre
        "on_the_way": water_stops,  # [from km, to km, ["toilets", "water"]], not at the Park Centre
    },
    "segments": {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"class": seg["class"]},
                "geometry": {
                    "type": "LineString",
                    "coordinates": lon_lat_line(seg["nodes"]),
                },
            }
            for seg in segments
        ],
    },
    "points": {
        "type": "FeatureCollection",
        "features": [point_feature(p) for p in points],
    },
    "full": lon_lat_line(nodes),
    "main": lon_lat_line(routing.route_nodes(H, main_steps)),
    "profile": profile,
    "directions": directions,
}
psp.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
out = psp.DATA_PROCESSED / "ultra.json"
out.write_text(json.dumps(ultra))
print(
    f"Saved {out.name}: {ultra['stats']['km']} km, ↑{ultra['stats']['gain_m']} m, "
    f"{len(directions)} directions, {out.stat().st_size / 1e6:.2f} MB"
)
