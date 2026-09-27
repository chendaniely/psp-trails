"""Route generation on the walkable network from `psp.walk_graph()`.

Routes are made on a *contracted* graph: chains of plain two-neighbour
nodes are merged, so every node is a junction or a dead end and every edge
is a stretch of trail or street between junctions.

Costs prefer trails: a metre of street costs 3x a metre of park trail, so
routes only use streets where that saves running the same trail twice.
"""

import math
import random
from collections import Counter
from itertools import pairwise

import networkx as nx
import numpy as np

# Cost of one metre, by kind of way. Trails are what we're here for;
# streets and sidewalks are only for linking trails up.
COST_PER_M = {
    "park trail": 1.0,
    "connector": 1.0,  # our links across big streets, between trails
    "other path": 1.5,
    "sidewalk": 2.5,
    "street": 3.0,
}
TRAIL_KINDS = {"park trail", "connector"}
STREET_KINDS = {"street", "sidewalk"}
REPEAT_PENALTY = 8  # later legs of a loop avoid ground already covered


# Contracting the graph -------------------------------------------------------


def contract(G: nx.Graph, keep=()) -> nx.MultiGraph:
    """Merge chains of two-neighbour nodes into single edges.

    Nodes in `keep` (e.g. the start of our runs) always stay, even mid-chain.

    Each edge keeps: `length` and `cost` (m), `nodes` (the original node ids
    in order from its lower-numbered end), and `pieces`: [name, kind, m] runs
    in the same order, for cue sheets.
    """
    keep = {n for n in G if G.degree(n) != 2} | set(keep)
    # A loop made only of two-neighbour nodes has no junction: keep one node.
    for component in nx.connected_components(G):
        if not component & keep:
            keep.add(min(component))

    H = nx.MultiGraph()
    H.add_nodes_from((n, G.nodes[n]) for n in keep)
    seen = set()
    for start in keep:
        for first in G[start]:
            if frozenset((start, first)) in seen:
                continue
            chain = [start, first]
            while chain[-1] not in keep:
                prev, here = chain[-2], chain[-1]
                chain.append(next(n for n in G[here] if n != prev))
            seen.update(frozenset(p) for p in pairwise(chain))
            if chain[0] > chain[-1]:
                chain.reverse()
            pieces = []
            for a, b in pairwise(chain):
                d = G.edges[a, b]
                if pieces and pieces[-1][:2] == [d["name"], d["kind"]]:
                    pieces[-1][2] += d["length"]
                else:
                    pieces.append([d["name"], d["kind"], d["length"]])
            H.add_edge(
                chain[0],
                chain[-1],
                nodes=chain,
                pieces=pieces,
                length=sum(p[2] for p in pieces),
                cost=sum(p[2] * COST_PER_M[p[1]] for p in pieces),
            )
    return H


# Paths -------------------------------------------------------------------------

# A route is a list of steps (u, v, key): edge `key` between u and v, run u -> v.


def edge_id(u, v, key):
    return (min(u, v), max(u, v), key)


def cheapest_path(H, a, b, used=None):
    """Cheapest steps from a to b; edges in `used` cost REPEAT_PENALTY times more."""
    used = used or set()

    def step_cost(u, v, key, data):
        return data["cost"] * (REPEAT_PENALTY if edge_id(u, v, key) in used else 1)

    def weight(u, v, parallel):
        return min(step_cost(u, v, k, d) for k, d in parallel.items())

    nodes = nx.bidirectional_dijkstra(H, a, b, weight=weight)[1]
    return [
        (u, v, min(H[u][v], key=lambda k, u=u, v=v: step_cost(u, v, k, H[u][v][k])))
        for u, v in pairwise(nodes)
    ]


# Describing a route --------------------------------------------------------------


def route_pieces(H, steps):
    """[name, kind, m] runs along the route, in running order."""
    out = []
    for u, v, key in steps:
        d = H.edges[u, v, key]
        pieces = d["pieces"] if d["nodes"][0] == u else d["pieces"][::-1]
        for name, kind, metres in pieces:
            if out and out[-1][:2] == [name, kind]:
                out[-1][2] += metres
            else:
                out.append([name, kind, metres])
    return out


