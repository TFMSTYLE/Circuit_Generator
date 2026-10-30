# SPDX-FileCopyrightText: 2025-2026 The French Monkey
# SPDX-License-Identifier: GPL-3.0-or-later

bl_info = {
    "name": "Circuit",
    "author": "The French Monkey",
    "version": (1, 0, 1),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Circuit",
    "description": "Generate autorouted traces",
    "category": "Add Mesh",
    "license": "GPL-3.0-or-later",
}

import bpy
import bmesh
import random
import math
import heapq
from mathutils import Vector
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------
# Router & Density Config
# ---------------------------------------------------------

GRID_RES = 60  

DIRS = (
    (1, 0, 1.0), (-1, 0, 1.0),
    (0, 1, 1.0), (0, -1, 1.0),
    (1, 1, 1.4142135623730951), (1, -1, 1.4142135623730951),
    (-1, 1, 1.4142135623730951), (-1, -1, 1.4142135623730951),
)

TURN_PENALTY = 0.02 
MITER_LIMIT = 4.0
SQRT2 = 1.4142135623730951

# ---------------------------------------------------------
# PCB Presets
# ---------------------------------------------------------

PCB_PRESETS = {
    "CUSTOM": {
        "area": 12.0,
        "nodes": 90,
        "density": "BALANCED",
        "description": "Custom settings"
    },
    "ARDUINO_UNO": {
        "area": 6.86,
        "nodes": 60,
        "density": "BALANCED",
        "description": "Arduino Uno sized board (68.6mm x 53mm)"
    },
    "RASPBERRY_PI": {
        "area": 8.56,
        "nodes": 100,
        "density": "DENSE",
        "description": "Raspberry Pi sized board (85mm x 56mm)"
    },
    "SMALL_MCU": {
        "area": 4.0,
        "nodes": 30,
        "density": "BALANCED",
        "description": "Small microcontroller board"
    },
    "BREAKOUT": {
        "area": 2.5,
        "nodes": 15,
        "density": "SPARSE",
        "description": "Breakout board / module"
    },
    "LARGE_BOARD": {
        "area": 16.0,
        "nodes": 150,
        "density": "DENSE",
        "description": "Large development board"
    },
}

# ---------------------------------------------------------
# Grid <-> World 
# ---------------------------------------------------------

_grid_cache = {}

def get_grid_cache_key(area: float) -> tuple:
    return (round(area, 3), GRID_RES)

def grid_to_world(i: int, j: int, area: float) -> Vector:
    key = get_grid_cache_key(area)
    if key not in _grid_cache:
        t = GRID_RES - 1
        half_area = area * 0.5
        inv_t = 1.0 / t
        cache = {}
        for gi in range(GRID_RES):
            x_base = gi * inv_t * area - half_area
            for gj in range(GRID_RES):
                y = gj * inv_t * area - half_area
                cache[(gi, gj)] = Vector((x_base, y, 0.0))
        _grid_cache[key] = cache
    return _grid_cache[key][(i, j)].copy()

# ---------------------------------------------------------
# Geometry:
# ---------------------------------------------------------

def stroke_polyline(bm, pts, width):
    if len(pts) < 2:
        return
    half_w = width * 0.5
    for i in range(len(pts) - 1):
        p1, p2 = pts[i], pts[i + 1]
        d = p2 - p1
        l = d.length
        if l < 1e-6:
            continue
        d.normalize()
        perp = Vector((-d.y, d.x, 0))
        verts = [bm.verts.new(p1 + perp * half_w), bm.verts.new(p1 - perp * half_w),
                 bm.verts.new(p2 - perp * half_w), bm.verts.new(p2 + perp * half_w)]
        try: bm.faces.new(verts)
        except ValueError: pass
    for i in range(1, len(pts) - 1):
        c = pts[i]
        verts = []
        for a in range(0, 360, 45):
            r = math.radians(a)
            verts.append(bm.verts.new(Vector((c.x + math.cos(r) * half_w, c.y + math.sin(r) * half_w, 0))))
        for j in range(len(verts)):
            try: bm.faces.new([bm.verts.new(c), verts[j], verts[(j + 1) % len(verts)]])
            except ValueError: pass

