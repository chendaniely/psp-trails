# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Trail network, route generator and website for a trail-running group in
Pacific Spirit Regional Park (Vancouver, BC). Runs start and finish at the
**Park Centre** (OSM node 317125595, Cleveland Trail at W 16th Ave). The site
(Quarto, GitHub Pages) shows the trail map and a Routes page that picks a
"route of the day" with elevation profile, written directions, GPX, share
link and printable cards.

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
| 07 | route library from the Park Centre | `data/processed/routes.geojson` |

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
  every kept route. Knobs are constants at the top of 07 (226 routes today,
  ~100 loops in 7–10 km).
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

**Website** (`website/`, Quarto): `index.qmd` iframes `trail_map.html`;
`routes.qmd` is Observable JS reading `routes.geojson`:
- Route of the day: routes matching the filters are shuffled with
  mulberry32 seeded by FNV-1a of today's date in America/Vancouver
  (Fisher–Yates); order[0] is the route of the day, "Another route" steps
  through the order. Documented on the About page; keep them in sync.
- Route map labels: `labels.geojson` (named trails/streets, `key` =
  `routing.trail_key` so "Salish" = "Salish Trail") draws faint trail lines
  and italic/grey labels; the route's own stretches (≥300 m, from the cue
  sheet) are named in bold at their midpoints and their faint labels hidden.
- Share uses the Web Share API (GPX file only where `navigator.canShare`
  allows; Chrome/Android doesn't for .gpx), else copies text to the clipboard.
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
