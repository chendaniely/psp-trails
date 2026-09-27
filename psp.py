"""Shared settings and small helpers for the numbered psp-trails scripts.

Run the scripts from the repository root, either with `make` or cell-by-cell
(`# %%` markers) in Positron / VS Code. Everything here is imported with
`import psp`.
"""

import logging
import math
import time
from itertools import pairwise
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
import pyproj
import requests
from shapely import LineString, Point

# Paths --------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
DATA_RAW = ROOT / "data" / "raw"  # downloads, never edited by hand
DATA_MANUAL = ROOT / "data" / "manual"  # our own edits (see 04)
DATA_PROCESSED = ROOT / "data" / "processed"
DATA_CACHE = ROOT / "data" / "cache"  # osmnx's response cache (Nominatim)
OUTPUT = ROOT / "output"

# Pacific Spirit Regional Park ----------------------------------------------

# https://www.openstreetmap.org/relation/11683817
PARK_OSM_RELATION = 11683817

# Metro Vancouver "Regional Parks Boundaries (Open Data)" layer, park code PAC.
# https://open-data-portal-metrovancouver.hub.arcgis.com/datasets/regional-parks-boundaries
METROVAN_PARKS_LAYER = (
    "https://services6.arcgis.com/56eqCzQ5SZhBaDST/arcgis/rest/services/"
    "RegionalParksBoundaries_OpenData/FeatureServer/0"
)
PARK_METROVAN_CODE = "PAC"

# Where our runs start and finish: the Park Centre (ranger station) on
# Cleveland Trail at W 16th Ave, by the toilets and drinking water. OSM
# "Park Centre" information point, node 317125595.
PARK_CENTRE_LAT_LON = (49.25918, -123.22248)

# How far past the park boundary to fetch neighbouring streets and paths, so
# trails can be linked up via streets instead of doubling back.
BUFFER_M = 400

# NAD83 / UTM zone 10N: a metric CRS for buffering and lengths in Vancouver.
CRS_METRIC = "EPSG:26910"
CRS_WGS84 = "EPSG:4326"

# Elevation ---------------------------------------------------------------------

# NRCan High Resolution DEM mosaic, 2 m, bare earth (DTM: the ground under the
# trees, not the canopy), as a Cloud Optimized GeoTIFF. The tile covering the
# park was found with the STAC API at https://datacube.services.geo.ca/stac/api/
# (collection hrdem-mosaic-2m). Open Government Licence - Canada.
DTM_URL = (
    "https://canelevation-dem.s3.ca-central-1.amazonaws.com/"
    "hrdem-mosaic-2m/2_3-mosaic-2m-dtm.tif"
)


# OpenStreetMap / Overpass ----------------------------------------------------

USER_AGENT = "psp-trails/0.1 (Pacific Spirit trail routing for a running group)"

# Tried in order. The main server is often busy (HTTP 504), so fall back to
# mirrors rather than waiting on it.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def overpass(query: str, tries_per_server: int = 2, pause_s: int = 30) -> dict:
    """Run an Overpass QL query, moving on to the next server when one fails."""
    for url in OVERPASS_URLS:
        for attempt in range(1, tries_per_server + 1):
            print(f"Overpass {url} (try {attempt}) ...", flush=True)
            try:
                resp = requests.post(
                    url,
                    data={"data": query},
                    headers={"User-Agent": USER_AGENT},
                    timeout=300,
                )
            except requests.RequestException as err:
                print(f"  {type(err).__name__}")
                break  # unreachable: try the next server
            if resp.ok:
                print(f"  OK, {len(resp.content) / 1e6:.1f} MB")
                return resp.json()
            print(f"  HTTP {resp.status_code}")
            if resp.status_code not in (429, 504):
                break  # not a "busy" error: waiting won't help
            if attempt < tries_per_server:
                time.sleep(pause_s)
    raise RuntimeError("Every Overpass server failed; try again later.")