def offset_polyline(pts, offset):
    if len(pts) == 1:
        return [pts[0].copy()]
    seg_norms = []
    for i in range(len(pts) - 1):
        d = pts[i + 1] - pts[i]
        l = d.length
        n = Vector((0, 1, 0)) if l < 1e-8 else Vector((-d.y / l, d.x / l, 0))
        seg_norms.append(n)
    out = []
    for i in range(len(pts)):
        if i == 0:
            n = seg_norms[0]
        elif i == len(pts) - 1:
            n = seg_norms[-1]
        else:
            n1, n2 = seg_norms[i - 1], seg_norms[i]
            n_avg = n1 + n2
            if n_avg.length_squared < 1e-16:
                n = n1
            else:
                n = n_avg.normalized()
                dot = n.dot(n1)
                if abs(dot) > 0.01:
                    n *= min(2.0, 1.0 / max(0.5, dot))
        out.append(pts[i] + n * offset)
    return out

# ---------------------------------------------------------
# Routing utilities
# ---------------------------------------------------------

def place_pads(count, rng, min_sep=3, edge_margin=2, line_spacing=1.0):
    adjusted_sep = max(2, int(min_sep * line_spacing))
    pads, spatial_hash = [], defaultdict(list)
    cell_size = adjusted_sep
    min_coord, max_coord = edge_margin, GRID_RES - edge_margin

    def get_hash_key(i, j): return (i // cell_size, j // cell_size)
    def check_neighbors_fast(i, j, key):
        ki, kj = key
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for pi, pj in spatial_hash[(ki + di, kj + dj)]:
                    if abs(pi - i) < adjusted_sep and abs(pj - j) < adjusted_sep:
                        return False
        return True

    tries, max_tries = 0, count * 200
    while len(pads) < count and tries < max_tries:
        tries += 1
        i, j = rng.randrange(min_coord, max_coord), rng.randrange(min_coord, max_coord)
        key = get_hash_key(i, j)
        if check_neighbors_fast(i, j, key):
            pads.append((i, j))
            spatial_hash[key].append((i, j))
    return pads

def cells_clearance_for_width(width_world, area):
    cell = area / (GRID_RES - 1)
    return max(1, int(math.ceil((width_world * 1.2) / cell)))

def reserve_path_nodes(path, blocked_nodes, keepout, line_spacing=1.0):
    k = max(0, int(keepout * line_spacing * 0.7))
    if k == 0:
        blocked_nodes.update(path)
        return
    offs = [(di, dj) for di in range(-k, k + 1) for dj in range(-k, k + 1)]
    blocked_nodes.update({
        (i + di, j + dj)
        for i, j in path
        for di, dj in offs
        if 0 <= i + di < GRID_RES and 0 <= j + dj < GRID_RES
    })

def detect_trace_clusters(routes, pads_connected, area, rng, cluster_probability=0.3):
    clusters = {}
    for idx, path in enumerate(routes):
        if len(path) < 20:
            continue
        if rng.random() < cluster_probability:
            clusters[idx] = rng.choice([2, 3, 3, 4, 5])
    return clusters

def create_parallel_traces(bm, path, area, trace_width, cluster_size, rng):
    poly = polyline_from_grid_path(path, area)
    if len(poly) < 2:
        return
    spacing = trace_width * rng.uniform(1.8, 2.4)
    if cluster_size == 2:
        offsets = [-spacing / 2, spacing / 2]
    elif cluster_size == 3:
        offsets = [-spacing, 0, spacing]
    elif cluster_size == 4:
        offsets = [-1.5 * spacing, -0.5 * spacing, 0.5 * spacing, 1.5 * spacing]
    else:
        offsets = [-2 * spacing, -spacing, 0, spacing, 2 * spacing]
    for off in offsets:
        width_var = trace_width * rng.uniform(0.95, 1.05)
        if abs(off) < 1e-6:
            stroke_polyline(bm, poly, width_var)
        else:
            stroke_polyline(bm, offset_polyline(poly, off), width_var)

def classify_route_type(path_length, rng):
    rand = rng.random()
    if path_length < 15:
        return ("POWER", 1.4) if rand < 0.15 else ("SIGNAL", 1.0)
    return ("POWER", 1.3) if rand < 0.05 else ("SIGNAL", 1.0)

def astar_longroute_maxlen(start, goal, blocked_nodes, wander_rng, wander_low, wander_high, max_len, area):
    if start == goal:
        return [start]
    si, sj = start
    gi, gj = goal
    HEURISTIC_WEIGHT = 0.25
    def h(a, b):
        dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
        return HEURISTIC_WEIGHT * (max(dx, dy) + 0.414 * min(dx, dy))
    openq, counter = [], 0
    heapq.heappush(openq, (0.0, counter, (si, sj), (0, 0), 0))
    came, gscore = {}, {(si, sj): 0.0}
    goal_tuple = (gi, gj)
    wander_values = [wander_rng.uniform(wander_low, wander_high) for _ in range(100)]
    wander_idx = 0
    while openq:
        _, _, cur, dprev, steps = heapq.heappop(openq)
        if cur == goal_tuple:
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            return list(reversed(path))
        if steps >= max_len:
            continue
        ci, cj = cur
        for di, dj, base_cost in DIRS:
            ni, nj = ci + di, cj + dj
            if ni < 0 or ni >= GRID_RES or nj < 0 or nj >= GRID_RES:
                continue
            nxt = (ni, nj)
            if nxt in blocked_nodes and nxt != goal_tuple:
                continue
            if di and dj:
                if ((ci + di, cj) in blocked_nodes) or ((ci, cj + dj) in blocked_nodes):
                    continue
            wander_idx = (wander_idx + 1) % 100
            cost = base_cost + wander_values[wander_idx]
            if dprev[0] or dprev[1]:
                cost += 0.02
                if di == dprev[0] and dj == dprev[1]:
                    cost -= 0.17  
            ng = gscore[cur] + cost
            if nxt not in gscore or ng < gscore[nxt]:
                gscore[nxt] = ng
                came[nxt] = cur
                f = ng + h(nxt, goal_tuple)
                counter += 1
                heapq.heappush(openq, (f, counter, nxt, (di, dj), steps + 1))
    return None

def polyline_from_grid_path(path, area):
    if not path:
        return []
    if len(path) == 1:
        return [grid_to_world(*path[0], area)]
    return [grid_to_world(i, j, area) for i, j in path]

# ---------------------------------------------------------
# Parallel Routing
# ---------------------------------------------------------

def route_single_connection(p, q, blocked_nodes, seed, wander, max_len, area):
    rng = random.Random(seed)
    path = astar_longroute_maxlen(p, q, blocked_nodes, rng, wander[0], wander[1], max_len, area)
    return p, q, path

def route_paths_parallel(pads, neighbors, blocked_nodes, base_seed, wander, max_len, area, conn_per_pad, line_spacing, max_workers=4):
    routes, used_pads, connections = [], set(), []
    seed_counter = base_seed
    for p in pads:
        cands = neighbors[p][:]
        random.Random(base_seed + hash(p)).shuffle(cands)
        for q in cands[:conn_per_pad]:
            connections.append((p, q, seed_counter))
            seed_counter += 1
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(route_single_connection, p, q, blocked_nodes.copy(), s, wander, max_len, area): (p, q)
                for p, q, s in connections}
        for f in as_completed(futs):
            p, q, path = f.result()
            if not path:
                continue
            if any(n in blocked_nodes and n not in (p, q) for n in path):
                continue
            routes.append(path)
            used_pads.update((p, q))
            reserve_path_nodes(path, blocked_nodes, max(1, int(line_spacing)), line_spacing)
    return routes, used_pads