def route_stats(H, steps):
    """Length, and the share of it on trail, on streets and run more than once."""
    pieces = route_pieces(H, steps)
    length = sum(p[2] for p in pieces)
    runs = Counter(edge_id(*s) for s in steps)
    repeated = sum(H.edges[e]["length"] * (n - 1) for e, n in runs.items())
    return {
        "length_m": length,
        "trail_share": sum(p[2] for p in pieces if p[1] in TRAIL_KINDS) / length,
        "street_share": sum(p[2] for p in pieces if p[1] in STREET_KINDS) / length,
        "repeat_share": repeated / length,
    }


def route_nodes(H, steps):
    """Original (uncontracted) node ids along the route, in running order."""
    nodes = []
    for u, v, key in steps:
        chain = H.edges[u, v, key]["nodes"]
        chain = chain if chain[0] == u else chain[::-1]
        nodes.extend(chain if not nodes else chain[1:])
    return nodes


# Making routes ------------------------------------------------------------------


def _waypoints(H, start):
    """Junctions on park trails, with the metres to reach them from `start`."""
    _, paths = nx.single_source_dijkstra(
        H, start, weight=lambda u, v, par: min(d["cost"] for d in par.values())
    )
    reach = {}
    for node, path in paths.items():
        on_trail = any(
            any(p[1] in TRAIL_KINDS for p in d["pieces"])
            for _, _, d in H.edges(node, data=True)
        )
        if node != start and H.degree(node) >= 3 and on_trail:
            steps = [(u, v, min(H[u][v], key=lambda k, u=u, v=v: H[u][v][k]["cost"])) for u, v in pairwise(path)]  # fmt: skip
            reach[node] = sum(H.edges[s]["length"] for s in steps)
    return reach


def _bearing(H, a, b):
    return math.atan2(
        H.nodes[b]["y"] - H.nodes[a]["y"], H.nodes[b]["x"] - H.nodes[a]["x"]
    )


def make_loops(H, start, min_m, max_m, tries=2000, seed=42):
    """Loops start -> A -> B -> start, each leg avoiding ground already run."""
    rng = random.Random(seed)
    reach = _waypoints(H, start)
    routes = []
    for _ in range(tries):
        target = rng.uniform(min_m, max_m)
        leg = target / rng.uniform(2.6, 3.6)  # roughly a triangle
        near = [n for n, m in reach.items() if 0.6 * leg <= m <= 1.4 * leg]
        if len(near) < 2:
            continue
        a = rng.choice(near)
        turn = [
            n for n in near
            if 0.8 <= abs(math.remainder(_bearing(H, start, n) - _bearing(H, start, a), math.tau)) <= 2.4
        ]  # fmt: skip
        if not turn:
            continue
        b = rng.choice(turn)
        steps, used = [], set()
        for x, y in ((start, a), (a, b), (b, start)):
            leg_steps = cheapest_path(H, x, y, used)
            steps += leg_steps
            used |= {edge_id(*s) for s in leg_steps}
        stats = route_stats(H, steps)
        if min_m <= stats["length_m"] <= max_m:
            routes.append({"type": "loop", "steps": steps, **stats})
    return routes


def make_out_and_backs(H, start, min_m, max_m):
    """Out to a trail junction at half the distance, and back the same way."""
    routes = []
    for turnaround, metres in _waypoints(H, start).items():
        if min_m <= 2 * metres <= max_m:
            out = cheapest_path(H, start, turnaround)
            steps = out + [(v, u, k) for u, v, k in reversed(out)]
            routes.append(
                {"type": "out-and-back", "steps": steps, **route_stats(H, steps)}
            )
    return routes


def pick_distinct(routes, max_overlap=0.6):
    """Best routes first, skipping any too similar to one already kept.

    Similarity is the share of edges two routes have in common (Jaccard).
    Best = most trail, least repeated ground, fewest streets.
    """

    def score(r):
        return r["trail_share"] - r["repeat_share"] - 0.5 * r["street_share"]

    kept, kept_edges = [], []
    for r in sorted(routes, key=score, reverse=True):
        edges = {edge_id(*s) for s in r["steps"]}
        if all(len(edges & k) / len(edges | k) < max_overlap for k in kept_edges):
            kept.append(r)
            kept_edges.append(edges)
    return kept