def overpass_poly(polygon) -> str:
    """Shapely polygon (lon/lat) -> Overpass `poly:` filter string ("lat lon ...").

    Overpass takes a single ring, so any holes in `polygon` are ignored.
    """
    return " ".join(f"{lat:.6f} {lon:.6f}" for lon, lat in polygon.exterior.coords)


def overpass_ways_to_gdf(data: dict) -> gpd.GeoDataFrame:
    """One row per OSM way from an `out geom;` Overpass response, tags as columns."""
    rows = [
        {
            "osm_id": el["id"],
            **el.get("tags", {}),
            "geometry": LineString((pt["lon"], pt["lat"]) for pt in el["geometry"]),
        }
        for el in data["elements"]
        if el["type"] == "way"
    ]
    return gpd.GeoDataFrame(rows, crs=CRS_WGS84)


def overpass_points_to_gdf(data: dict) -> gpd.GeoDataFrame:
    """One point per element from an `out center tags;` response, tags as columns.

    Nodes use their own position; ways and relations (e.g. a toilet building)
    use their centre.
    """
    rows = [
        {
            "osm_type": el["type"],
            "osm_id": el["id"],
            **el.get("tags", {}),
            "geometry": Point(el["lon"], el["lat"])
            if el["type"] == "node"
            else Point(el["center"]["lon"], el["center"]["lat"]),
        }
        for el in data["elements"]
    ]
    return gpd.GeoDataFrame(rows, crs=CRS_WGS84)


def setup_osmnx() -> None:
    """Identify ourselves to OSM services and keep osmnx's cache in data/."""
    ox.settings.cache_folder = DATA_CACHE
    ox.settings.http_user_agent = USER_AGENT
    ox.settings.log_console = True
    ox.settings.log_level = logging.WARNING  # INFO prints every request URL


# Classifying OSM ways ---------------------------------------------------------

# highway=* values that are trails/paths rather than roads.
TRAIL_HIGHWAYS = {
    "path",
    "footway",
    "track",
    "bridleway",
    "cycleway",
    "steps",
    "pedestrian",
}
# footway=* values that are part of a street, not a trail.
SIDEWALK_FOOTWAYS = {"sidewalk", "crossing", "traffic_island", "access_aisle"}
# Ways a running group can't (or shouldn't) use.
NOT_OPEN_HIGHWAYS = {"construction", "proposed", "abandoned", "corridor"}
NOT_OPEN_ACCESS = {"private", "no", "customers", "delivery", "agricultural", "permit"}
OPEN_FOOT = {"yes", "designated", "permissive"}
NOISE_SERVICE = {"driveway", "parking_aisle", "drive-through"}
# bicycle=* values, summarised the way the official park map does.
BIKE_SHARED = {"yes", "designated", "permissive"}
BIKE_HIKING_ONLY = {"no", "dismount"}


def _as_lines(ways):
    """Way geometries in metres; area ways (closed polygons) as their outline."""
    lines = ways.geometry.to_crs(CRS_METRIC)
    return lines.where(lines.geom_type != "Polygon", lines.boundary)


def share_within(ways, areas) -> pd.Series:
    """Fraction of each way's length that lies inside `areas` (any polygons)."""
    lines = _as_lines(ways)
    inside = lines.intersection(areas.to_crs(CRS_METRIC).union_all())
    return (inside.length / lines.length).fillna(0)


