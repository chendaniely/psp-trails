# Pacific Spirit trails: run the numbered scripts in order.
#
#   make             download the data (if missing), build the map, run checks
#   make view        open the map in a browser
#   make website     build the Quarto site (website/_site/); `make preview` serves it
#   make lint        check formatting and lint
#   make clean       delete generated files (keeps downloads and data/manual/)
#   make clean-data  delete downloads too; the next `make` re-downloads
#
# Every script can also be run cell-by-cell (`# %%`) from the repo root,
# e.g. in Positron or VS Code.
#
# Downloads only re-run when their own script changes, so editing psp.py
# doesn't hit the OSM servers. After changing psp.BUFFER_M, or to get fresh
# OSM data, run `make clean-data && make`.
#
# Our own edits live in data/manual/ and are never deleted by make.

PY := uv run python

.PHONY: all download map check view website preview lint clean clean-data

all: map check

# 01 · Park boundary: Metro Vancouver (official) + OpenStreetMap
data/raw/park_boundary_metrovan.geojson: 01_download_park_boundary.py
	$(PY) $<

# 02 · OSM trails, footways, streets, toilets and water in the park + 400 m
data/raw/osm_highways.gpkg data/raw/osm_amenities.geojson: 02_download_osm.py data/raw/park_boundary_metrovan.geojson
	$(PY) $<

download: data/raw/park_boundary_metrovan.geojson data/raw/osm_highways.gpkg data/raw/osm_amenities.geojson

# 03 · Classify ways and apply our edits in data/manual/
data/processed/ways.gpkg: 03_apply_manual_edits.py psp.py data/raw/osm_highways.gpkg $(wildcard data/manual/*)
	$(PY) $<

# 04 · Interactive MapLibre map of the trails
output/trail_map.html: 04_map_trails.py psp.py data/processed/ways.gpkg data/raw/osm_amenities.geojson
	$(PY) $<

map: output/trail_map.html

# 05 · Do trails connect across Chancellor, University and W 16th, and do
#      the links we rely on (e.g. Vine Maple to Blanca) exist?
output/connection_check.csv: 05_check_connections.py psp.py data/processed/ways.gpkg
	$(PY) $<

check: output/connection_check.csv

view: output/trail_map.html
	$(PY) -m webbrowser "file://$(CURDIR)/$<"

# Website: the Quarto site in website/ has the map as its front page. The
# GitHub Action rebuilds and publishes it to the gh-pages branch on every push
# to main, so these targets are only for looking at it locally.
website/trail_map.html: output/trail_map.html
	cp $< $@

website: website/trail_map.html
	quarto render website

preview: website/trail_map.html
	quarto preview website

lint:
	uv run ruff format --check .
	uv run ruff check .

clean:
	rm -rf output data/processed website/_site website/.quarto website/trail_map.html

clean-data:
	rm -rf data/raw data/cache
