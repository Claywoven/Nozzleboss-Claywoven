"""LEGACY - files from before the Geometry Nodes attributes: per-segment
values painted as Flow/Speed/Tool/Fan corner colours, and the Start/End/T0/T1
text blocks. Kept for the foreseeable future as a second reader, but not
extended. It reads the evaluated mesh into the same run dict as
mesh_read.read_object_moves, and gcode_write writes it. To remove: delete
this file, the LEGACY lines in export.py, panel.py and attributes.py, and
the LEGACY settings."""

import bpy
import numpy as np

from . import overlay
from .attributes import (LEGACY_COLOURS, LEGACY_END_TEXT, LEGACY_FAN, LEGACY_FLOW, LEGACY_SPEED,
                         LEGACY_START_TEXT, LEGACY_TOOL, LEGACY_TOOL_TEXTS, TOOL_PATH)
from .mesh_read import checked_run, evaluated_world_mesh, read_edges
from .printer import find_printer, helper_tag, is_printer

# Extrusion width as a multiple of the nozzle_diameter setting.
WIDTH_FACTOR = 1.5

# Added to the export's result, so it is clear which export ran.
NOTE = "legacy colour export"
# Firmware retraction around long travels, as the original export wrote it
RETRACTION = ('G10 \n', 'G11 \n')

TOOLTIP = ("Export active Object to G-code. Result can be found in folder of current .blend file.\n"
           "Legacy colour export (Flow/Speed/Tool/Fan colours)")

# A missing colour reads as white (the old export created it white), except
# Fan: without it the export leaves the fan to the start code and T0/T1.
MISSING = {LEGACY_FLOW: "max flow", LEGACY_SPEED: "max speed",
           LEGACY_TOOL: "T0 at the start", LEGACY_FAN: "no fan changes"}

DEFAULT_TEXTS = {
    LEGACY_START_TEXT: (';nozzleboss\n'
                        'G28 ;homing\n'
                        'M104 S180 ;set hotend temp\n'
                        'M190 S50 ;wait for bed temp\n'
                        'M109 S200 ;wait for hotendtemp\n'
                        'M83; relative extrusion mode (REQUIRED)\n'),
    LEGACY_END_TEXT: ('G10 ;retract\n'
                      'M104 S0 ;deactivate hotend\n'
                      'M140 S0 ;deactivate bed\n'
                      'G28 ;homing\n'
                      'M84 ;turn off motors\n'),
}
for _name in LEGACY_TOOL_TEXTS:
    DEFAULT_TEXTS[_name] = f'{_name}; switch to extruder {_name} (any G-code macro can be passed here)\n'


def uses_legacy_export(obj):
    """True for a mesh with no printer and no tool_path after its modifiers.
    A mesh linked to a printer always takes the node-tree export, which
    stops if tool_path is missing (a tree that lost it must not fall back).
    A wrapper or profile (helper_tag) is never a legacy file."""
    if is_printer(obj) or helper_tag(obj) or find_printer(obj) is not None:
        return False
    mesh = overlay.evaluated_mesh(obj)
    return mesh is not None and mesh.attributes.get(TOOL_PATH) is None


def read_text(name, created):
    """The text block's G-code, each line ending in a newline. A missing
    block is first made from DEFAULT_TEXTS and its name added to created."""
    text = bpy.data.texts.get(name)
    if text is None:
        text = bpy.data.texts.new(name)
        text.write(DEFAULT_TEXTS[name])
        created.append(name)
    return ''.join(line.body + '\n' for line in text.lines)


def start_end(created):
    return read_text(LEGACY_START_TEXT, created), read_text(LEGACY_END_TEXT, created)


def _components(n_verts, edges):
    """Per vertex, the smallest vertex index joined to it by edges, or -1
    for a vertex on no edge. Hook each root to the smaller one, then jump
    pointers until every vertex points at its root."""
    label = np.arange(n_verts)
    while True:
        a, b = label[edges[:, 0]], label[edges[:, 1]]
        hooked = label.copy()
        np.minimum.at(hooked, np.maximum(a, b), np.minimum(a, b))
        while True:
            jumped = hooked[hooked]
            if np.array_equal(jumped, hooked):
                break
            hooked = jumped
        if np.array_equal(hooked, label):
            break
        label = hooked
    on_edge = np.zeros(n_verts, dtype=bool)
    on_edge[edges.ravel()] = True
    return np.where(on_edge, label, -1)