# Cue sheet and name ----------------------------------------------------------------

UNNAMED = {
    "park trail": "unnamed trail",
    "connector": "crossing",
    "other path": "path",
    "sidewalk": "sidewalk",
    "street": "street",
}


def clean_name(name):
    """First part of a two-language name: "Camosun Bog | xʷməm̓qʷe:m Boardwalk"."""
    return name.split(" | ")[0].split(" - ")[0]


def trail_key(name):
    """OSM spells some trails both ways: "Salish" and "Salish Trail"."""
    return clean_name(name).removesuffix(" Trail")


ABBREVIATIONS = {
    "West": "W", "East": "E", "North": "N", "South": "S", "Northwest": "NW",
    "Southwest": "SW", "Avenue": "Ave", "Street": "St", "Boulevard": "Blvd",
    "Drive": "Dr", "Road": "Rd", "Crescent": "Cres", "Place": "Pl",
}  # fmt: skip


def _street(name):
    """Short street names for print: "West 16th Avenue" -> "W 16th Ave"."""
    return " ".join(ABBREVIATIONS.get(word, word) for word in name.split())


def cue_sheet(H, steps, min_m=40):
    """[label, start km, km] for each stretch of the route.

    A short stretch of street is a crossing ("Cross West 16th Avenue"); any
    other stretch under `min_m` folds into the one before it. A "street" named
    "... Trail" is a trail OSM tags as a service road, not a road to cross.
    """
    cues = []
    for name, kind, metres in route_pieces(H, steps):
        crossing = (
            bool(name)
            and "Trail" not in name
            and kind in STREET_KINDS
            and metres < min_m
        )
        label = (
            f"Cross {_street(name)}" if crossing else clean_name(name or UNNAMED[kind])
        )
        if cues and (
            trail_key(cues[-1][0]) == trail_key(label)
            or (metres < min_m and not crossing)
        ):
            cues[-1][1] += metres
        else:
            cues.append([label, metres])
    out, at = [], 0.0
    for label, metres in cues:
        out.append([label, round(at / 1000, 2), round(metres / 1000, 2)])
        at += metres
    return out


def route_name(H, steps, n=3):
    """The route's main trails, in running order, e.g. "Salish, Imperial & Top"."""
    by_trail = Counter()
    for name, kind, metres in route_pieces(H, steps):
        if name and kind in TRAIL_KINDS:
            by_trail[trail_key(name)] += metres  # insertion order = running order
    top = {trail for trail, _ in by_trail.most_common(n)}
    names = [trail for trail in by_trail if trail in top]
    if len(names) > 1:
        return ", ".join(names[:-1]) + " & " + names[-1]
    return names[0] if names else "Unnamed trails"


# Elevation --------------------------------------------------------------------


