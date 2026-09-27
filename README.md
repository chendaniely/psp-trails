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
| 03 | `03_download_elevation.py`: ground elevation at every OSM node, from NRCan's 2 m lidar DTM | `data/raw/node_elevation.csv` |
| 04 | `04_apply_manual_edits.py`: classify ways, apply our edits in `data/manual/` | `data/processed/ways.gpkg` |
| 05 | `05_map_trails.py`: interactive MapLibre map | `output/trail_map.html` |
| 06 | `06_check_connections.py`: do trails connect across Chancellor, University and W 16th, and do the links we rely on (e.g. Vine Maple → Blanca → 16th Ave cycleway) exist? | `output/connection_check.csv` |
| 07 | `07_generate_routes.py`: loops and out-and-backs from the Park Centre (see `routing.py`) | `data/processed/routes.geojson`, `data/processed/coverage.json` |
| 08 | `08_plan_ultra.py`: one route over every park trail from the Park Centre (route inspection / "Chinese postman") | `data/processed/ultra.json` |

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
OSM snapshot in `data/raw/` (steps 04–07, no downloads), stops if a
connection check fails, and publishes the site to the `gh-pages` branch:
<https://pstrunners.github.io/>.

To update the site with fresh OSM data: `make clean-data && make`, check the
map and the connection checks, then commit `data/raw/` and push.

## Privacy

Everything committed here is public map data (no OSM contributor names or
ids are downloaded). Keep anything personal, like GPX of runs that start from
someone's home, in `data/private/`, which git ignores.

## How we route

Trails come first. Streets and sidewalks are there to link trails up so a
route doesn't have to double back, and should be used sparingly.

Step 07 (`routing.py`) builds a library of routes from the Park Centre (the
ranger station on Cleveland Trail at W 16th Ave), where the group starts:

- A metre of street costs 3x a metre of trail (sidewalk 2.5x, paths outside
  the park 1.5x), so the cheapest way between two points prefers trails.
- **Loops** go start → A → B → start, each leg avoiding ground already run
  (13,500 random tries, fixed seeds). **Out-and-backs** go to a trail
  junction and back.
- We keep routes that are at least 80% trail, at most 10% street and (loops)
  at most 15% run twice, then drop near-duplicates: best route first, each
  next one kept only if it shares at most 75% of its trail segments with
  every route already kept (Jaccard similarity; `routing.pick_distinct`).
- A **coverage pass** (`routing.cover_gaps`) then adds loops aimed at any
  trail no 7–10 km route runs yet: 140 routes of 7–10 km cover 49.9 of the
  park's 50.0 km of trail (the rest is the foot of Spanish Trail at NW Marine
  Dr, listed on the About page). 268 routes in all. The knobs are at the top of
  `07_generate_routes.py`.

Each route also gets:

- **Elevation**: a profile and total climb/descent from NRCan's lidar ground
  model (bare earth, so the forest canopy doesn't count). Heights are
  smoothed over 50 m and a climb counts once it passes 1 m, so the totals
  are on the conservative side of what a watch might show.
- **Written directions** from the geometry at each junction: "At the T, turn
  left onto Salish", "Keep right onto Council", "Cross W 16th Ave", with
  toilets and water as landmarks; plus a short form for printed cards
  (`T← Salish`, `Y↗ Council`).

The website's **ROTD** (route of the day, `rotd.qmd`) page picks one: loops
by default, 7–10 km on a two-thumb slider, and the same "route of the day"
for everyone. **Routes** (`routes.qmd`) lists every route as a card to filter,
search and sort; a card opens that route on ROTD (`?route=<key>&from=list`),
and an old `routes.html?route=<key>` link redirects there. Route ids
(L01, L02, …; OB01, …) are positions in the library, shortest first, so they
can change when the library is regenerated. The page shuffles the routes
matching your choices with a random generator seeded by today's date in
Vancouver (FNV-1a hash of the date → mulberry32 → Fisher–Yates); the first
is the route of the day, and **Another route** steps through the rest without
repeats. **Share** sends the route (text, directions, and the GPX where the
phone allows) through the phone's share sheet, with a link ending in
`?route=<key>`: `key` fingerprints the route's exact path (07), so the link
keeps opening that route. On a computer the button is **Copy link** and
copies the same text. **Image** (for social media) and **Card image** (the
printable card, to keep on a phone) go to the share sheet on phones and
download elsewhere. See `website/rotd.qmd` and the About page.
It shows the map, elevation profile, directions, a cue sheet, a GPX download
and printable cards (2 to 12 per page; 8 is palm size). Toilets and water
(05 writes `amenities.geojson`) are always on the map, and marked on the
profile where the route passes them.
**Bikes** (`bikes.qmd`) is the same for bikes, from 07 and 08 run with
`MODE=bike` (`make bikes`): every trail but the hiking-only ones
(`psp.bike_ways`), 8–24 km (14–20 by default), loops round 3–7 turn points
so long rides stay in a park 4 km across, no written directions; plus the
bike ultra (every trail open to bikes, ~50 km).
If a route goes somewhere we wouldn't, that's usually a data fix in
`data/manual/`.

## Ultra: every trail in one run

`08_plan_ultra.py` finds one route over every park trail, starting and
finishing at the Park Centre (the aid station: free parking along W 16th Ave,
toilets and water). It's the route inspection ("Chinese postman") problem:
join the trail network into one piece, pick the cheapest stretches to run
twice so every junction is even (minimum-weight matching), then walk it as
one Euler circuit (`routing.postman_route`). Dead-end offshoots up to 400 m
are optional (`routing.offshoots`). A bit of road beats running a trail twice
here (`REPEAT_PER_M`, `ULTRA_PER_M`; Imperial Dr and W 29th Ave count as
trail), and the circuit only turns straight back at dead ends. Today:
61.9 km, ↑700 m, 6.4 km run twice, 8% street. The map marks toilets, water, or both (within 25 m of each other) on
the way: within 40 m of the route, or up to 100 m as a short detour. A
second aid station sits at the Imperial Trail trailhead by W King Edward Ave
and W 29th Ave (street parking, toilets), splitting the long middle. The idea comes from an existing Pacific Spirit Park ultra route,
credited on the Ultra page (link to come).

## Our edits (`data/manual/`)

OSM has more than we run, and misses a few links we use. Rather than editing
the download, we keep our changes here; step 04 applies them after every
download, and `make` never deletes them.

| File | What it does | Currently |
|---|---|---|
| `park_trails.csv` | Count a way as park trail although it lies just outside the official boundary | Camosun Trails' last stretch up to W 16th Ave |
| `remove_areas.geojson` | Drop every way at least half inside a polygon | Water side of NW/SW Marine Drive: Foreshore Trail, Wreck Beach Trails 3/4/6/7, Acadia, Grand Fir, Spanish Banks, Old Marine Drive, the Asian Garden |
| `remove_ways.csv` | Drop single OSM ways by id (click a line on the map to get it) | Salish past Admiralty to NW Marine Dr; small untagged bits by NW Marine Dr and Salish; the path in University Hill Elementary; dead ends by Pioneer/East Canyon; the parking-lot spurs at Cleveland north of 16th; paths along W 4th Ave and at Drummond Dr; the footway off Drummond Dr near Chancellor |
| `cut_ways.csv` | Drop just part of a way, between two of its OSM nodes | Salish stub past the Admiralty junction; the east end of the shared path along Chancellor, past Spanish Trail |
| `add_connectors.geojson` | Short links OSM is missing; each end must be within 5 m of a node | Cleveland Trail across W 16th; Sword Fern to Douglas Fir across W 16th |

Edit these by hand or at [geojson.io](https://geojson.io). If OSM later
splits or deletes a way we list by id, step 04 prints a warning. A missing
*public* trail is better fixed in OpenStreetMap itself, so every app gets it.

## Data sources

| Data | Source | Licence |
|---|---|---|
| Trails, footways, streets | [OpenStreetMap](https://www.openstreetmap.org/relation/11683817) via the Overpass API | © OpenStreetMap contributors, [ODbL](https://opendatacommons.org/licenses/odbl/) |
| Elevation | [NRCan High Resolution DEM](https://open.canada.ca/data/en/dataset/0fe65119-e96e-4a57-8bfe-9d9245fba06b) (2 m bare-earth DTM, from lidar) | Open Government Licence – Canada |
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
  our connectors fix both. Step 06 checks 8 crossings and 4 links.
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
Elevations are from NRCan's High Resolution DEM under the Open Government
Licence – Canada.