# ---------------------------------------------------------
# PCB mesh builder
# ---------------------------------------------------------

def build_pcb_mesh(density, base_nodes, trace_width, area, seed, max_len, line_spacing, cluster_amount, use_parallel=True):
    rng = random.Random(seed)
    if density == "BALANCED":
        pad_mult, conn_per_pad, neighbor_k, keepout_extra, wander, edge_margin, allow_clusters = 2.5, 8, 40, -2, (0.02, 0.10), 2, True
    else:
        pad_mult, conn_per_pad, neighbor_k, keepout_extra, wander, edge_margin, allow_clusters = 1.5, 5, 25, -1, (0.05, 0.18), 2, False

    pad_count = max(5, round(base_nodes * pad_mult))
    pads = place_pads(pad_count, rng, min_sep=3, edge_margin=edge_margin, line_spacing=line_spacing)
    blocked_nodes = set(pads)
    keepout = max(1, int((cells_clearance_for_width(trace_width, area) + keepout_extra) * line_spacing))

    pad_vec = {p: Vector((p[0], p[1], 0)) for p in pads}
    neighbors = {}
    for p in pads:
        pv = pad_vec[p]
        dists = [(q, (pv - pad_vec[q]).length_squared) for q in pads if q != p]
        dists.sort(key=lambda x: x[1])
        close_count = neighbor_k // 4
        close_neighbors = [q for q, _ in dists[:close_count]]
        distant_start = int(len(dists) * 0.4)
        distant_candidates = [q for q, _ in dists[distant_start:]]
        random.Random(seed + hash(p)).shuffle(distant_candidates)
        far_neighbors = distant_candidates[: neighbor_k - close_count]
        neighbors[p] = close_neighbors + far_neighbors

    pad_connections = defaultdict(int)
    connection_pairs = set()

    if use_parallel and len(pads) > 20:
        routes, used_pads = route_paths_parallel(pads, neighbors, blocked_nodes, seed, wander, max_len, area, conn_per_pad, line_spacing, max_workers=4)
        for path in routes:
            if path:
                s, e = path[0], path[-1]
                pad_connections[s] += 1
                pad_connections[e] += 1
                connection_pairs.add((min(s, e), max(s, e)))
    else:
        routes, used_pads = [], set()
        for p in pads:
            routed = 0
            cand = neighbors[p][:]
            rng.shuffle(cand)
            for q in cand:
                if routed >= conn_per_pad:
                    break
                if pad_connections[p] >= 2 or pad_connections[q] >= 2:
                    continue
                pair = (min(p, q), max(p, q))
                if pair in connection_pairs:
                    continue
                path = astar_longroute_maxlen(p, q, blocked_nodes, rng, wander[0], wander[1], max_len, area)
                if not path:
                    continue
                if routes:
                    path_set = frozenset(path[1:-1])
                    if any(path_set & frozenset(r[1:-1]) for r in routes):
                        continue
                reserve_path_nodes(path, blocked_nodes, keepout, line_spacing)
                routes.append(path)
                used_pads.update((p, q))
                pad_connections[p] += 1
                pad_connections[q] += 1
                connection_pairs.add(pair)
                routed += 1

    pads_connected = [p for p in pads if p in used_pads]

    bm = bmesh.new()
    pad_r = trace_width * 1.3
    for i, j in pads_connected:
        c = grid_to_world(i, j, area)
        segs = 16
        ring = [bm.verts.new(Vector((c.x + math.cos(a) * pad_r, c.y + math.sin(a) * pad_r, 0)))
                for a in (k * (math.tau / segs) for k in range(segs))]
        try: bm.faces.new(ring)
        except ValueError: pass

    trace_clusters = detect_trace_clusters(routes, pads_connected, area, rng, cluster_probability=cluster_amount)

    for idx, path in enumerate(routes):
        if path[0] not in used_pads and path[-1] not in used_pads:
            continue
        if idx in trace_clusters:
            create_parallel_traces(bm, path, area, trace_width, trace_clusters[idx], rng)
        else:
            _, wmul = classify_route_type(len(path), rng)
            stroke_polyline(bm, polyline_from_grid_path(path, area), trace_width * wmul)

    mesh = bpy.data.meshes.new("CircuitPCB")
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return mesh

