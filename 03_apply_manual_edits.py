# %% [markdown]
# # 03 · Apply our manual edits
#
# OSM has more than we want to route on. We keep our own edits in
# `data/manual/` instead of touching the raw download, so a fresh download
# never wipes them:
#
# - `remove_ways.csv`: OSM way ids to drop (click a line on the map to see its
#   id), with a reason.
# - `remove_areas.geojson`: polygons; a way at least half inside one is dropped.
# - `cut_ways.csv`: drop just part of a way, between two of its OSM nodes (for
#   dead-end stubs on a way we otherwise keep).
# - `add_connectors.geojson`: short links OSM is missing, e.g. where a trail
#   crosses a big street but the two sides were never joined. Each end must
#   be within 5 m of an existing node; step 05 snaps them on.
#
# Removed ways are kept in the output with a `removed` reason, so the map can
# still show what was taken out. Connectors are added with kind "connector".
#
# Inputs:  `data/raw/osm_highways.gpkg` and `.json`,
#          `data/raw/park_boundary_metrovan.geojson`, `data/manual/*`
# Output:  `data/processed/ways.gpkg`: every way with `kind`, `bike`, `removed`,
#          `cut_from`/`cut_to` for cut ways, plus the cut-off pieces (removed)
#          and our connectors

# %%
import json

import geopandas as gpd
import pandas as pd
from shapely import LineString, MultiLineString

import psp

# %% Load and classify (park trail / other path / sidewalk / street / excluded)
park = gpd.read_file(psp.DATA_RAW / "park_boundary_metrovan.geojson")
ways = psp.classify_ways(gpd.read_file(psp.DATA_RAW / "osm_highways.gpkg"), park)
ways["removed"] = None  # a reason string once removed

# %% Remove areas
remove_areas = gpd.read_file(psp.DATA_MANUAL / "remove_areas.geojson")
for _, area in remove_areas.iterrows():
    inside = psp.share_within(
        ways, gpd.GeoSeries([area.geometry], crs=remove_areas.crs)
    )
    hit = (inside >= 0.5) & ways["removed"].isna()
    ways.loc[hit, "removed"] = area["name"]
    print(f"{area['name']}: {hit.sum()} ways")

# %% Remove individual ways
remove_ways = pd.read_csv(psp.DATA_MANUAL / "remove_ways.csv")
missing = set(remove_ways["osm_id"]) - set(ways["osm_id"])
if missing:
    # OSM ids change when someone splits or deletes a way: re-check these.
    print(f"WARNING: not in this download, check on openstreetmap.org: {missing}")
for _, row in remove_ways.iterrows():
    ways.loc[ways["osm_id"] == row["osm_id"], "removed"] = (
        f"{row['name']}: {row['reason']}"
    )

# %% Cut parts of ways. The raw download has each way's node ids, in order.
raw = json.loads((psp.DATA_RAW / "osm_highways.json").read_text())
osm_way = {el["id"]: el for el in raw["elements"] if el["type"] == "way"}
cut_ways = pd.read_csv(psp.DATA_MANUAL / "cut_ways.csv")
ways["cut_from"] = ways["cut_to"] = None
cut_off = []
for _, cut in cut_ways.iterrows():
    el = osm_way.get(cut["osm_id"])
    if el is None or not {cut["from_node"], cut["to_node"]} <= set(el["nodes"]):
        print(f"WARNING: can't cut way {cut['osm_id']}; OSM changed, re-check it")
        continue
    i, j = sorted(
        [el["nodes"].index(cut["from_node"]), el["nodes"].index(cut["to_node"])]
    )
    xy = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
    kept = [LineString(part) for part in (xy[: i + 1], xy[j:]) if len(part) > 1]
    row = ways.index[ways["osm_id"] == cut["osm_id"]][0]
    cut_off.append(
        ways.loc[[row]].assign(
            geometry=[LineString(xy[i : j + 1])],
            removed=f"{cut['name']}: {cut['reason']}",
        )
    )
    ways.loc[row, "geometry"] = kept[0] if len(kept) == 1 else MultiLineString(kept)
    ways.loc[row, ["cut_from", "cut_to"]] = [cut["from_node"], cut["to_node"]]
cut_off = pd.concat(cut_off, ignore_index=True)
for df in (ways, cut_off):  # lengths changed
    df["length_m"] = df.to_crs(psp.CRS_METRIC).length.round(1)
ways = pd.concat([ways, cut_off], ignore_index=True)
cut_off[["osm_id", "name", "length_m"]]

# %% Add connectors
connectors = gpd.read_file(psp.DATA_MANUAL / "add_connectors.geojson")
connectors = connectors.assign(
    kind="connector",
    length_m=connectors.to_crs(psp.CRS_METRIC).length.round(1),
)[["name", "reason", "kind", "length_m", "geometry"]]
ways = pd.concat([ways, connectors], ignore_index=True)
connectors[["name", "length_m"]]

# %% What did we take out?
removed = ways[ways["removed"].notna()]
(removed.groupby(["removed", "kind"])["length_m"].sum() / 1000).round(2)

# %% Park trails that are gone, by name (km)
gone = removed[removed["kind"] == "park trail"]
(gone.groupby(gone["name"].fillna("(unnamed)"))["length_m"].sum() / 1000).round(2)

# %% Save
psp.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
out = psp.DATA_PROCESSED / "ways.gpkg"
ways.to_file(out, layer="ways")
kept = ways["removed"].isna()
print(
    f"Saved {len(ways):,} ways: {kept.sum():,} kept, {(~kept).sum():,} removed, "
    f"{len(connectors)} connectors added"
)
