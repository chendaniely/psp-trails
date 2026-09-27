# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Trail network, route generator and website for the Pacific Spirit Trail
Runners, a trail-running group in Pacific Spirit Regional Park (Vancouver,
BC); that's also the site's title. Runs start and finish at the
**Park Centre** (OSM node 317125595, Cleveland Trail at W 16th Ave). The site
(Quarto, GitHub Pages) has a home page, the trail map and a Routes page that
picks a "route of the day" with elevation profile, written directions, GPX,
share link and printable cards.

Repo: <https://github.com/pstrunners/pstrunners.github.io> (the pstrunners
organization's Pages repo, so the site is served at the root:
<https://pstrunners.github.io/>). The project itself is still called psp-trails.

## Commands

```bash
uv sync                       # install (Python 3.13; not a package: [tool.uv] package = false)
make                          # run the pipeline: map, connection checks, routes
make view                     # open output/trail_map.html
make preview                  # Quarto preview of website/ (copies map + routes in first)
make website                  # render website/_site/
make test                     # uv run pytest -q
make lint                     # ruff format --check + ruff check
uv run pytest tests/test_routing.py::test_t_junction_says_at_the_t -q   # one test
uv run python 07_generate_routes.py                                     # one step
make clean-data && make       # fresh downloads from OSM / NRCan / Metro Vancouver
```

Scripts are meant to be run from the repo root, either via `make` or
cell-by-cell (`# %%` markers) in Positron/VS Code. Make only re-runs a
download step when that script itself changes (not when `psp.py` changes), so
editing helpers never hits the public servers.

## Pipeline (numbered scripts at the repo root)

| Step | Script | Writes |
|---|---|---|
| 01 | download park boundaries (Metro Vancouver official + OSM) | `data/raw/park_boundary_*.geojson` |
| 02 | download OSM `highway=*` ways in park + 400 m, plus toilets/water | `data/raw/osm_highways.json` (raw Overpass, keeps node ids), `.gpkg`, `osm_amenities.geojson`, `study_area.geojson` |
| 03 | NRCan 2 m lidar DTM sampled at every OSM node | `data/raw/node_elevation.csv` |
| 04 | classify ways, apply `data/manual/` edits | `data/processed/ways.gpkg` |
| 05 | MapLibre map (via the `maplibre` Python package), plus trail/street names for the Routes map | `output/trail_map.html`, `output/labels.geojson` |
| 06 | connection checks across Chancellor, University, W 16th + required links | `output/connection_check.csv` |
| 07 | route library from the Park Centre, plus a coverage report | `data/processed/routes.geojson`, `coverage.json` |
| 08 | the ultra: one route over every park trail (route inspection) | `data/processed/ultra.json` |

`data/raw/` is **committed**: it's the snapshot the manual edits' OSM ids refer
to, and CI builds from it. CI never downloads. `data/processed/`, `output/`
and the copies in `website/` are generated and gitignored.

Helpers live in two modules only: `psp.py` (paths, constants, Overpass,
way classification, the routing graph) and `routing.py` (route generation,
elevation, cue sheets, directions). New pipeline work = a new numbered
script plus a Makefile target, not a package.

## Architecture: how the pieces connect

**Way classification** (`psp.classify_ways`): each OSM way gets `kind`:
park trail (trail-type highway ≥50% inside the *official* boundary), other
path, sidewalk, street, or excluded (private, construction, driveways,
parking aisles, permit-only); and `bike` for park trails (shared / hiking
only / untagged, from `bicycle=*`).