def elevation_profile(G, nodes, step_m=10, smooth=5):
    """Distance along the route (m) with elevation, x and y every `step_m`.

    Elevations are a rolling median over `smooth` samples (50 m), which drops
    lidar and bridge spikes but keeps real hills.
    """
    xy = np.array([(G.nodes[n]["x"], G.nodes[n]["y"]) for n in nodes])
    z = np.array([G.nodes[n]["z"] for n in nodes])
    along = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])
    at = np.append(np.arange(0, along[-1], step_m), along[-1])
    padded = np.pad(np.interp(at, along, z), smooth // 2, mode="edge")
    z_at = np.median(np.lib.stride_tricks.sliding_window_view(padded, smooth), axis=1)
    return at, z_at, np.interp(at, along, xy[:, 0]), np.interp(at, along, xy[:, 1])


def climb(z, threshold_m=1.0):
    """Total ascent and descent (m), counting a change once it passes `threshold_m`."""
    gain = loss = 0.0
    ref = z[0]
    for height in z[1:]:
        if height - ref >= threshold_m:
            gain, ref = gain + height - ref, height
        elif ref - height >= threshold_m:
            loss, ref = loss + ref - height, height
    return gain, loss


# Directions -----------------------------------------------------------------------

THE_UNNAMED = {
    "park trail": "the unnamed trail",
    "connector": "the crossing",
    "other path": "the path",
    "sidewalk": "the sidewalk",
    "street": "the street",
}
COMPASS = ["east", "northeast", "north", "northwest", "west", "southwest", "south", "southeast"]  # fmt: skip


def _oriented(H, step):
    """The edge's original nodes and [name, kind, m] pieces, in running order."""
    u, v, key = step
    d = H.edges[u, v, key]
    if d["nodes"][0] == u:
        return d["nodes"], d["pieces"]
    return d["nodes"][::-1], d["pieces"][::-1]


def _heading(G, chain, look_m=15):
    """Direction (radians anticlockwise from east) from chain[0], ~look_m along."""
    x0, y0 = G.nodes[chain[0]]["x"], G.nodes[chain[0]]["y"]
    for n in chain[1:]:
        x, y = G.nodes[n]["x"], G.nodes[n]["y"]
        if math.hypot(x - x0, y - y0) >= look_m:
            break
    return math.atan2(y - y0, x - x0)


def _label(pieces, within_m=60):
    """What to call a stretch: its first name within `within_m`, else its kind."""
    run = 0
    for name, kind, metres in pieces:
        if name:
            return _street(name) if kind in STREET_KINDS else trail_key(name)
        run += metres
        if run >= within_m:
            break
    return THE_UNNAMED[pieces[0][1]]


def _street_crossed(G, chain, pieces, max_m=60):
    """If a stretch starts by crossing a street: (street, what it leads to).

    The crossing is the stretch's leading run of street/sidewalk/connector,
    up to `max_m`. The street is our connector's "Cross ..." name, a named
    street piece, or a street that meets the crossing's nodes. Returns
    (None, None) if it isn't a crossing.
    """
    lead = []
    for piece in pieces:
        if piece[1] in {"park trail", "other path"}:
            break
        lead.append(piece)
    lead_m = sum(p[2] for p in lead)
    if not lead or lead_m > max_m:
        return None, None
    rest = pieces[len(lead) :]
    after = _label(rest) if rest else None

    for name, kind, _ in lead:
        if kind == "connector" and name and name.startswith("Cross "):
            return _street(name.removeprefix("Cross ")), after
        if kind == "street" and name and "Trail" not in name:
            return _street(name), after
    nodes, walked = [chain[0]], 0.0
    for a, b in pairwise(chain):
        walked += G.edges[a, b]["length"]
        if walked > lead_m + 0.5:
            break
        nodes.append(b)
    inside = set(pairwise(chain)) | set(pairwise(chain[::-1]))
    streets = Counter(
        G.edges[n, m]["name"]
        for n in nodes
        for m in G[n]
        if (n, m) not in inside
        and G.edges[n, m]["kind"] == "street"
        and G.edges[n, m]["name"]
    )
    if not streets:
        return None, None
    return _street(streets.most_common(1)[0][0]), after


def _near(G, node, landmarks, within_m=30):
    x, y = G.nodes[node]["x"], G.nodes[node]["y"]
    for lx, ly, label in landmarks:
        if math.hypot(lx - x, ly - y) <= within_m:
            return label
    return None


# Short forms for printed cards: arrows instead of words.
ARROWS = {
    ("turn", "left"): "←", ("turn", "right"): "→",
    ("slight", "left"): "↖", ("slight", "right"): "↗",
    ("sharp", "left"): "↙", ("sharp", "right"): "↘",
}  # fmt: skip
SHORT_UNNAMED = {
    "the unnamed trail": "trail",
    "the crossing": "crossing",
    "the path": "path",
    "the sidewalk": "sidewalk",
    "the street": "street",
}
SHORT_LANDMARK = {"toilets": "WC", "water fountain": "water"}
SHORT_COMPASS = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]