def classify_ways(ways, park, in_park_share: float = 0.5):
    """Add `kind`, `bike`, `in_park` and `length_m` columns to OSM highway ways.

    kind:  "park trail", "other path", "sidewalk", "street" or "excluded"
           (private, construction, driveways, parking aisles, permit-only).
    bike:  for park trails, "shared", "hiking only" or "untagged", from bicycle=*.
    in_park: at least `in_park_share` of the way's length lies within 10 m of
           the park boundary polygon `park`.
    """
    ways = ways.copy()
    ways["length_m"] = _as_lines(ways).length.round(1)
    park_plus_10m = park.to_crs(CRS_METRIC).buffer(10)
    ways["in_park"] = share_within(ways, park_plus_10m) >= in_park_share

    def tag(key):
        # A tag nobody used in this download has no column; treat it as blank.
        return ways.get(key, pd.Series(None, index=ways.index, dtype=object))

    excluded = (
        ways["highway"].isin(NOT_OPEN_HIGHWAYS)
        | (tag("access").isin(NOT_OPEN_ACCESS) & ~tag("foot").isin(OPEN_FOOT))
        | tag("foot").isin({"no", "permit"})
        | tag("service").isin(NOISE_SERVICE)
    )
    sidewalk = tag("footway").isin(SIDEWALK_FOOTWAYS)
    trail = ways["highway"].isin(TRAIL_HIGHWAYS) & ~sidewalk

    ways["kind"] = "street"
    ways.loc[sidewalk, "kind"] = "sidewalk"
    ways.loc[trail, "kind"] = "other path"
    ways.loc[trail & ways["in_park"], "kind"] = "park trail"
    ways.loc[excluded, "kind"] = "excluded"

    ways["bike"] = None
    is_trail = ways["kind"] == "park trail"
    ways.loc[is_trail, "bike"] = bike_access(tag("bicycle")[is_trail])
    return ways


def bike_access(bicycle: pd.Series) -> pd.Series:
    """Bikes on a park trail, from its bicycle=* tag: shared, hiking only or untagged."""
    bike = pd.Series("untagged", index=bicycle.index, dtype=object)
    bike[bicycle.isin(BIKE_SHARED)] = "shared"
    bike[bicycle.isin(BIKE_HIKING_ONLY)] = "hiking only"
    return bike


# Toilets and drinking water --------------------------------------------------------

AMENITY_KIND = {"toilets": "toilets", "drinking_water": "water"}
NOT_PUBLIC = {"private", "no", "customers"}


def amenity_sites(
    amenities: gpd.GeoDataFrame, within_m: float = 25
) -> gpd.GeoDataFrame:
    """Public toilets and drinking water (step 02), as sites: the ones within
    `within_m` of each other are one. `kind` is toilets, water or both; the
    point is the first one found (metric CRS)."""
    access = amenities.get(
        "access", pd.Series(None, index=amenities.index, dtype=object)
    )
    public = amenities[~access.isin(NOT_PUBLIC)].to_crs(CRS_METRIC)
    sites = []  # [point, {kinds}]
    for point, amenity in zip(public.geometry, public["amenity"]):
        site = next((s for s in sites if s[0].distance(point) <= within_m), None)
        if site:
            site[1].add(AMENITY_KIND[amenity])
        else:
            sites.append([point, {AMENITY_KIND[amenity]}])
    return gpd.GeoDataFrame(
        {"kind": ["both" if len(k) == 2 else next(iter(k)) for _, k in sites]},
        geometry=[p for p, _ in sites],
        crs=CRS_METRIC,
    )


# Walkable network ---------------------------------------------------------------


