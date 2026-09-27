# psp-trails

Trail data for [Pacific Spirit Regional Park](https://metrovancouver.org/services/regional-parks/park/pacific-spirit-regional-park)
(Vancouver, BC) and the streets around it, for a running group that wants to:

- turn written trail directions into a GPX route, and a GPX route into
  turn-by-turn directions;
- generate random running / walking / biking routes of a given distance, as a
  loop or point to point;
- eventually run every trail in the park with as little backtracking as possible.

All of that needs a complete, gap-free trail network, so the first step is
getting the data and looking at it.

## Quick start

Needs [uv](https://docs.astral.sh/uv/) and `make`.

```bash
uv sync      # install dependencies into .venv
make         # download data, apply our edits, build the map, run checks
make view    # open output/trail_map.html
make preview # the website, with the map as its front page
```

Each numbered script can also be run cell-by-cell (`# %%` markers) from the
repo root in Positron or VS Code; shared settings live in `psp.py`.

## Pipeline

| Step | Script | Writes |
|---|---|---|
| 01 | `01_download_park_boundary.py`: official and OSM park boundaries | `data/raw/park_boundary_metrovan.geojson`, `data/raw/park_boundary_osm.geojson` |
| 02 | `02_download_osm.py`: every `highway=*` way in the park + 400 m, plus public toilets and drinking water | `data/raw/study_area.geojson`, `data/raw/osm_highways.json` (raw Overpass response, keeps node ids), `data/raw/osm_highways.gpkg`, `data/raw/osm_amenities.geojson` |
| 03 | `03_apply_manual_edits.py`: classify ways, apply our edits in `data/manual/` | `data/processed/ways.gpkg` |
| 04 | `04_map_trails.py`: interactive MapLibre map | `output/trail_map.html` |
| 05 | `05_check_connections.py`: do trails connect across Chancellor, University and W 16th, and do the links we rely on (e.g. Vine Maple → Blanca → 16th Ave cycleway) exist? | `output/connection_check.csv` |

On the map, park trails are coloured by bike access (shared / hiking only /
untagged), the same split the official park map uses. Hover a line for its
name; click it for its OSM tags, its way id and a link to openstreetmap.org.
Our added links are drawn dotted black; what we removed is a red layer you
can switch on at the top left. Toilets (yellow, "WC") and drinking water
(magenta, "W") come from OSM: handy for long runs and aid stations, but check
hours and seasonal closures before race day.

## Website

`website/` is a [Quarto](https://quarto.org) site whose front page is the
map. On every push to `main`, the GitHub Action in
`.github/workflows/publish-website.yml` rebuilds the map from the committed
OSM snapshot in `data/raw/` (steps 03–05, no downloads), stops if a
connection check fails, and publishes the site to the `gh-pages` branch:
<https://chendaniely.github.io/psp-trails/>.

To update the site with fresh OSM data: `make clean-data && make`, check the
map and the connection checks, then commit `data/raw/` and push.

## Privacy

Everything committed here is public map data (no OSM contributor names or
ids are downloaded). Keep anything personal, like GPX of runs that start from
someone's home, in `data/private/`, which git ignores.

## How we route

Trails come first. Streets and sidewalks are there to link trails up so a
route doesn't have to double back, and should be used sparingly. The routing
steps still to come will weight the network that way.

## Our edits (`data/manual/`)

OSM has more than we run, and misses a few links we use. Rather than editing
the download, we keep our changes here; step 03 applies them after every
download, and `make` never deletes them.

| File | What it does | Currently |
|---|---|---|
| `remove_areas.geojson` | Drop every way at least half inside a polygon | Water side of NW/SW Marine Drive: Foreshore Trail, Wreck Beach Trails 3/4/6/7, Acadia, Grand Fir, Spanish Banks, Old Marine Drive, the Asian Garden |
| `remove_ways.csv` | Drop single OSM ways by id (click a line on the map to get it) | Salish past Admiralty to NW Marine Dr; small untagged bits by NW Marine Dr and Salish; the path in University Hill Elementary; dead ends by Pioneer/East Canyon; the parking-lot spurs at Cleveland north of 16th; paths along W 4th Ave and at Drummond Dr |
| `cut_ways.csv` | Drop just part of a way, between two of its OSM nodes | Salish stub past the Admiralty junction; the east end of the shared path along Chancellor, past Spanish Trail |
| `add_connectors.geojson` | Short links OSM is missing; each end must be within 5 m of a node | Cleveland Trail across W 16th; Sword Fern to Douglas Fir across W 16th |

Edit these by hand or at [geojson.io](https://geojson.io). If OSM later
splits or deletes a way we list by id, step 03 prints a warning. A missing
*public* trail is better fixed in OpenStreetMap itself, so every app gets it.

## Data sources

| Data | Source | Licence |
|---|---|---|
| Trails, footways, streets | [OpenStreetMap](https://www.openstreetmap.org/relation/11683817) via the Overpass API | © OpenStreetMap contributors, [ODbL](https://opendatacommons.org/licenses/odbl/) |
| Official park boundary | [Metro Vancouver Regional Parks Boundaries](https://open-data-portal-metrovancouver.hub.arcgis.com/datasets/regional-parks-boundaries) (park code `PAC`) | Open Government Licence – Metro Vancouver |
| Basemap | [OpenFreeMap](https://openfreemap.org/) | © OpenMapTiles, data © OpenStreetMap contributors |

Metro Vancouver publishes park boundaries but no trail layer, so trails come
from OpenStreetMap. The official
[park map (PDF)](https://metrovancouver.org/services/regional-parks/Documents/pacific-spirit-regional-park-map.pdf)
names 37 trails, and all 37 are in OSM.

The public Overpass server often answers `504`. `psp.overpass()` retries
each server once and then falls back to mirrors, so a download can take a few
minutes.

## What the data looks like (downloaded 2026-09-26)

- 4,163 OSM ways in the study area.
- 58.0 km of park trails: 37.8 km shared (Metro Vancouver says 34 km are
  multi-use), 14.7 km hiking only, 5.4 km with no bike tag.
- After our edits: 50.1 km (37.3 shared, 11.2 hiking only, 1.6 untagged).
- In OSM, two W 16th Ave crossings weren't joined (routes detoured 200–480 m);
  our connectors fix both. Step 05 checks 8 crossings and 4 links.
- 26 toilets (7 run by Metro Vancouver) and 4 drinking fountains in OSM.
- The official and OSM park boundaries differ by about 187 ha (861 ha vs 841 ha).
- Most park trails carry Metro Vancouver's trail number in `ref` (1–33). A few
  are inconsistent: Clinton is tagged both 3 and 21, Sword Fern 21 and 24,
  Iron Knee `32;9`.

## Next steps

1. Check the whole network for gaps, not just the big-street crossings:
   disconnected pieces, dead ends that nearly touch another path, and paths
   that cross without a shared node. (`psp.walk_graph()` already builds the
   routable graph.)
2. GPX ↔ turn-by-turn directions.
3. Random route generation by distance, loop or point to point.
4. A route covering every trail with minimal backtracking (Chinese postman).

## Licence

The code is [MIT](LICENSE). The data is not ours to relicense: everything
derived from OpenStreetMap (`data/raw/osm_*`, our edits in `data/manual/`, and
the map) is © OpenStreetMap contributors under the
[ODbL](https://opendatacommons.org/licenses/odbl/), and the park boundary is
© Metro Vancouver under the Open Government Licence – Metro Vancouver.
