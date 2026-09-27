import itertools
import math

import networkx as nx
import numpy as np
import pytest

import routing


def make_graph(edges, coords):
    """edges: (a, b, length, name, kind); coords: {node: (x, y)}."""
    G = nx.Graph()
    for n, (x, y) in coords.items():
        G.add_node(n, x=x, y=y)
    for a, b, length, name, kind in edges:
        G.add_edge(a, b, length=length, name=name, kind=kind)
    return G


def grid(n=6, spacing=100):
    """An n x n grid of named park trails, 100 m blocks."""
    coords = {(i, j): (i * spacing, j * spacing) for i in range(n) for j in range(n)}
    edges = []
    for i, j in itertools.product(range(n), repeat=2):
        if i + 1 < n:
            edges.append(((i, j), (i + 1, j), spacing, f"Row {j}", "park trail"))
        if j + 1 < n:
            edges.append(((i, j), (i, j + 1), spacing, f"Col {i}", "park trail"))
    return make_graph(edges, coords)


def test_contract_merges_chains_and_keeps_pieces_in_order():
    # 1 - 2 - 3 - 4 with a fork at 4: the chain 1..4 becomes one edge.
    G = make_graph(
        [
            (1, 2, 10, "Salal", "park trail"),
            (2, 3, 20, "Salal", "park trail"),
            (3, 4, 5, None, "sidewalk"),
            (4, 5, 7, "Heron", "park trail"),
            (4, 6, 8, "Heron", "park trail"),
        ],
        {n: (n, 0) for n in range(1, 7)},
    )
    H = routing.contract(G)

    assert set(H.nodes) == {1, 4, 5, 6}
    (edge,) = H.get_edge_data(1, 4).values()
    assert edge["nodes"] == [1, 2, 3, 4]
    assert edge["length"] == 35
    assert edge["pieces"] == [["Salal", "park trail", 30], [None, "sidewalk", 5]]
    assert edge["cost"] == 30 * 1.0 + 5 * routing.COST_PER_M["sidewalk"]


def test_cheapest_path_prefers_a_longer_trail_to_a_shorter_street():
    # a -> b directly by 100 m of street, or 200 m of trail via c.
    G = make_graph(
        [
            ("a", "b", 100, "W 16th", "street"),
            ("a", "c", 100, "Salal", "park trail"),
            ("c", "b", 100, "Salal", "park trail"),
            ("b", "d", 50, "Heron", "park trail"),
            ("a", "e", 50, "Heron", "park trail"),
        ],
        {"a": (0, 0), "b": (100, 0), "c": (50, 80), "d": (150, 0), "e": (-50, 0)},
    )
    H = routing.contract(G)
    steps = routing.cheapest_path(H, "a", "b")
    names = {name for name, _, _ in routing.route_pieces(H, steps)}
    assert names == {"Salal"}


def test_out_and_back_repeats_everything_once():
    H = routing.contract(grid(), keep=[(0, 0)])
    out = routing.cheapest_path(H, (0, 0), (3, 2))
    steps = out + [(v, u, k) for u, v, k in reversed(out)]
    stats = routing.route_stats(H, steps)
    assert stats["length_m"] == pytest.approx(1000)
    assert stats["repeat_share"] == pytest.approx(0.5)
    assert stats["trail_share"] == pytest.approx(1.0)


def test_loops_start_and_end_at_the_start_and_fit_the_distance():
    start = (0, 0)  # a corner: only two neighbours, so it must be kept
    H = routing.contract(grid(), keep=[start])
    loops = routing.make_loops(H, start, 1200, 2000, tries=300)

    assert loops
    for loop in loops:
        steps = loop["steps"]
        assert steps[0][0] == start and steps[-1][1] == start
        assert all(s[1] == t[0] for s, t in itertools.pairwise(steps))
        assert 1200 <= loop["length_m"] <= 2000


