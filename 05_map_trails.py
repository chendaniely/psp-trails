# %% [markdown]
# # 05 · Map the trails
#
# Draws the ways from step 04 (sorted into park trail / other path / sidewalk /
# street / excluded, with our manual removals applied) on an interactive
# MapLibre map. Ways we removed are in their own layer, off by default.
#
# Park trails are coloured by bike access, the same split as the official park
# map's "shared trail" vs "hiking only". Hover a line for its name; click for
# its OSM tags and a link to the way on openstreetmap.org.
#
# Toilets and drinking water from OSM are marked too (WC / W), for planning
# long runs and aid stations; click one for its hours and operator.
#
# Inputs:  `data/raw/*` boundaries and amenities (01, 02),
#          `data/processed/ways.gpkg` (04)
# Output:  `output/trail_map.html` (self-contained; open it in a browser)

# %%
import json
import logging

# The maplibre package warns about its optional Shiny support on import.
logging.getLogger("maplibre").setLevel(logging.ERROR)

import geopandas as gpd
from maplibre import Layer, LayerType, Map, MapOptions
from maplibre.controls import (
    AttributionControl,
    InfoBoxControl,
    LayerSwitcherControl,
    NavigationControl,
    ScaleControl,
)
from maplibre.sources import GeoJSONSource

import psp

# %% Load
park = gpd.read_file(psp.DATA_RAW / "park_boundary_metrovan.geojson")
park_osm = gpd.read_file(psp.DATA_RAW / "park_boundary_osm.geojson")
study_area = gpd.read_file(psp.DATA_RAW / "study_area.geojson")
all_ways = gpd.read_file(psp.DATA_PROCESSED / "ways.gpkg")
removed = all_ways[all_ways["removed"].notna()]
ways = all_ways[all_ways["removed"].isna()]
amenities = gpd.read_file(psp.DATA_RAW / "osm_amenities.geojson")
# Only ones the public can use
amenities = amenities[~amenities.get("access", "").isin(["private", "no", "customers"])]
amenities["amenity"].value_counts()

# %% What's left, by kind
summary = ways.groupby("kind")["length_m"].agg(ways="count", km="sum")
summary["km"] = (summary["km"] / 1000).round(1)
summary

# %% Park trails by bike access
trail_km = (
    ways[ways["kind"] == "park trail"].groupby("bike")["length_m"].sum() / 1000
).round(1)
trail_km

# %% For display only: OSM returns whole ways, so long streets like W 16th Ave
# run far past the study area. Trim them to its edge.
ways = ways.clip(study_area)
removed = removed.clip(study_area)

# %% Colours: trail classes use the first three slots of a colourblind-checked
# categorical palette; everything else is neutral context.
BIKE_COLOURS = {
    "shared": "#2a78d6",  # blue
    "hiking only": "#eb6834",  # orange
    "untagged": "#1baf7a",  # aqua
}
# MapLibre expression: pick each trail's colour from its `bike` value.
TRAIL_COLOUR = [
    "match", ["get", "bike"],
    "shared", BIKE_COLOURS["shared"],
    "hiking only", BIKE_COLOURS["hiking only"],
    BIKE_COLOURS["untagged"],  # fallback
]  # fmt: skip
INK = {"primary": "#0b0b0b", "secondary": "#52514e", "muted": "#898781"}
CONTEXT = {
    "street": "#898781",
    "sidewalk": "#c3c2b7",
    "other path": "#52514e",
    "excluded": "#c3c2b7",
}
REMOVED_RED = "#d03b3b"  # status "critical": always shown with a label
AMENITY_COLOURS = {  # next two categorical slots after the trail classes
    "toilets": "#eda100",  # yellow
    "drinking_water": "#e87ba4",  # magenta
}
PARK_GREEN = "#008300"
OSM_VIOLET = "#4a3aa7"

# OpenFreeMap: free vector basemap, no API key.
BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/positron"


# %% Helpers to turn GeoDataFrames into map layers
MAP_COLUMNS = [
    "osm_id", "name", "ref", "highway", "kind", "bike", "length_m",
    "surface", "bicycle", "horse", "foot", "dog", "access", "removed", "reason",
    "amenity", "osm_type", "operator", "fee", "opening_hours", "seasonal",
]  # fmt: skip


def geojson_source(gdf):
    """GeoJSON source from a GeoDataFrame, dropping missing tags."""
    gdf = gdf[[c for c in MAP_COLUMNS if c in gdf] + ["geometry"]]
    return GeoJSONSource(data=json.loads(gdf.to_crs(psp.CRS_WGS84).to_json(na="drop")))


def line_width(at_z12, at_z17):
    """Line width that grows as you zoom in."""
    return ["interpolate", ["linear"], ["zoom"], 12, at_z12, 17, at_z17]


def ways_of(kind):
    return ways[ways["kind"] == kind]


