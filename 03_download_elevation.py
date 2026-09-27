# %% [markdown]
# # 03 · Download ground elevation for every trail and street node
#
# Elevation comes from NRCan's High Resolution DEM (2 m, from lidar). We use
# the bare-earth model (DTM): Pacific Spirit is dense forest, and surface
# models would give us the tree tops instead of the trail.
#
# Rather than keep the raster, we read the part covering the study area once
# and look up the height of every OSM node from step 02. That small table is
# all route profiles need.
# Data: NRCan, Open Government Licence - Canada.
#
# Inputs:  `data/raw/osm_highways.json` (02)
# Output:  `data/raw/node_elevation.csv`: OSM node id, elevation in metres

# %%
import json

import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform
from rasterio.windows import from_bounds

import psp

# %% Every node of every way we downloaded
raw = json.loads((psp.DATA_RAW / "osm_highways.json").read_text())
nodes = {
    node: (pt["lon"], pt["lat"])
    for el in raw["elements"]
    if el["type"] == "way"
    for node, pt in zip(el["nodes"], el["geometry"])
}
node_ids = np.array(list(nodes))
lons, lats = np.array(list(nodes.values())).T
print(f"{len(node_ids):,} nodes")

# %% Read the DTM just around those nodes (one ~30 MB request)
with (
    rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"),
    rasterio.open(psp.DTM_URL) as dtm,
):
    xs, ys = transform(psp.CRS_WGS84, dtm.crs, lons, lats)
    pad = 10 * dtm.res[0]
    window = (
        from_bounds(
            min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad, dtm.transform
        )
        .round_offsets()
        .round_lengths()
    )
    heights = dtm.read(1, window=window, masked=True)
    window_transform = dtm.window_transform(window)
heights.shape

# %% Look up each node's pixel
rows, cols = rasterio.transform.rowcol(window_transform, xs, ys)
elevation = heights[np.array(rows), np.array(cols)].filled(np.nan)
table = pd.DataFrame({"node": node_ids, "elevation_m": np.round(elevation, 1)})
table["elevation_m"].describe()

# %% Save
table.to_csv(psp.DATA_RAW / "node_elevation.csv", index=False)
print(
    f"Saved {len(table):,} node elevations; {table['elevation_m'].isna().sum()} missing"
)
