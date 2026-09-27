// Toilets and drinking water, drawn the same on every map and elevation
// profile, and like the trail map: a yellow "WC" dot, a magenta "W" dot, and
// the two side by side where there's both.

export const AMENITY = {toilets: "Toilets", water: "Water", both: "Toilets and water"};
const DOT = {toilets: ["#eda100", "WC"], water: ["#e87ba4", "W"]};
const R = 2, D = 22; // pixel ratio; one dot, in CSS px

function dot(ctx, cx, [color, letters]) {
  ctx.beginPath();
  ctx.arc(cx, D / 2, D / 2 - 1.5, 0, 2 * Math.PI);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#fcfcfb";
  ctx.stroke();
  ctx.fillStyle = "#0b0b0b";
  ctx.font = "700 9px system-ui, -apple-system, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(letters, cx, D / 2 + 0.5);
}

function icon(...dots) { // dots side by side
  const width = D + (dots.length - 1) * 16;
  const canvas = Object.assign(document.createElement("canvas"), {width: width * R, height: D * R});
  canvas.style.width = `${width}px`;
  const ctx = canvas.getContext("2d");
  ctx.scale(R, R);
  dots.forEach((d, i) => dot(ctx, D / 2 + i * 16, d));
  return canvas;
}

// The icons as canvases, for maps and legends: {toilets, water, both}.
export function icons() {
  return {toilets: icon(DOT.toilets), water: icon(DOT.water), both: icon(DOT.toilets, DOT.water)};
}

// Show toilets and water on a MapLibre map: `data` is GeoJSON points with a
// `kind` (toilets / water / both) and, optionally, a `label` for the popup.
export function addToMap(map, maplibregl, data, {id = "amenities", before} = {}) {
  for (const [kind, canvas] of Object.entries(icons())) { // the style has its own "toilets"
    if (!map.hasImage(`amenity-${kind}`)) {
      const {width, height} = canvas;
      map.addImage(`amenity-${kind}`, canvas.getContext("2d").getImageData(0, 0, width, height), {pixelRatio: R});
    }
  }
  map.addSource(id, {type: "geojson", data});
  map.addLayer({id, type: "symbol", source: id,
                filter: ["in", ["get", "kind"], ["literal", Object.keys(AMENITY)]],
                layout: {"icon-image": ["concat", "amenity-", ["get", "kind"]], "icon-allow-overlap": true}},
               before);
  map.on("click", id, (e) => {
    const {kind, label} = e.features[0].properties;
    new maplibregl.Popup()
      .setLngLat(e.features[0].geometry.coordinates)
      .setText(label ? `${AMENITY[kind]}: ${label}` : AMENITY[kind])
      .addTo(map);
  });
  map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
}

// Observable Plot marks for toilets and water passed on an elevation profile:
// `passes` is [{km, kind, m}], drawn just above the line (the W on top of the
// WC where there's both).
export function profileMarks(Plot, passes) {
  const dots = (data, [fill, letters], dy) => [
    Plot.dot(data, {x: "km", y: "m", dy, r: 7, fill, stroke: "#fcfcfb", strokeWidth: 1.5}),
    Plot.text(data, {x: "km", y: "m", dy: dy + 0.5, text: () => letters,
                     fontSize: 8, fontWeight: 700, fill: "#0b0b0b"})
  ];
  return [
    ...dots(passes.filter((d) => d.kind !== "water"), DOT.toilets, -11),
    ...dots(passes.filter((d) => d.kind === "water"), DOT.water, -11),
    ...dots(passes.filter((d) => d.kind === "both"), DOT.water, -27)
  ];
}

// What's passed within 150 m of `km`, for a tooltip: ["toilets", "water (72 m off the route)"].
// `passesBy` is [[km, kind, metres off the route], ...] from the pipeline.
export function passedNear(passesBy, km) {
  return passesBy
    .filter(([at]) => Math.abs(at - km) <= 0.15)
    .map(([, kind, off]) => AMENITY[kind].toLowerCase() + (off > 40 ? ` (${off} m off the route)` : ""));
}

// The passes as Plot data, placed on the profile line: `profile` is [{km, m}].
export function onProfile(passesBy, profile) {
  const at = (km) => profile.reduce((a, b) => (Math.abs(b.km - km) < Math.abs(a.km - km) ? b : a));
  return passesBy.map(([km, kind, off]) => ({km, kind, off, m: at(km).m}));
}