# %% Build the map
m = Map(
    MapOptions(
        style=BASEMAP_STYLE,
        bounds=tuple(study_area.total_bounds),
        hash=True,  # keep the view in the URL so it can be shared
        attribution_control=False,  # replaced below, with our data credits added
    )
)
m.add_control(
    AttributionControl(
        custom_attribution=(
            "Trails &amp; streets © OpenStreetMap contributors (ODbL) · "
            "Park boundary © Metro Vancouver"
        ),
    ),
    position="bottom-right",
)
m.add_control(NavigationControl(), position="top-right")
m.add_control(ScaleControl(unit="metric"), position="bottom-right")

# Areas, bottom-most first
m.add_layer(
    Layer(
        id="Study area (park + 400 m)",
        type=LayerType.LINE,
        source=geojson_source(study_area),
        paint={"line-color": INK["muted"], "line-width": 1, "line-dasharray": [4, 3]},
    )
)
m.add_layer(
    Layer(
        id="Park boundary (Metro Vancouver)",
        type=LayerType.FILL,
        source=geojson_source(park),
        paint={
            "fill-color": PARK_GREEN,
            "fill-opacity": 0.08,
            "fill-outline-color": PARK_GREEN,
        },
    )
)
m.add_layer(
    Layer(
        id="Park boundary (OpenStreetMap)",
        type=LayerType.LINE,
        source=geojson_source(park_osm),
        paint={"line-color": OSM_VIOLET, "line-width": 1.5, "line-dasharray": [2, 2]},
        layout={"visibility": "none"},
    )
)

# Context ways
m.add_layer(
    Layer(
        id="Excluded (private, driveways, construction)",
        type=LayerType.LINE,
        source=geojson_source(ways_of("excluded")),
        paint={
            "line-color": CONTEXT["excluded"],
            "line-width": line_width(0.5, 1.5),
            "line-dasharray": [1, 2],
        },
        layout={"visibility": "none"},
    )
)
for kind, label, widths in [
    ("street", "Streets", (1, 3)),
    ("sidewalk", "Sidewalks & crossings", (0.5, 1.5)),
    ("other path", "Other paths (outside park)", (0.75, 2)),
]:
    m.add_layer(
        Layer(
            id=label,
            type=LayerType.LINE,
            source=geojson_source(ways_of(kind)),
            paint={"line-color": CONTEXT[kind], "line-width": line_width(*widths)},
        )
    )

# Park trails on top, coloured by bike access
trail_source = geojson_source(ways_of("park trail"))
m.add_layer(
    Layer(
        id="Park trails",
        type=LayerType.LINE,
        source=trail_source,
        paint={
            "line-color": TRAIL_COLOUR,
            "line-width": line_width(2, 5),
        },
        layout={"line-cap": "round", "line-join": "round"},
    )
)
m.add_layer(
    Layer(
        id="Trail names",
        type=LayerType.SYMBOL,
        source=trail_source,
        min_zoom=14,
        layout={
            "symbol-placement": "line",
            "text-field": ["get", "name"],
            "text-font": ["Noto Sans Regular"],
            "text-size": 12,
        },
        paint={
            "text-color": INK["secondary"],
            "text-halo-color": "#fcfcfb",
            "text-halo-width": 1.5,
        },
    )
)

# Links we added in step 04 where OSM is missing a connection
m.add_layer(
    Layer(
        id="Added by our edits",
        type=LayerType.LINE,
        source=geojson_source(ways_of("connector")),
        paint={
            "line-color": INK["primary"],
            "line-width": line_width(2, 5),
            "line-dasharray": [1, 1],
        },
    )
)

# What we removed in step 04, drawn on top so it's visible when switched on
m.add_layer(
    Layer(
        id="Removed by our edits",
        type=LayerType.LINE,
        source=geojson_source(removed),
        paint={
            "line-color": REMOVED_RED,
            "line-width": line_width(1.5, 4),
            "line-dasharray": [2, 1],
        },
        layout={"visibility": "none"},
    )
)

# Toilets and drinking water: a coloured dot with a short label on it
for amenity, label, letters in [
    ("toilets", "Toilets", "WC"),
    ("drinking_water", "Drinking water", "W"),
]:
    source = geojson_source(amenities[amenities["amenity"] == amenity])
    m.add_layer(
        Layer(
            id=label,
            type=LayerType.CIRCLE,
            source=source,
            paint={
                "circle-color": AMENITY_COLOURS[amenity],
                "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 5, 16, 10],
                "circle-stroke-color": "#fcfcfb",
                "circle-stroke-width": 2,
            },
        )
    )
    m.add_layer(
        Layer(
            id=f"{label} labels",
            type=LayerType.SYMBOL,
            source=source,
            min_zoom=14,
            layout={
                "text-field": letters,
                "text-font": ["Noto Sans Bold"],
                "text-size": 9,
                "text-allow-overlap": True,
            },
            paint={"text-color": INK["primary"]},
        )
    )
    m.add_tooltip(label, template=f"<b>{label}</b> {{{{ name }}}}")
    m.add_popup(
        label,
        template=(
            f"<b>{label}</b> {{{{ name }}}}<br>"
            "operator: {{ operator }}<br>"
            "hours: {{ opening_hours }} · fee: {{ fee }} · seasonal: {{ seasonal }}<br>"
            "<a href='https://www.openstreetmap.org/{{ osm_type }}/{{ osm_id }}' "
            "target='_blank'>OSM {{ osm_type }} {{ osm_id }}</a>"
        ),
    )