def _junction(G, H, prev, step):
    """(full, short) instruction at the junction between two steps, or None."""
    u = step[0]
    in_chain, in_pieces = _oriented(H, prev)
    out_chain, out_pieces = _oriented(H, step)
    arrive = _heading(G, in_chain[::-1]) + math.pi

    def turn(chain):
        return math.degrees(math.remainder(_heading(G, chain) - arrive, math.tau))

    ours = turn(out_chain)
    others = []
    for a, b, key in H.edges(u, keys=True):
        if edge_id(a, b, key) in (edge_id(*prev), edge_id(*step)):
            continue
        chain = H.edges[a, b, key]["nodes"]
        others.append(turn(chain if chain[0] == u else chain[::-1]))

    here, there = _label(in_pieces[::-1]), _label(out_pieces)
    unnamed = there.startswith("the ")
    same = here == there and not unnamed
    onto = f"to stay on {there}" if same else f"onto {there}"
    short = SHORT_UNNAMED.get(there, there)
    side = "left" if ours > 0 else "right"
    ahead = [t for t in others if abs(t) < 60]

    if abs(ours) < 60 and ahead:  # a fork: say which branch
        side = "left" if ours > max(ahead) else "right"
        return f"Keep {side} {onto}", f"Y{ARROWS['slight', side]} {short}"
    if abs(ours) < 30:  # straight on: only worth saying if the name changes
        return None if same or unnamed else (f"Continue onto {there}", f"↑ {short}")
    if all(abs(t) >= 45 for t in others) and any(t * ours < 0 for t in others):
        return f"At the T, turn {side} {onto}", f"T{ARROWS['turn', side]} {short}"
    how = "slight" if abs(ours) < 60 else "turn" if abs(ours) < 135 else "sharp"
    return f"{how.title()} {side} {onto}", f"{ARROWS[how, side]} {short}"


def directions(G, H, steps, start="the Park Centre", landmarks=(), merge_m=30):
    """Turn-by-turn directions: [[km, full text, short text], ...].

    Instructions come at junctions where the route turns, forks or changes
    trail, plus street crossings. The short text is for printed cards
    ("T← Salish", "Y↗ Council"). Instructions within `merge_m` of each other
    are joined ("Cross W 16th Ave, then turn left onto ..."). `landmarks` are
    (x, y, label) points, e.g. toilets, mentioned when a junction is near one.
    """
    chain, pieces = _oriented(H, steps[0])
    octant = round(_heading(G, chain) / (math.pi / 4)) % 8
    first = _label(pieces)
    out = [
        [
            0.0,
            f"Start at {start}, heading {COMPASS[octant]} on {first}",
            f"Start: {SHORT_COMPASS[octant]} on {SHORT_UNNAMED.get(first, first)}",
        ]
    ]
    at = H.edges[steps[0]]["length"]
    crossing_before, crossing_at = None, None
    mentioned = {}  # landmark -> metres where we last mentioned it
    for prev, step in pairwise(steps):
        if edge_id(*prev) == edge_id(*step) and step[0] == prev[1]:
            out.append([at, "Turn around and return the same way", "↩ Turn around"])
            break
        chain, pieces = _oriented(H, step)
        street, after = _street_crossed(G, chain, pieces)
        here = _label(_oriented(H, prev)[1][::-1])
        says_where = after and not after.startswith("the ") and after != here
        text = None
        if street and street != crossing_before:
            full = f"Cross {street}" + (f" to {after}" if says_where else "")
            text = (full, full)
            crossing_at = len(out)
        elif street and says_where and " to " not in out[crossing_at][1]:
            # Still crossing the same street: say where it comes out.
            out[crossing_at][1] += f" to {after}"
            out[crossing_at][2] += f" to {after}"
        elif not street:
            text = _junction(G, H, prev, step)
        crossing_before = street
        if text:
            full, short = text
            landmark = _near(G, step[0], landmarks)
            if landmark and at - mentioned.get(landmark, -1e9) < 150:
                landmark = None  # just said it
            if landmark:
                mentioned[landmark] = at
                full += f" (by the {landmark})"
                short += f" ({SHORT_LANDMARK.get(landmark, landmark)})"
            out.append([at, full, short])
        at += H.edges[step]["length"]
    total = sum(H.edges[s]["length"] for s in steps)
    out.append([total, f"Finish at {start}", "Finish"])

    merged = [out[0]]
    for metres, full, short in out[1:]:
        last = merged[-1]
        close = len(merged) > 1 and metres - last[0] < merge_m
        if close and not full.startswith("Finish"):
            if last[1].startswith("Continue onto"):
                merged[-1] = [metres, full, short]  # a brief stretch: skip its name
            else:
                last[1] = f"{last[1]}, then {full[0].lower()}{full[1:]}"
                last[2] = f"{last[2]} · {short}"
        else:
            merged.append([metres, full, short])
    return [[round(m / 1000, 2), full, short] for m, full, short in merged]