def walk_graph(
    raw: dict, ways: gpd.GeoDataFrame, elevation=None, snap_m: float = 5
) -> nx.Graph:
    """The network we route on, as an undirected graph.

    `raw` is the Overpass response from step 02 (it has each way's node ids, so
    ways that share a node are joined). `ways` is step 04's output: removed and
    excluded ways are left out, cut ways lose the part between `cut_from` and
    `cut_to`, and our added connectors are joined to the nearest node within
    `snap_m` metres at each end.

    `elevation` (optional) maps OSM node id -> metres (step 03). Nodes along a
    bridge get heights interpolated between its ends: the ground model gives
    the creek bed underneath, not the bridge deck.

    Nodes carry `x`, `y` (metres, CRS_METRIC) and `z` if `elevation` is
    given; edges carry `length` (m),
    `osm_id` (None for our connectors), `name` and `kind` (see classify_ways,
    plus "connector").
    """
    usable = ways[ways["removed"].isna() & (ways["kind"] != "excluded")]
    osm_ways = usable[usable["osm_id"].notna()]
    kind_of = dict(zip(osm_ways["osm_id"].astype(int), osm_ways["kind"]))
    osm_ids = set(kind_of)
    cuts = {
        int(way): (int(a), int(b))
        for way, a, b in usable[["osm_id", "cut_from", "cut_to"]].dropna().values
    }
    to_metric = pyproj.Transformer.from_crs(CRS_WGS84, CRS_METRIC, always_xy=True)

    G = nx.Graph()
    for el in raw["elements"]:
        if el["type"] != "way" or el["id"] not in osm_ids:
            continue
        lons = [pt["lon"] for pt in el["geometry"]]
        lats = [pt["lat"] for pt in el["geometry"]]
        # Round to the centimetre: projection maths differs in the last digits
        # between machines (Mac vs the Linux CI), and those crumbs could
        # break ties between equal routes differently. Rounded, they can't.
        xs, ys = np.round(to_metric.transform(lons, lats), 2)
        points = list(zip(el["nodes"], xs, ys))
        cut = range(0)
        if el["id"] in cuts:
            i, j = sorted(el["nodes"].index(n) for n in cuts[el["id"]])
            cut = range(i, j)  # segments i -> i+1, ..., j-1 -> j
        for k, ((a, xa, ya), (b, xb, yb)) in enumerate(pairwise(points)):
            if k in cut:
                continue
            G.add_node(a, x=xa, y=ya)
            G.add_node(b, x=xb, y=yb)
            G.add_edge(
                a,
                b,
                length=round(math.hypot(xb - xa, yb - ya), 2),
                osm_id=el["id"],
                name=el.get("tags", {}).get("name"),
                kind=kind_of[el["id"]],
            )

    # Our connectors: snap both ends onto the network.
    node_ids = np.array(list(G.nodes))
    node_xy = np.array([(G.nodes[n]["x"], G.nodes[n]["y"]) for n in node_ids])
    connectors = usable[usable["kind"] == "connector"].to_crs(CRS_METRIC)
    for name, line in zip(connectors["name"], connectors.geometry):
        ends = []
        for x, y in (line.coords[0], line.coords[-1]):
            gaps = np.hypot(*(node_xy - (x, y)).T)
            if gaps.min() > snap_m:
                raise ValueError(f"Connector {name!r}: no node within {snap_m} m")
            ends.append(node_ids[gaps.argmin()])
        G.add_edge(
            *ends,
            length=round(line.length, 2),
            osm_id=None,
            name=name,
            kind="connector",
        )

    if elevation is not None:
        for node in G:
            G.nodes[node]["z"] = float(elevation[node])
        for el in raw["elements"]:
            bridge = el.get("tags", {}).get("bridge", "no")
            if el["type"] != "way" or bridge == "no" or el["id"] not in osm_ids:
                continue
            on_graph = [n for n in el["nodes"] if n in G]
            if len(on_graph) < 3:
                continue
            xy = np.array([(G.nodes[n]["x"], G.nodes[n]["y"]) for n in on_graph])
            along = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])
            ends_z = [G.nodes[on_graph[0]]["z"], G.nodes[on_graph[-1]]["z"]]
            flat = np.interp(along[1:-1], along[[0, -1]], ends_z)
            for n, z in zip(on_graph[1:-1], flat):
                G.nodes[n]["z"] = float(z)
    return G


def nearest_node(G: nx.Graph, lat: float, lon: float):
    """The graph node closest to a point (graph nodes carry x, y in CRS_METRIC)."""
    x, y = pyproj.Transformer.from_crs(CRS_WGS84, CRS_METRIC, always_xy=True).transform(
        lon, lat
    )
    nodes = list(G.nodes)
    xy = np.array([(G.nodes[n]["x"], G.nodes[n]["y"]) for n in nodes])
    return nodes[int(np.hypot(*(xy - (x, y)).T).argmin())]