# ---------------------------------------------------------
# Rebuild / Generate / Live Update
# ---------------------------------------------------------

def rebuild_circuit(obj):
    if not obj or obj.type != "MESH":
        return False
    d = obj.get("c_density", "BALANCED")
    n = obj.get("c_nodes", 180)
    w = obj.get("c_width", 0.008)
    a = obj.get("c_area", 12.0)
    s = obj.get("c_seed", 0)
    m = obj.get("c_maxlen", 400)
    l = obj.get("c_linespacing", 0.4)
    c = obj.get("c_cluster_amount", 0.4)
    p = obj.get("c_parallel", True)
    _grid_cache.clear()
    mesh = build_pcb_mesh(d, n, w, a, s, m, l, c, p)
    old = obj.data
    obj.data = mesh
    if old and old.users == 0:
        bpy.data.meshes.remove(old)
    return True

def apply_preset(context, preset_name):
    if preset_name == "CUSTOM":
        return
    preset = PCB_PRESETS.get(preset_name)
    if not preset:
        return
    props = context.scene.circuitgen_props
    props.c_area = preset["area"]
    props.c_nodes = preset["nodes"]
    props.c_density = preset["density"]

def generate_circuit(context):
    p = context.scene.circuitgen_props
    o = context.active_object
    keys = ("c_density", "c_nodes", "c_width", "c_area", "c_seed", "c_maxlen", 
            "c_linespacing", "c_cluster_amount", "c_parallel")
    if o and o.type == "MESH" and "circuit_generator" in o and o.select_get():
        for k in keys: o[k] = getattr(p, k)
        rebuild_circuit(o)
        return o
    mesh = bpy.data.meshes.new("Circuit")
    new_o = bpy.data.objects.new("Circuit", mesh)
    context.collection.objects.link(new_o)
    context.view_layer.objects.active = new_o
    new_o.select_set(True)
    for k in keys: new_o[k] = getattr(p, k)
    new_o["circuit_generator"] = True
    rebuild_circuit(new_o)
    return new_o