def test_pick_distinct_drops_near_duplicates():
    H = routing.contract(grid(), keep=[(0, 0)])
    loops = routing.make_loops(H, (0, 0), 1200, 2000, tries=300)
    kept = routing.pick_distinct(loops, max_overlap=0.6)

    assert 0 < len(kept) < len(loops)
    edge_sets = [{routing.edge_id(*s) for s in r["steps"]} for r in kept]
    for a, b in itertools.combinations(edge_sets, 2):
        assert len(a & b) / len(a | b) < 0.6


def test_cue_sheet_merges_tiny_bits_and_names_street_crossings():
    G = make_graph(
        [
            (1, 2, 500, "Salish Trail", "park trail"),
            (2, 3, 20, None, "park trail"),  # tiny unnamed bit: merged
            (3, 4, 700, "Salish", "park trail"),  # same trail, other spelling
            (4, 5, 30, "West 16th Avenue", "street"),  # short: a crossing
            (5, 6, 300, "Imperial Trail", "park trail"),
        ],
        {n: (n * 100, 0) for n in range(1, 7)},
    )
    H = routing.contract(G)
    steps = routing.cheapest_path(H, 1, 6)
    cues = routing.cue_sheet(H, steps)

    assert [c[0] for c in cues] == [
        "Salish Trail",
        "Cross W 16th Ave",
        "Imperial Trail",
    ]
    assert cues[0][2] == pytest.approx(1.22)
    assert cues[1][1] == pytest.approx(1.22)
    assert cues[2][1] == pytest.approx(1.25)


def test_route_name_treats_trail_suffix_as_the_same_trail():
    G = make_graph(
        [
            (1, 2, 900, "Sherry Sakamoto", "park trail"),
            (2, 3, 800, "Sherry Sakamoto Trail", "park trail"),
            (3, 4, 700, "Council Trail", "park trail"),
            (4, 5, 100, "Imperial Trail", "park trail"),
        ],
        {n: (n * 100, 0) for n in range(1, 6)},
    )
    H = routing.contract(G)
    steps = routing.cheapest_path(H, 1, 5)
    assert routing.route_name(H, steps) == "Sherry Sakamoto, Council & Imperial"


# Elevation -------------------------------------------------------------------


def test_climb_ignores_noise_but_counts_a_real_hill():
    rng = np.random.default_rng(0)
    along = np.arange(0, 1000, 5.0)
    hill = 20 * np.exp(-(((along - 500) / 120) ** 2))  # up 20 m and back down
    noise = rng.uniform(-0.3, 0.3, along.size)
    G = nx.Graph()
    for i, (x, z) in enumerate(zip(along, hill + noise)):
        G.add_node(i, x=x, y=0.0, z=z)
    nodes = list(G.nodes)

    at, z, _, _ = routing.elevation_profile(G, nodes)
    gain, loss = routing.climb(z)
    assert at[-1] == pytest.approx(995)
    assert 18 <= gain <= 21
    assert 18 <= loss <= 21

    flat_gain, _ = routing.climb(routing.elevation_profile(G, nodes[:60])[1])
    assert flat_gain < 1  # the first 300 m are flat: noise isn't climbing


# Directions ------------------------------------------------------------------


def star(approach, branches):
    """A junction J at (0, 0). We arrive from the south along `approach`;
    `branches` are (name, kind, degrees anticlockwise from east, length)."""
    edges = [("S", "J", 200, approach, "park trail")]
    coords = {"S": (0, -200), "J": (0, 0)}
    for i, (name, kind, deg, length) in enumerate(branches):
        end = f"B{i}"
        coords[end] = (
            length * math.cos(math.radians(deg)),
            length * math.sin(math.radians(deg)),
        )
        edges.append(("J", end, length, name, kind))
        # Give every branch end a way on, so J isn't the only junction.
        coords[f"{end}x"] = (coords[end][0] * 1.5, coords[end][1] * 1.5)
        edges.append((end, f"{end}x", length / 2, name, kind))
        edges.append((end, "S", 400, "Loop back", "park trail"))
    return make_graph(edges, coords)