def _island_moves(verts, edges):
    """(v1, v2, v3, new_run, skipped) for every move, in print order.

    Each island (connected part) is a ribbon: the lower half of its vertex
    indices is the nozzle path, the upper half the matching bottom verts.
    Move k goes from v1 to v2 along the path, and v1-v3 is its height edge.
    Islands print lowest mean path Z first; equal Z keeps the order the
    old export found them in (first appearance in the edge list). skipped
    counts islands too short to hold a move."""
    label = _components(len(verts), edges)
    used = np.flatnonzero(label >= 0)
    order = used[np.lexsort((used, label[used]))]  # grouped by island, index order within
    island = label[order]
    start = np.flatnonzero(np.r_[True, island[1:] != island[:-1]])
    half = np.diff(np.r_[start, len(order)]) // 2

    flat = edges.ravel()
    first_seen = np.full(len(verts), len(flat))
    np.minimum.at(first_seen, label[flat], np.arange(len(flat)))
    z = np.r_[0.0, np.cumsum(verts[order, 2])]
    mean_z = (z[start + half] - z[start]) / np.maximum(half, 1)

    keep = half >= 2
    printed = np.flatnonzero(keep)
    # Rounded so float noise in the mean doesn't reorder islands on one layer.
    printed = printed[np.lexsort((first_seen[island[start[printed]]], np.round(mean_z[printed], 6)))]
    per = half[printed] - 1
    offset = np.repeat(np.cumsum(per) - per, per)
    k = np.arange(per.sum()) - offset
    p1 = np.repeat(start[printed], per) + k
    p3 = p1 + np.repeat(half[printed], per)
    return order[p1], order[p1 + 1], order[p3], k == 0, int(np.count_nonzero(~keep))


def _corners(mesh, edges, v1, v2):
    """For each move v1-v2, the face corner at v1 in the face holding that
    edge (the last such face, as the old export did); -1 where the edge is
    missing or on no face."""
    n_loops = len(mesh.loops)
    loop_vert = np.empty(n_loops, dtype=int)
    loop_edge = np.empty(n_loops, dtype=int)
    mesh.loops.foreach_get("vertex_index", loop_vert)
    mesh.loops.foreach_get("edge_index", loop_edge)
    loop_start = np.empty(len(mesh.polygons), dtype=int)
    loop_total = np.empty(len(mesh.polygons), dtype=int)
    mesh.polygons.foreach_get("loop_start", loop_start)
    mesh.polygons.foreach_get("loop_total", loop_total)
    next_loop = np.arange(1, n_loops + 1)
    next_loop[loop_start + loop_total - 1] = loop_start

    # Corners are stored face by face, so the highest corner on an edge is in its last face.
    last_corner = np.full(len(edges), -1)
    np.maximum.at(last_corner, loop_edge, np.arange(n_loops))

    n = len(mesh.vertices)
    edge_key = np.minimum(edges[:, 0], edges[:, 1]) * n + np.maximum(edges[:, 0], edges[:, 1])
    by_key = np.argsort(edge_key)
    move_key = np.minimum(v1, v2) * n + np.maximum(v1, v2)
    pos = np.minimum(np.searchsorted(edge_key[by_key], move_key), len(edges) - 1)
    edge = np.where(edge_key[by_key][pos] == move_key, by_key[pos], -1)
    corner = np.where(edge >= 0, last_corner[edge], -1)
    safe = np.maximum(corner, 0)
    return np.where(corner < 0, -1, np.where(loop_vert[safe] == v1, safe, next_loop[safe]))


def _colour_values(mesh, name, corner, v1):
    """The painted value (0-1) of each move from colour name: luma of the
    corner colour, back in sRGB so a colour picked as 0.5 reads 0.5. None if
    the mesh has no such colour attribute."""
    attr = mesh.color_attributes.get(name)
    if attr is None:
        return None
    if attr.domain == 'CORNER':
        index = corner
    elif attr.domain == 'POINT':
        index = v1
    else:
        raise ValueError(f"'{name}' must be a face corner or vertex colour, found {attr.domain}")
    rgba = np.empty(len(attr.data) * 4, dtype=np.float32)
    attr.data.foreach_get("color", rgba)
    rgb = rgba.reshape(-1, 4)[index, :3].astype(np.float64)
    luma = np.clip(rgb[:, 0] * 0.299 + rgb[:, 1] * 0.587 + rgb[:, 2] * 0.114, 0.0, 1.0)
    srgb = np.where(luma <= 0.0031308, luma * 12.92, 1.055 * luma ** (1 / 2.4) - 0.055)
    # Rounded so white reads exactly 1 (it comes back as 0.9999...).
    return np.round(srgb, 6)


def _remap(weight, low, high):
    return np.interp(weight, [0.0, 1.0], [low, high])