def update_circuit(self, context):
    p = context.scene.circuitgen_props
    if not p.live_update:
        return

    o = context.active_object
    target = None

    if o and o.type == "MESH" and "circuit_generator" in o and o.select_get():
        target = o
    elif o and o.parent and o.parent.type == "MESH" and "circuit_generator" in o.parent and o.parent.select_get():
        target = o.parent

    if not target:
        return

    keys = (
        "c_density", "c_nodes", "c_width", "c_area", "c_seed",
        "c_maxlen", "c_linespacing", "c_cluster_amount", "c_parallel"
    )
    for k in keys:
        target[k] = getattr(p, k)
    rebuild_circuit(target)

def update_preset(self, context):
    apply_preset(context, self.c_preset)
    if self.live_update:
        update_circuit(self, context)

# ---------------------------------------------------------
# Properties
# ---------------------------------------------------------

class CircuitGenProperties(bpy.types.PropertyGroup):

    live_update:       bpy.props.BoolProperty(name="Live Update", default=False, description="Automatically update circuit when changing parameters (may be slow for large boards)")

    c_preset:          bpy.props.EnumProperty(
        name="Preset", default="CUSTOM", update=update_preset,
        description="PCB size and complexity presets",
        items=[
            ("CUSTOM", "Custom", "Custom settings"),
            ("ARDUINO_UNO", "Arduino Uno", PCB_PRESETS["ARDUINO_UNO"]["description"]),
            ("RASPBERRY_PI", "Raspberry Pi", PCB_PRESETS["RASPBERRY_PI"]["description"]),
            ("SMALL_MCU", "Small MCU", PCB_PRESETS["SMALL_MCU"]["description"]),
            ("BREAKOUT", "Breakout Board", PCB_PRESETS["BREAKOUT"]["description"]),
            ("LARGE_BOARD", "Large Board", PCB_PRESETS["LARGE_BOARD"]["description"]),
        ]
    )

    c_density:         bpy.props.EnumProperty(name="Layout", default="BALANCED", update=update_circuit, description="Circuit routing density",
                         items=[("SPARSE", "Sparse", "Open layout with fewer, longer routes"), ("BALANCED", "Balanced", "Standard PCB layout with good coverage")])

    c_nodes:           bpy.props.IntProperty(name="Nodes", default=180, min=5, max=1000, subtype='FACTOR', update=update_circuit, description="Number of connection nodes")
    c_width:           bpy.props.FloatProperty(name="Trace Width", default=0.008, min=0.001, max=0.08, subtype='FACTOR', update=update_circuit, description="Base width of PCB traces")
    c_area:            bpy.props.FloatProperty(name="Board Area", default=12.0, min=2.0, max=50.0, subtype='FACTOR', update=update_circuit, description="Total size of the circuit board")
    c_maxlen:          bpy.props.IntProperty(name="Max Trace Length", default=400, min=20, max=600, subtype='FACTOR', update=update_circuit, description="Maximum routing path length (higher = longer traces)")
    c_linespacing:     bpy.props.FloatProperty(name="Line Spacing", default=0.4, min=0.2, max=2.0, subtype='FACTOR', update=update_circuit, description="Spacing between traces (lower = denser, more traces)")
    c_cluster_amount:  bpy.props.FloatProperty(name="Cluster Amount", default=0.4, min=0.0, max=1.0, subtype='FACTOR', update=update_circuit, description="Probability of traces forming parallel clusters (0=none, 1=maximum)")
    c_seed:            bpy.props.IntProperty(name="Seed", default=0, min=0, max=999999, subtype='FACTOR', update=update_circuit, description="Random seed for reproducible results")
    c_parallel:        bpy.props.BoolProperty(name="Parallel Routing", default=False, update=update_circuit, description="Use parallel processing for faster generation (recommended for large boards)")

