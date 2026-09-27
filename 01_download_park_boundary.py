# %% [markdown]
# # 01 · Download the Pacific Spirit Regional Park boundary
#
# Two versions of the boundary, so we can compare them:
#
# - **Metro Vancouver** (official, legal boundary) from the Regional Parks
#   Boundaries open-data layer, park code `PAC`.
#   Licence: Open Government Licence – Metro Vancouver.
# - **OpenStreetMap** relation 11683817. © OpenStreetMap contributors, ODbL.
#
# The official boundary is what later steps use to decide whether a path is
# a "park trail".
#
# Outputs:
# - `data/raw/park_boundary_metrovan.geojson`
# - `data/raw/park_boundary_osm.geojson`

# %%
from urllib.parse import urlencode

import geopandas as gpd
import osmnx as ox

import psp

psp.setup_osmnx()
psp.DATA_RAW.mkdir(parents=True, exist_ok=True)

# %% Official boundary: query the Metro Vancouver ArcGIS layer as GeoJSON
query = urlencode(
    {
        "where": f"parkcode='{psp.PARK_METROVAN_CODE}'",
        "outFields": "*",
        "outSR": 4326,
        "f": "geojson",
    }
)
park_metrovan = gpd.read_file(f"{psp.METROVAN_PARKS_LAYER}/query?{query}")
park_metrovan

# %% OpenStreetMap boundary: look the relation up by id (via Nominatim)
park_osm = ox.geocode_to_gdf(f"R{psp.PARK_OSM_RELATION}", by_osmid=True)
park_osm

# %% How similar are they? Compare areas in hectares.
mv = park_metrovan.to_crs(psp.CRS_METRIC).union_all()
osm = park_osm.to_crs(psp.CRS_METRIC).union_all()
print(f"Metro Vancouver: {mv.area / 1e4:7.1f} ha")
print(f"OpenStreetMap:   {osm.area / 1e4:7.1f} ha")
print(f"Overlap:         {mv.intersection(osm).area / 1e4:7.1f} ha")
print(f"Only in one:     {mv.symmetric_difference(osm).area / 1e4:7.1f} ha")

# %% Save
park_metrovan.to_file(psp.DATA_RAW / "park_boundary_metrovan.geojson")
park_osm.to_file(psp.DATA_RAW / "park_boundary_osm.geojson")