**Manual edits** (`data/manual/`, applied by 04; never edit `data/raw/`):
- `remove_areas.geojson`: whole ways ≥50% inside are removed. Currently the
  water side of NW/SW Marine Drive (Marine Drive is the park's edge for us).
- `park_trails.csv`: ways to count as park trail although they're just outside
  the official boundary (e.g. Camosun Trails up to W 16th Ave).
- `remove_ways.csv`: OSM way ids with a reason.
- `cut_ways.csv`: remove part of a way between two of its OSM node ids.
- `add_connectors.geojson`: links OSM lacks (e.g. across W 16th Ave); each end
  must be within 5 m of a node. Its `name` is what runners read in directions
  ("Cross W 16th Ave"); the detail goes in `reason`.
Removed/cut pieces stay in `ways.gpkg` with a `removed` reason so the map can
show them. A re-download can change OSM ids: 04 prints warnings for ids it
can't find. Missing *public* trails are better fixed in OpenStreetMap itself.

**Routing graph** (`psp.walk_graph(raw, ways, elevation)`): built from the raw
Overpass JSON so ways that share an OSM node are joined; honours removed,
excluded and cut ways, snaps connectors, and sets node `z` (bridge interiors
are interpolated between the bridge's ends, since the DTM gives the creek bed).
`routing.contract` merges degree-2 chains into a junction-to-junction
MultiGraph whose edges carry `nodes` (original ids), `pieces` ([name, kind, m])
and `cost`. A route is a list of steps `(u, v, key)`.

**Route generation** (`routing.py`, driven by `07_generate_routes.py`):
- Cost per metre: park trail/connector 1, other path 1.5, sidewalk 2.5,
  street 3; ground already used in a loop costs `REPEAT_PENALTY` (8×) more.
- Loops are start → A → B → start via random trail junctions (fixed seeds,
  so the library is deterministic); out-and-backs go to a junction at half
  the distance.
- Keep: ≥80% trail, ≤10% street, loops ≤15% repeated. Then `pick_distinct`:
  best first, keep a route only if its edge set shares ≤75% (Jaccard) with
  every kept route. Knobs are constants at the top of 07 (268 routes today,
  140 in 7–10 km).
- Coverage pass (`routing.cover_gaps`): after de-duplication, loops aimed at
  trail stretches no 7–10 km route runs yet are added (start → one end →
  along it → home, direct or via a random junction), so the 7–10 km set
  covers all park trail but the foot of Spanish Trail at NW Marine Dr (92 m;
  Dan: runs don't do out-and-backs, so that's fine). `coverage.json` lists what's left; the About
  page shows it.
- `id` (L01…, OB01…) is the position in the library, shortest first, and
  changes when the library does. `key` = sha1 of the route's node sequence,
  used for share links (`routes.html?route=<key>`).
- Elevation: profile every 10 m, 50 m rolling median; `climb` counts a change
  once it passes 1 m (conservative vs. a naive sum of rises).
- `routing.directions` returns `[km, full, short]`. Turn logic compares
  headings ~15 m either side of each junction: T-junction, fork (keep
  left/right), slight/turn/sharp, "continue onto" only for named changes,
  street crossings (short non-trail lead of a stretch), toilets/water as
  landmarks, instructions <30 m apart merged. `short` uses arrows for cards
  (`T← Salish`, `Y↗ Council`). Tests in `tests/test_routing.py` build small
  synthetic graphs for each case; add one when changing this logic.

**Ultra** (`08_plan_ultra.py`, `routing.postman_route`): required = junction
stretches that are ≥50% park trail. Join the pieces with an MST of cheapest
paths, pair odd junctions with `nx.min_weight_matching` over cheapest-path
costs, then an Euler circuit from the Park Centre. The ultra has its own
costs (`cost="ultra_cost"`): a trail run a second time 2.5/m, road 1.5/m,
Imperial Dr and W 29th Ave 1/m. Dan: "rather reduce out and backs and do a
bit more road for the ultra". `routing._euler_circuit` avoids turning back
the way it came when another way on is free, and `_untangle` reverses a
loop to remove a U-turn at a junction passed again; U-turns left are forced
(dead ends). `routing.offshoots` peels dead ends to find short out-and-back
branches (≤400 m), marked optional; `directions(..., stop_at_turnaround=False,
optional=...)` keeps going past dead ends and flags them. Aid stations are
`AID_STATIONS` in 08: the Park Centre and "King Edward" (Imperial Trail
trailhead at W King Edward Ave & W 29th Ave: street parking, toilets; Dan's
pick, it splits the long middle). A pass = within 250 m; each gets an "Aid
station: …" line in the directions and a dashed line on the profile. Toilets and water within 25 m of each other are one
site (toilets / water / both); on the way = every pass within 40 m, or the
closest pass if 40–100 m off (labelled as a detour). ~62 km, 3 s to compute. A stray bit of "park trail"
far from the rest can cost the ultra kilometres of detour: check the route and
remove such stubs in `data/manual/` (as with the SW/NW Marine Dr stub).

**Website** (`website/`, Quarto): `index.qmd` is the landing page (team
photo, intro, Facebook group and Strava club buttons, links into the site);
`map.qmd` iframes `trail_map.html`; `routes.qmd` is Observable JS reading
`routes.geojson`:
- Team photo: goes in `website/images/team-photo.jpg`. Until it exists the
  home page shows a CSS placeholder (`.hero-placeholder` in `styles.css`);
  swapping it in is the one-line change described in a comment in
  `index.qmd`. The photo scales to fit (no cropping), up to 75vh tall.
- Route of the day: routes matching the filters are shuffled with
  mulberry32 seeded by FNV-1a of today's date in America/Vancouver
  (Fisher–Yates); order[0] is the route of the day, "Another route" steps
  through the order. Documented on the About page; keep them in sync.
- Route map labels: `labels.geojson` (named trails/streets, `key` =
  `routing.trail_key` so "Salish" = "Salish Trail") draws faint trail lines
  and italic/grey labels; the route's own stretches (≥300 m, from the cue
  sheet) are named in bold at their midpoints and their faint labels hidden.
- Toilets / water: `psp.amenity_sites` (public only, one site where within
  25 m: toilets / water / both) → 05 writes `amenities.geojson` (always on
  the Routes map); `routing.site_passes` gives each route's (07) and the
  ultra's (08) `passes_by` [km, kind, m off] for the profile (07 skips the
  first/last 250 m: every route starts at the Park Centre's). Both pages
  draw them with the shared `website/amenities.js` (loaded with `import()`
  from `document.baseURI`; it's in `_quarto.yml` resources).
- "Image" draws a 1080×1350 PNG on a canvas (map snapshot via
  `map.once("render")` + `getCanvas()`, name, stats, elevation line, trail
  sequence, credits). "Card image" rasterises the fitted card preview:
  each word drawn at its `Range` rect, rules from borders, the outline SVG
  re-serialised at full size with the page font. Not SVG `foreignObject`:
  Safari taints the canvas with it. Both go to the share sheet on phones,
  else download (`saveImage`).
- `ultra.qmd` reads `ultra.json`; `about.qmd` reads `coverage.json`. The
  Ultra page credits an existing Pacific Spirit Park ultra route: add its
  link/GPX there when Dan provides it.
- Share uses the Web Share API only on touch devices (`pointer: coarse`),
  with the GPX file where `navigator.canShare` allows; otherwise, or if the
  share is refused, it copies the text ("Copy link" on desktop). Desktop
  Chrome on a Mac has `navigator.share` but rejects it ("Permission denied").
- Ultra map icons (toilets / water / both) are canvases added with
  `map.addImage` as `amenity-*`: the OpenFreeMap sprite already has
  "toilets" and "water".
- Printable cards: `LAYOUTS` (2–12 per letter page; 8 = palm size). Text
  auto-shrinks until the directions fit (`fitCards`); "Print cards" opens a
  standalone page in a new window and calls `window.print()`.
- MapLibre GL JS is pinned to 5.x from jsdelivr (6.x ships no UMD build).
  Quarto quirks handled in `styles.css`: `#quarto-content > *` padding (needs
  an id-level selector) and `.btn-quarto` buttons with invisible text.

**CI** (`.github/workflows/publish-website.yml`, on push to main): `uv sync
--locked` → pytest → 04, 05 → 06 (fails the build on any DETOUR / NOT
CONNECTED) → 07 → `quarto publish gh-pages` (path `website`).
`astral-sh/setup-uv` has no moving major tag, so it's pinned to a full
version.

## Project rules and preferences

- Trails first: streets and sidewalks only to link trails and avoid
  backtracking. Typical run: 7–10 km loop (the page default); out-and-backs
  OK; point-to-point rare. Bikes: not yet.
- Public Overpass servers often return 504: `psp.overpass()` retries and falls
  back to mirrors. Overpass `poly:` ignores polygon holes, so 02 re-filters
  ways against the real study area.
- Write GeoPackage "no value" as `None`, not `pd.NA` (pyogrio writes the
  string `"<NA>"`).
- Data licences: OSM (ODbL, attribution on map and site), Metro Vancouver and
  NRCan (Open Government Licences). Code is MIT.
- The repo is public. Personal data (e.g. GPX starting at someone's home)
  goes in `data/private/`, which is gitignored.
- When verifying map/UI changes, look at the rendered page (the site and the
  map are self-contained HTML), not just the build output.