# ---------------------------------------------------------
# Operator
# ---------------------------------------------------------

class CIRCUITGEN_OT_generate(bpy.types.Operator):
    bl_idname = "circuitgen.generate"
    bl_label = "Generate Circuit"
    bl_options = {"REGISTER", "UNDO"}
    
    def execute(self, context):
        generate_circuit(context)
        return {"FINISHED"}
    
# ---------------------------------------------------------
# UI
# ---------------------------------------------------------

class VIEW3D_PT_circuit(bpy.types.Panel):
    bl_label = "Circuit"
    bl_idname = "VIEW3D_PT_circuit"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Circuit"

    def draw(self, context):
        layout = self.layout
        p = context.scene.circuitgen_props
        o = context.object

        layout.operator("circuitgen.generate", icon="SYSTEM")
        layout.prop(p, "live_update", toggle=True)

        layout.separator(factor=0.5)

        layout.prop(p, "c_preset", text="Preset")
        layout.prop(p, "c_density", expand=True)

        layout.prop(p, "c_nodes")
        layout.prop(p, "c_width")
        layout.prop(p, "c_area")
        layout.prop(p, "c_maxlen")
        layout.prop(p, "c_linespacing", slider=True)
        layout.prop(p, "c_cluster_amount", slider=True)
        layout.prop(p, "c_seed")
        layout.prop(p, "c_parallel", toggle=True)

        layout.separator(factor=0.5)

        if o and o.type == "MESH" and "circuit_generator" in o and o.select_get():
            layout.label(text=f"Editing: {o.name}", icon="SYSTEM")
        else:
            layout.label(text="No circuit selected", icon="INFO")

# ---------------------------------------------------------
# Registration
# ---------------------------------------------------------

classes = (
    CircuitGenProperties,
    CIRCUITGEN_OT_generate,
    VIEW3D_PT_circuit, 
)

def register():
    """Register addon classes and properties."""
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.circuitgen_props = bpy.props.PointerProperty(type=CircuitGenProperties)

def unregister():
    """Unregister addon classes and properties."""
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.Scene.circuitgen_props

if __name__ == "__main__":
    register()