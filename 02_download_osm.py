# %% [markdown]
# # 02 · Download trails, streets and amenities from OpenStreetMap
#
# Inside the official park boundary plus a `psp.BUFFER_M` buffer:
#
# - every OSM way tagged `highway=*` (trails, footways, sidewalks, streets,
#   ...). The buffer brings in the surrounding streets so later routing can hop
#   between trails via streets instead of doubling back.
# - public toilets and drinking water, for planning long runs and aid stations.
#
# All OSM tags are kept; filtering and classifying happens later.
# Data © OpenStreetMap contributors, ODbL.
#
# Inputs:  `data/raw/park_boundary_metrovan.geojson` (from 01)
# Outputs:
# - `data/raw/study_area.geojson`: the buffered park polygon that was queried
# - `data/raw/osm_highways.json`: the raw Overpass response. Keeps each way's
#   OSM node ids, which tell us where ways join (needed for routing).
# - `data/raw/osm_highways.gpkg`: one row per way, tags as columns
# - `data/raw/osm_amenities.geojson`: toilets and drinking water, one point each

# %%
import json

import geopandas as gpd

import psp

psp.DATA_RAW.mkdir(parents=True, exist_ok=True)

# %% Study area: official park boundary + buffer, simplified to keep the
# Overpass query short
park = gpd.read_file(psp.DATA_RAW / "park_boundary_metrovan.geojson")
study_area = (
    park.to_crs(psp.CRS_METRIC).buffer(psp.BUFFER_M).simplify(10).to_crs(psp.CRS_WGS84)
)
polygon = study_area.union_all()

# %% Overpass query: every highway=* way with at least one node in the study
# area, returned whole (tags, node ids and coordinates)
query = f"""
[out:json][timeout:240];
way["highway"](poly:"{psp.overpass_poly(polygon)}");
out geom;
"""

# %% Download (can take a few minutes when the servers are busy)
data = psp.overpass(query)
data["osm3s"]["timestamp_osm_base"]  # when this OSM snapshot was taken

# %% One row per way. Overpass's `poly:` filter only takes the outer ring, so
# drop ways that fall in the study area's holes (UBC campus is more than
# BUFFER_M from the park).
ways = psp.overpass_ways_to_gdf(data)
ways = ways[ways.intersects(polygon)].reset_index(drop=True)
ways["highway"].value_counts()

# %% Save
gpd.GeoDataFrame(geometry=study_area).to_file(psp.DATA_RAW / "study_area.geojson")
(psp.DATA_RAW / "osm_highways.json").write_text(json.dumps(data))
ways.to_file(psp.DATA_RAW / "osm_highways.gpkg", layer="highways")
print(f"Saved {len(ways):,} ways with {ways.shape[1] - 2} OSM tag columns")

# %% Toilets and drinking water (buildings come back as their centre point)
amenity_query = f"""
[out:json][timeout:60];
nwr["amenity"~"^(toilets|drinking_water)$"](poly:"{psp.overpass_poly(polygon)}");
out center tags;
"""
amenities = psp.overpass_points_to_gdf(psp.overpass(amenity_query))
amenities = amenities[amenities.intersects(polygon)].reset_index(drop=True)
amenities["amenity"].value_counts()

# %% Save
amenities.to_file(psp.DATA_RAW / "osm_amenities.geojson")
print(f"Saved {len(amenities)} toilets and drinking water points")
