# %% [markdown]
# # 05 · Check the connections routing depends on
#
# Two kinds of check, both on the walkable network (OSM + our edits):
#
# 1. **Crossings.** Chancellor Blvd, University Blvd and W 16th Ave cut the
#    park into pieces. Wherever a listed trail comes within 40 m of one, we
#    take the trail's nodes on each side and find the shortest path across. If
#    it's much longer than the straight-line gap, the sides aren't joined there
#    and routes would detour.
# 2. **Links.** Specific connections we rely on, e.g. Vine Maple Trail to
#    Blanca St and the 16th Ave cycleway. Each end is a point that snaps to the
#    nearest node; the path between them must be no longer than `max_m`.
#
# A failure means OSM changed or is missing a link: add a connector in
# `data/manual/add_connectors.geojson` (see step 03).
#
# Inputs:  `data/raw/osm_highways.json` (02), `data/processed/ways.gpkg` (03)
# Output:  `output/connection_check.csv`

# %%
import json
from itertools import pairwise

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely import MultiPoint, Point

import psp

# Street -> trails that cross it (or meet across it).
CROSSINGS = {
    "Chancellor Boulevard": ["Spanish Trail", "Pioneer Trail", "Salish Trail"],
    "University Boulevard": ["Salish Trail", "Cleveland Trail"],
    "West 16th Avenue": [
        "Douglas Fir",
        "Sword Fern Trail",
        "Cleveland Trail",
        "Salal Trail",
        "Salish Trail",
    ],
}
NEAR_STREET_M = 40  # trail nodes this close to the street count as "at" it
SITE_RADIUS_M = 120  # how far either side of the street to look
DETOUR_SLACK_M = 60  # network path may be this much longer than straight across

# Connections we rely on: (name, from (lat, lon), to (lat, lon), max network m)
LINKS = [
    ("Vine Maple Trail east end to Blanca St",
     (49.25943, -123.21554), (49.25908, -123.21542), 120),
    ("Vine Maple Trail east end to 16th Ave cycleway",
     (49.25943, -123.21554), (49.25900, -123.21568), 100),
    ("16th Ave cycleway at Blanca to Salal Trail",
     (49.25900, -123.21568), (49.25872, -123.21900), 350),
    ("16th Ave cycleway at Blanca to Heron Trail",
     (49.25900, -123.21568), (49.25872, -123.21888), 350),
]  # fmt: skip

# %% Build the walkable network
raw = json.loads((psp.DATA_RAW / "osm_highways.json").read_text())
ways = gpd.read_file(psp.DATA_PROCESSED / "ways.gpkg")
G = psp.walk_graph(raw, ways)
print(f"{G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")

node_xy = gpd.GeoSeries(
    {n: Point(d["x"], d["y"]) for n, d in G.nodes(data=True)}, crs=psp.CRS_METRIC
)
ways_m = ways.to_crs(psp.CRS_METRIC)


# %% Helpers
def shortest(from_nodes, to_nodes):
    """Shortest walk from any of `from_nodes` to any of `to_nodes`: (m, via)."""
    dist, path = nx.multi_source_dijkstra(
        G, set(from_nodes), cutoff=2000, weight="length"
    )
    reached = [(dist[n], n) for n in to_nodes if n in dist]
    if not reached:
        return None, None
    metres, end = min(reached)
    names = [G.edges[u, v]["name"] or "(unnamed)" for u, v in pairwise(path[end])]
    return round(metres), " > ".join(dict.fromkeys(names))  # distinct, in order


def pieces(geom):
    return list(getattr(geom, "geoms", [geom]))


def verdict(network, allowed):
    if network is None:
        return "NOT CONNECTED"
    return "ok" if network <= allowed else "DETOUR"


def names_at(nodes, wanted):
    return ", ".join(sorted({G.edges[e]["name"] for n in nodes for e in G.edges(n)} & set(wanted)))  # fmt: skip


def lat_lon(point_m):
    p = gpd.GeoSeries([point_m], crs=psp.CRS_METRIC).to_crs(psp.CRS_WGS84)[0]
    return round(p.y, 5), round(p.x, 5)


# %% 1. Crossings
rows = []
for street, trails in CROSSINGS.items():
    street_line = ways_m.loc[ways_m["name"] == street].union_all()
    street_band = street_line.buffer(12)
    trail_nodes = {n for u, v, name in G.edges(data="name") if name in trails for n in (u, v)}  # fmt: skip
    on_trail = node_xy[list(trail_nodes)]
    at_street = on_trail[on_trail.distance(street_line) < NEAR_STREET_M]

    # Nearby trail nodes that are within 80 m of each other form one site.
    for site in pieces(at_street.buffer(NEAR_STREET_M).union_all()):
        disk = site.centroid.buffer(SITE_RADIUS_M)
        sides = sorted(pieces(disk.difference(street_band)), key=lambda p: -p.area)
        a = [n for n, p in on_trail.items() if sides[0].contains(p)]
        b = [n for n, p in on_trail.items() if sides[1].contains(p)]
        lat, lon = lat_lon(site.centroid)
        row = {
            "check": "crossing",
            "name": f"{street}: {names_at(a, trails)} / {names_at(b, trails)}",
            "lat": lat,
            "lon": lon,
        }
        if not a or not b:
            rows.append(row | {"result": "listed trails on one side only"})
            continue
        straight = MultiPoint(list(node_xy[a])).distance(MultiPoint(list(node_xy[b])))
        network, via = shortest(a, b)
        rows.append(
            row
            | {
                "straight_m": round(straight),
                "network_m": network,
                "result": verdict(network, straight + DETOUR_SLACK_M),
                "via": via,
            }
        )

# %% 2. Links
for name, start, end, max_m in LINKS:
    ends = gpd.GeoSeries(
        [Point(lon, lat) for lat, lon in (start, end)], crs=psp.CRS_WGS84
    ).to_crs(psp.CRS_METRIC)
    a, b = (node_xy.distance(p).idxmin() for p in ends)
    network, via = shortest([a], [b])
    rows.append(
        {
            "check": "link",
            "name": name,
            "lat": start[0],
            "lon": start[1],
            "straight_m": round(ends.iloc[0].distance(ends.iloc[1])),
            "network_m": network,
            "result": verdict(network, max_m),
            "via": via,
        }
    )

checks = pd.DataFrame(rows)
checks.drop(columns="via")

# %% Save, and flag anything that isn't joined up
psp.OUTPUT.mkdir(exist_ok=True)
checks.to_csv(psp.OUTPUT / "connection_check.csv", index=False)
problems = checks[checks["result"].isin(["DETOUR", "NOT CONNECTED"])]
if problems.empty:
    print(f"All {len(checks)} connection checks pass.")
else:
    print(f"WARNING: {len(problems)} connection check(s) fail:")
    print(problems.drop(columns="via").to_string(index=False))