def read_object_moves(context, obj, settings):
    """(run, text blocks created) for a colour-attribute object: the same
    run dict as mesh_read.read_object_moves, with T0/T1 as snippets where
    the Tool colour changes. settings is scene.nozzleboss."""
    if not obj.visible_get():
        raise ValueError(f"'{obj.name}' is hidden; show it to export")
    warnings = []
    with evaluated_world_mesh(context, obj) as (mesh, verts):
        edges = read_edges(mesh)
        if len(edges) == 0:
            raise ValueError(f"'{obj.name}' has no edges to export")
        v1, v2, v3, new_run, skipped = _island_moves(verts, edges)
        if len(v1) == 0:
            raise ValueError(f"'{obj.name}' has no moves: no part has a path of two or more segments")
        corner = _corners(mesh, edges, v1, v2)
        off_face = np.flatnonzero(corner < 0)
        if len(off_face):
            a, b = v1[off_face[0]], v2[off_face[0]]
            raise ValueError(f"'{obj.name}': the move from vert {a} to {b} is not an edge of a face "
                             f"({len(off_face)} such moves) - not an extruded ribbon, or the vertex "
                             f"order is scrambled")
        values = {name: _colour_values(mesh, name, corner, v1) for name in LEGACY_COLOURS}
        P1, P2, P3 = verts[v1], verts[v2], verts[v3]

    missing = [name for name in LEGACY_COLOURS if values[name] is None]
    if missing:
        warnings.append(f"no {', '.join(missing)} colour: {', '.join(MISSING[n] for n in missing)}")
    if skipped:
        warnings.append(f"{skipped} parts too short for a move skipped")

    def painted(name):
        return np.ones(len(v1)) if values[name] is None else values[name]

    flow = _remap(painted(LEGACY_FLOW), settings.min_flow, settings.max_flow)
    speed = settings.extrusion_speed * _remap(painted(LEGACY_SPEED), settings.min_speed, settings.max_speed)
    fan = None
    if values[LEGACY_FAN] is not None:
        fan = np.round(_remap(values[LEGACY_FAN], settings.min_fan, settings.max_fan) * 255)

    run = checked_run(obj.name, P1, P2, np.linalg.norm(P3 - P1, axis=1),
                      settings.nozzle_diameter * WIDTH_FACTOR, flow, speed, fan,
                      new_run, {}, warnings, v1, "vert")

    # A tool block wherever the Tool value changes, light T0, dark T1. Read
    # only once the checks pass, so a failed export creates no text blocks.
    tool = painted(LEGACY_TOOL)
    created = []
    for k in np.flatnonzero(np.r_[True, tool[1:] != tool[:-1]]):
        name = LEGACY_TOOL_TEXTS[1] if tool[k] < 0.5 else LEGACY_TOOL_TEXTS[0]
        run["snippets"][int(k)] = [read_text(name, created)]
    return run, created


def has_fan(obj):
    """True when the evaluated mesh has a Fan colour."""
    mesh = overlay.evaluated_mesh(obj)
    return mesh is not None and mesh.color_attributes.get(LEGACY_FAN) is not None


def draw_panel(layout, settings, fan):
    """The whole panel while the active object uses the legacy export: the
    original nozzleboss export box, without its import box, so the old
    documentation still matches. fan adds a Fan row (the original had none)
    when the object has a Fan colour. The tool colour and text block fields
    are for show, as they were in the original (see settings.py)."""
    col = layout.box().column(align=True)
    col.label(text="Export settings:")
    col.prop(settings, "nozzle_diameter", text='Nozzle Size')
    col.prop(settings, "travel_speed", text='Travel Speed')
    col.prop(settings, "extrusion_speed", text='Extrusion Speed')

    col.separator(factor=1.5)

    row = col.row()
    row.label(text=" ")
    row.separator(factor=2)
    row.label(text="min:")
    row.label(text="max:")
    rows = [("Flow Multiplier:", "min_flow", "max_flow"),
            ("Speed Multiplier:", "min_speed", "max_speed")]
    if fan:
        rows.append(("Fan Speed:", "min_fan", "max_fan"))
    for k, (label, low, high) in enumerate(rows):
        if k:
            col.separator()
            col.label(text=" ")
        row = col.row(align=True)
        row.label(text=label)
        row.separator()
        row.prop(settings, low)
        row.prop(settings, high)

    col.separator(factor=2)

    col.prop(settings, 'tool_color')
    row = col.row(align=True)
    row.enabled = settings.tool_color
    row.prop(settings, "color_T0", text="")
    row.prop_search(settings, "T0", bpy.data, "texts", text="")
    row.separator(factor=2)
    row.prop(settings, "color_T1", text="")
    row.prop_search(settings, "T1", bpy.data, "texts", text="")

    col.separator(factor=2)

    row = col.row(align=True)
    row.label(text='Start G-code:')
    row.prop_search(settings, "start", bpy.data, "texts", text="")
    row = col.row(align=True)
    row.label(text='End G-code:')
    row.prop_search(settings, "end", bpy.data, "texts", text="")

    col.separator(factor=2)
    row = col.row(align=True)
    row.scale_y = 1.5
    row.operator("wm.gcode_export").legacy = True