def directions_via(G, branch):
    H = routing.contract(G, keep=["S"])
    steps = routing.cheapest_path(H, "S", "J") + routing.cheapest_path(H, "J", branch)
    return [d[1] for d in routing.directions(G, H, steps)[1:-1]]


def test_t_junction_says_at_the_t():
    G = star(
        "Salal", [("Heron", "park trail", 0, 150), ("Hemlock", "park trail", 180, 150)]
    )
    assert directions_via(G, "B0") == ["At the T, turn right onto Heron"]
    assert directions_via(G, "B1") == ["At the T, turn left onto Hemlock"]


def test_fork_says_keep_left_or_right():
    G = star(
        "Salish", [("Council", "park trail", 60, 150), ("Top", "park trail", 120, 150)]
    )
    assert directions_via(G, "B0") == ["Keep right onto Council"]
    assert directions_via(G, "B1") == ["Keep left onto Top"]


def test_straight_on_the_same_trail_needs_no_instruction():
    G = star(
        "Salish Trail",
        [("Salish", "park trail", 90, 150), ("Heron", "park trail", 180, 150)],
    )
    assert directions_via(G, "B0") == []
    assert directions_via(G, "B1") == ["Turn left onto Heron"]


def test_straight_onto_a_new_name_says_continue():
    G = star("Salal", [("Heron", "park trail", 95, 150), ("Top", "park trail", 0, 150)])
    assert directions_via(G, "B0") == ["Continue onto Heron"]


def test_crossing_a_street_then_turning_is_one_instruction():
    # S -(trail)- J -(20 m crossing footway over W 16th)- K -(trail)- ...
    G = make_graph(
        [
            ("S", "J", 200, "Salish", "park trail"),
            ("J", "R", 10, None, "sidewalk"),  # crossing footway, to the road node
            ("R", "K", 10, None, "sidewalk"),
            ("W", "R", 100, "West 16th Avenue", "street"),
            ("R", "E", 100, "West 16th Avenue", "street"),
            ("K", "L", 150, "Sherry Sakamoto", "park trail"),
            ("K", "M", 150, "Douglas Fir", "park trail"),
            ("J", "X", 150, "Heron", "park trail"),
        ],
        {"S": (0, -200), "J": (0, 0), "R": (0, 10), "K": (0, 20), "W": (-100, 10),
         "E": (100, 10), "L": (-150, 20), "M": (0, 170), "X": (150, 0)},
    )  # fmt: skip
    H = routing.contract(G, keep=["S"])
    steps = routing.cheapest_path(H, "S", "L")
    texts = [d[1] for d in routing.directions(G, H, steps)[1:-1]]
    assert texts == ["Cross W 16th Ave, then turn left onto Sherry Sakamoto"]


def test_out_and_back_turns_around_and_retraces():
    G = star(
        "Salal", [("Heron", "park trail", 0, 150), ("Hemlock", "park trail", 180, 150)]
    )
    H = routing.contract(G, keep=["S"])
    out = routing.cheapest_path(H, "S", "B0")
    steps = out + [(v, u, k) for u, v, k in reversed(out)]
    cues = routing.directions(G, H, steps, start="the car park")
    assert cues[0][1] == "Start at the car park, heading north on Salal"
    assert cues[-2][1].startswith("Turn around")
    assert cues[-1][:2] == [pytest.approx(0.7), "Finish at the car park"]


def test_crossing_at_the_start_of_a_stretch_says_where_it_leads():
    # The crossing footway and the trail beyond it are one stretch (no junction).
    G = make_graph(
        [
            ("S", "J", 200, "Cleveland Trail", "park trail"),
            ("J", "R", 12, None, "sidewalk"),
            ("R", "K", 12, None, "sidewalk"),
            ("K", "T", 300, "Salal Trail", "park trail"),
            ("W", "R", 100, "West 16th Avenue", "street"),
            ("R", "E", 100, "West 16th Avenue", "street"),
            ("J", "X", 150, "Heron", "park trail"),
            ("T", "Y", 100, "Salal Trail", "park trail"),
            ("T", "Z", 100, "Vine Maple", "park trail"),
        ],
        {"S": (0, -200), "J": (0, 0), "R": (0, 12), "K": (0, 24), "T": (0, 324),
         "W": (-100, 12), "E": (100, 12), "X": (150, 0), "Y": (0, 424), "Z": (100, 324)},
    )  # fmt: skip
    H = routing.contract(G, keep=["S"])
    steps = routing.cheapest_path(H, "S", "Y")
    texts = [d[1] for d in routing.directions(G, H, steps)[1:-1]]
    assert texts == ["Cross W 16th Ave to Salal"]