# %% Hover and click
HOVER = "<b>{{ name }}</b> <span style='color:#52514e'>{{ highway }}</span>"
CLICK = """
<b>{{ name }}</b> <span style='color:#52514e'>{{ ref }}</span><br>
{{ highway }} · {{ length_m }} m<br>
bicycle: {{ bicycle }} · horse: {{ horse }} · dog: {{ dog }}<br>
surface: {{ surface }}<br>
<a href="https://www.openstreetmap.org/way/{{ osm_id }}" target="_blank">OSM way {{ osm_id }}</a>
"""
for layer_id in [
    "Park trails",
    "Other paths (outside park)",
    "Streets",
    "Sidewalks & crossings",
    "Excluded (private, driveways, construction)",
]:
    m.add_tooltip(layer_id, template=HOVER)
    m.add_popup(layer_id, template=CLICK)
m.add_tooltip("Added by our edits", template="<b>{{ name }}</b>")
m.add_popup(
    "Added by our edits",
    template="<b>{{ name }}</b><br>{{ length_m }} m, added by us<br>{{ reason }}",
)
m.add_tooltip("Removed by our edits", template=HOVER)
m.add_popup(
    "Removed by our edits",
    template=CLICK + "<br><span style='color:#d03b3b'>Removed:</span> {{ removed }}",
)


# %% Legend and layer toggles
def dot(colour, label):
    return (
        f"<div><span style='display:inline-block;width:10px;height:10px;"
        f"margin:0 10px 0 4px;border-radius:50%;vertical-align:middle;"
        f"background:{colour}'></span>{label}</div>"
    )


n_toilets = (amenities["amenity"] == "toilets").sum()
n_water = (amenities["amenity"] == "drinking_water").sum()


def swatch(colour, label, dashed=False):
    style = "dashed" if dashed else "solid"
    return (
        f"<div><span style='display:inline-block;width:18px;margin-right:6px;"
        f"vertical-align:middle;border-top:3px {style} {colour}'></span>{label}</div>"
    )


legend = "".join(
    [
        "<div style='font-weight:600;margin-bottom:4px'>Pacific Spirit park trails</div>",
        *[
            swatch(colour, f"{label.capitalize()} · {trail_km.get(label, 0)} km")
            for label, colour in BIKE_COLOURS.items()
        ],
        "<div style='margin-top:6px;color:#52514e'>Context</div>",
        swatch(CONTEXT["street"], "Streets"),
        swatch(CONTEXT["other path"], "Other paths"),
        swatch(CONTEXT["sidewalk"], "Sidewalks & crossings"),
        swatch(PARK_GREEN, "Park boundary (official)"),
        swatch(OSM_VIOLET, "Park boundary (OSM)", dashed=True),
        swatch(INK["primary"], "Added by our edits", dashed=True),
        swatch(REMOVED_RED, "Removed by our edits (off)", dashed=True),
        "<div style='margin-top:6px;color:#52514e'>Amenities (OSM)</div>",
        dot(AMENITY_COLOURS["toilets"], f"Toilets · {n_toilets}"),
        dot(AMENITY_COLOURS["drinking_water"], f"Drinking water · {n_water}"),
    ]
)
m.add_control(
    InfoBoxControl(
        content=legend,
        css_text=(
            "font: 12px/1.5 system-ui, -apple-system, 'Segoe UI', sans-serif;"
            "color:#0b0b0b; background:#fcfcfb; padding:8px 10px; border-radius:4px;"
            "box-shadow: 0 0 0 1px rgba(11,11,11,0.10);"
        ),
    ),
    position="bottom-left",
)
m.add_control(
    LayerSwitcherControl(
        layer_ids=[
            "Toilets",
            "Drinking water",
            "Added by our edits",
            "Removed by our edits",
            "Park trails",
            "Trail names",
            "Other paths (outside park)",
            "Sidewalks & crossings",
            "Streets",
            "Excluded (private, driveways, construction)",
            "Park boundary (Metro Vancouver)",
            "Park boundary (OpenStreetMap)",
            "Study area (park + 400 m)",
        ],
        theme="default",
        css_text="padding:6px 8px; font: 12px/1.6 system-ui, sans-serif;",
    ),
    position="top-left",
)

# %% Save
psp.OUTPUT.mkdir(exist_ok=True)
out = psp.OUTPUT / "trail_map.html"
out.write_text(
    m.to_html(title="Pacific Spirit trails", style="position:absolute; inset:0;")
)
print(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