def test_a_brief_continue_onto_is_dropped_before_the_next_turn():
    # 10 m of Sherry Sakamoto between the Salish junction and a turn onto Top.
    G = make_graph(
        [
            ("S", "J", 200, "Salish", "park trail"),
            ("J", "K", 10, "Sherry Sakamoto", "park trail"),
            ("J", "X", 150, "Heron", "park trail"),
            ("K", "L", 150, "Top", "park trail"),
            ("K", "M", 150, "Sherry Sakamoto", "park trail"),
        ],
        {"S": (0, -200), "J": (0, 0), "K": (0, 10), "X": (150, 0),
         "L": (-150, 10), "M": (0, 160)},
    )  # fmt: skip
    H = routing.contract(G, keep=["S"])
    steps = routing.cheapest_path(H, "S", "L")
    texts = [d[1] for d in routing.directions(G, H, steps)[1:-1]]
    assert texts == ["Turn left onto Top"]


def test_straight_onto_something_unnamed_says_nothing():
    G = star("Salal", [(None, "park trail", 92, 150), ("Top", "park trail", 0, 150)])
    assert directions_via(G, "B0") == []


def test_long_bilingual_names_are_shortened():
    G = star("Salal", [("Camosun Bog | xʷməm̓qʷe:m Boardwalk", "park trail", 0, 150),
                       ("Hemlock", "park trail", 180, 150)])  # fmt: skip
    assert directions_via(G, "B0") == ["At the T, turn right onto Camosun Bog"]


def test_short_directions_for_printed_cards():
    G = star(
        "Salish", [("Council", "park trail", 60, 150), ("Top", "park trail", 120, 150)]
    )
    H = routing.contract(G, keep=["S"])
    steps = routing.cheapest_path(H, "S", "J") + routing.cheapest_path(H, "J", "B0")
    short = [d[2] for d in routing.directions(G, H, steps)]
    assert short == ["Start: N on Salish", "Y↗ Council", "Finish"]

    G = star(
        "Salal", [("Heron", "park trail", 0, 150), ("Hemlock", "park trail", 180, 150)]
    )
    H = routing.contract(G, keep=["S"])
    steps = routing.cheapest_path(H, "S", "J") + routing.cheapest_path(H, "J", "B1")
    toilets = [(0.0, 5.0, "toilets")]
    short = [d[2] for d in routing.directions(G, H, steps, landmarks=toilets)]
    assert short[1] == "T← Hemlock (WC)"


def test_a_landmark_is_mentioned_once_not_at_every_nearby_junction():
    # Two junctions 20 m apart, both beside the same toilets.
    G = make_graph(
        [
            ("S", "J", 200, "Salish", "park trail"),
            ("J", "K", 20, "Top", "park trail"),
            ("J", "X", 150, "Heron", "park trail"),
            ("K", "L", 150, "Long", "park trail"),
            ("K", "M", 150, "Top", "park trail"),
        ],
        {"S": (0, -200), "J": (0, 0), "K": (-20, 0), "X": (150, 0),
         "L": (-20, 150), "M": (-170, 0)},
    )  # fmt: skip
    H = routing.contract(G, keep=["S"])
    steps = routing.cheapest_path(H, "S", "L")
    toilets = [(-10.0, 5.0, "toilets")]
    cues = routing.directions(G, H, steps, landmarks=toilets)
    assert cues[1][2] == "T← Top (WC) · → Long"
