"""Reading the export mesh: attribute readers, the moves along tool_path in
world space, and the checks on their values. Blender-facing; the G-code
text is built from these arrays by gcode_write."""

from contextlib import contextmanager

import numpy as np

from . import gcode_source
from .attributes import (TOOL_PATH, USE_FOR_HEIGHT, FACE_NUMBER, FLOW_VALUE, SPEED_VALUE,
                         FAN_VALUE, CONTINUOUS_VALUES, G_CODE_STRING, NAME_INFIXES,
                         masked_stages)
from .gcode_write import FILAMENT_AREA, format_snippet

# Path points this far below Z 0 (mm) count as rounding, not a bed crash.
Z_TOLERANCE = 1e-4
# Moves shorter than this (mm) are skipped: they would write a bare G1 E0.
MIN_MOVE = 1e-6

def read_verts(mesh):
    mverts_co = np.zeros((len(mesh.vertices) * 3), dtype=float)
    mesh.vertices.foreach_get("co", mverts_co)
    return np.reshape(mverts_co, (len(mesh.vertices), 3))


def read_edges(mesh): #return np.array
    fastedges = np.zeros((len(mesh.edges)*2), dtype=int) # [0.0, 0.0] * len(mesh.edges)
    mesh.edges.foreach_get("vertices", fastedges)
    return np.reshape(fastedges, (len(mesh.edges), 2))


SCALAR_ATTRIBUTE_DTYPES = {'FLOAT': np.float32, 'INT': np.int32}


def read_scalar_attribute(attr):
    """Values of a FLOAT or INT attribute as a float64 array, or None for other types."""
    dtype = SCALAR_ATTRIBUTE_DTYPES.get(attr.data_type)
    if dtype is None or len(attr.data) == 0:
        return None
    values = np.empty(len(attr.data), dtype=dtype)
    attr.data.foreach_get('value', values)
    return values.astype(np.float64)


def read_boolean_attribute(attr):
    """Values of a BOOLEAN attribute as a bool array, or None for other types."""
    if attr.data_type != 'BOOLEAN' or len(attr.data) == 0:
        return None
    values = np.empty(len(attr.data), dtype=bool)
    attr.data.foreach_get('value', values)
    return values



def _mesh_attribute(mesh, name, domain, data_types):
    attr = mesh.attributes.get(name)
    if attr is None:
        raise ValueError(f"Mesh has no '{name}' attribute")
    if attr.domain != domain or attr.data_type not in data_types:
        raise ValueError(f"'{name}' must be a {domain} {'/'.join(data_types)} attribute, "
                         f"found {attr.domain} {attr.data_type}")
    return attr


def read_face_values(mesh, name):
    """A FLOAT or INT face attribute as a float64 array."""
    return read_scalar_attribute(_mesh_attribute(mesh, name, 'FACE', ('FLOAT', 'INT')))


def read_face_mask(mesh, name):
    """A BOOLEAN face attribute as a bool array."""
    return read_boolean_attribute(_mesh_attribute(mesh, name, 'FACE', ('BOOLEAN',)))


def read_path_moves(mesh, verts, edges):
    """The extrusion moves along the tool_path edges, in vertex index order.

    Returns parallel arrays, one row per move from vert start[k] to start[k]+1:
    start, face (polygon holding the move's values) and height (length of the
    use_for_height edge at the start vert). A move whose start does not follow
    on from the previous move begins a new run, which is reached by a travel."""
    tool_path = read_boolean_attribute(_mesh_attribute(mesh, TOOL_PATH, 'EDGE', ('BOOLEAN',)))
    path_edges = np.sort(edges[tool_path], axis=1)
    if len(path_edges) == 0:
        raise ValueError(f"No edges are marked '{TOOL_PATH}'")
    bad = np.flatnonzero(path_edges[:, 1] - path_edges[:, 0] != 1)
    if len(bad):
        a, b = path_edges[bad[0]]
        raise ValueError(f"'{TOOL_PATH}' edge {a}-{b} does not join consecutive vertices "
                         f"({len(bad)} such edges); vertex order is scrambled")
    start = np.sort(path_edges[:, 0])
    if np.any(np.diff(start) == 0):
        raise ValueError(f"Duplicate '{TOOL_PATH}' edges")

    face_number = read_face_values(mesh, FACE_NUMBER).astype(np.int64)
    if face_number.min() < 1 or face_number.max() >= len(verts):
        raise ValueError(f"'{FACE_NUMBER}' out of range 1-{len(verts) - 1}")
    if len(np.unique(face_number)) != len(face_number):
        raise ValueError(f"'{FACE_NUMBER}' has duplicate values")
    face_of_move = np.full(len(verts), -1, dtype=np.int64)
    face_of_move[face_number - 1] = np.arange(len(face_number))
    face = face_of_move[start]
    missing = np.flatnonzero(face < 0)
    if len(missing):
        v = start[missing[0]]
        raise ValueError(f"No face with '{FACE_NUMBER}' {v + 1} for the move from vert {v} to {v + 1}")

    use_for_height = read_boolean_attribute(_mesh_attribute(mesh, USE_FOR_HEIGHT, 'EDGE', ('BOOLEAN',)))
    height_edges = edges[use_for_height]
    on_path = np.zeros(len(verts), dtype=bool)
    on_path[path_edges.ravel()] = True
    path_vert = np.where(on_path[height_edges[:, 0]], height_edges[:, 0], height_edges[:, 1])
    height_at = np.full(len(verts), np.nan)
    height_at[path_vert] = np.linalg.norm(verts[height_edges[:, 0]] - verts[height_edges[:, 1]], axis=1)
    height = height_at[start]
    missing = np.flatnonzero(np.isnan(height))
    if len(missing):
        raise ValueError(f"No '{USE_FOR_HEIGHT}' edge at vert {start[missing[0]]}")

    return start, face, height

def _check_moves(name, bad, number, problem, values=None, number_name=FACE_NUMBER):
    """Stop the export at the first move flagged in bad, naming the object,
    where the move is (number, e.g. its face_number) and (if given) the
    value there."""
    flagged = np.flatnonzero(bad)
    if len(flagged) == 0:
        return
    k = flagged[0]
    shown = f" ({values[k]:g})" if values is not None else ""
    more = f" and {len(flagged) - 1} more" if len(flagged) > 1 else ""
    raise ValueError(f"'{name}': {problem}{shown} at {number_name} {number[k]}{more}")


def _check_stages(stages):
    """Stop on stage attributes the export can't tell apart."""
    by_label = {}
    for stage in stages:
        by_label.setdefault(stage.label, []).append(stage.mask)
        bad = [infix for infix in NAME_INFIXES if infix in stage.label]
        if bad:
            raise ValueError(f"{stage.mask}: the Name '{stage.label}' can't contain '{bad[0]}'")
        clash = [attr for _, attr in stage.values if attr in CONTINUOUS_VALUES]
        if clash:
            raise ValueError(f"{stage.mask}: the Name '{stage.label}' clashes with {clash[0]} - "
                             f"give the stage a different Name")
        names = [name for name, _ in stage.values]
        twice = sorted({name for name in names if names.count(name) > 1})
        if twice:
            attrs = [attr for name, attr in stage.values if name == twice[0]]
            raise ValueError(f"{' and '.join(attrs)} are both {{{twice[0]}}}")
    for label, masks in by_label.items():
        if len(masks) > 1:
            raise ValueError(f"{' and '.join(masks)} are two stages named '{label}' - "
                             f"give the stages different Names")


def _masked_snippets(mesh, face, face_number, templates):
    """{move index: [G-code]} for every stage whose mask turns on at that
    move, in _pNN order, plus warnings for masks without a template and
    templates without a mask. face_number is each move's, for errors.

    A snippet fires once where its mask turns on; the following moves that
    stay inside the mask don't repeat it, even across a travel. Its
    placeholders are filled from the stage's values at that move; values the
    text doesn't use are ignored."""
    snippets = {}
    warnings = []
    stages = masked_stages(mesh)
    _check_stages(stages)
    for stage in stages:
        if stage.label not in templates:
            warnings.append(f"no {G_CODE_STRING} for '{stage.label}'")
            continue
        template, where = templates[stage.label]
        used = gcode_source.placeholders(template, where)
        available = dict(stage.values)
        missing = sorted(used - set(available))
        if missing:
            shown = ", ".join(f"{{{name}}}" for name in sorted(available))
            raise gcode_source.snippet_error(
                where, f"It uses {{{missing[0]}}}, but '{stage.label}' has "
                + (f"only {shown}." if shown else "no values."),
                f"{{{stage.label}}} is {stage.label}_value; {{name}} is {stage.label}_value_name.")
        on = read_face_mask(mesh, stage.mask)[face]
        fire = on & ~np.r_[False, on[:-1]]
        columns = {}
        for name in used:
            attr_name = available[name]
            values = read_face_values(mesh, attr_name)[face]
            _check_moves(stage.label, fire & ~np.isfinite(values), face_number,
                         f"{attr_name} is not a number", values)
            if mesh.attributes[attr_name].data_type == 'INT':
                values = values.astype(int)  # T{tool} -> T1, not T1.0
            columns[name] = values
        for k in np.flatnonzero(fire):
            snippets.setdefault(int(k), []).append(
                format_snippet(where, template, {name: column[k] for name, column in columns.items()}))
    labels = {stage.label for stage in stages}
    for label in sorted(set(templates) - labels):
        warnings.append(f"G-code for '{label}' unused (no {label}_mask_pNN on the mesh)")
    return snippets, warnings


@contextmanager
def evaluated_world_mesh(context, obj):
    """(evaluated mesh, its vertex positions in world space) for obj, with
    enabled Solidify modifiers switched off while it is read (they duplicate
    the faces). Stops if the node tree outputs instances, which the
    evaluated mesh leaves out."""
    solidify = [m for m in obj.modifiers if m.type == 'SOLIDIFY' and m.show_viewport]
    for mod in solidify:
        mod.show_viewport = False
    try:
        depsgraph = context.evaluated_depsgraph_get()
        if any(inst.is_instance and inst.parent is not None and inst.parent.original == obj
               for inst in depsgraph.object_instances):
            raise ValueError(f"'{obj.name}' outputs instances; add a Realize Instances node")
        mesh = obj.evaluated_get(depsgraph).data
        # World space before reading heights, so they are in world scale
        M = np.array(obj.matrix_world)
        yield mesh, read_verts(mesh) @ M[:3, :3].T + M[:3, 3]
    finally:
        for mod in solidify:
            mod.show_viewport = True


def read_object_moves(context, obj, width):
    """obj's moves along its tool_path edges as plain arrays in world space,
    read from the evaluated mesh (see evaluated_world_mesh). Returns None if
    there are no moves. width is the line width (mm)."""
    with evaluated_world_mesh(context, obj) as (mesh, verts):
        if mesh.attributes.get(TOOL_PATH) is None:
            raise ValueError(f"'{obj.name}' has no '{TOOL_PATH}' attribute")
        try:
            start, face, height = read_path_moves(mesh, verts, read_edges(mesh))
        except ValueError as e:
            raise ValueError(f"'{obj.name}':\n{e}") from None
        flow = read_face_values(mesh, FLOW_VALUE)[face]
        speed = read_face_values(mesh, SPEED_VALUE)[face]  # mm/s
        fan = read_face_values(mesh, FAN_VALUE)[face]
        face_number = start + 1  # face k is the move from vert k-1 to k
        try:
            snippets, warnings = _masked_snippets(mesh, face, face_number,
                                                  gcode_source.masked_templates(obj))
        except ValueError as e:
            raise ValueError(f"'{obj.name}':\n{e}") from None

    if len(start) == 0:
        return None
    new_run = np.ones(len(start), dtype=bool)
    new_run[1:] = start[1:] != start[:-1] + 1
    return checked_run(obj.name, verts[start], verts[start + 1], height, width, flow, speed, fan,
                       new_run, snippets, warnings, face_number)


def checked_run(name, P1, P2, height, width, flow, speed, fan, new_run, snippets, warnings,
                number, number_name=FACE_NUMBER):
    """The run dict gcode_write.write_path_moves takes, after the checks on
    the values: one row per move from P1 to P2 (world space), speed in mm/s,
    fan 0-255 or None to leave the fan alone. number names each move in
    errors (its face_number, or number_name). warnings is extended."""
    # Values a node tree can easily produce (e.g. an unclamped Map Range)
    # that would write invalid or dangerous G-code.
    def check(bad, problem, values=None):
        _check_moves(name, bad, number, problem, values, number_name)
    check(~(np.isfinite(P1).all(axis=1) & np.isfinite(P2).all(axis=1)), "position is not a number")
    for attr_name, values in ((FLOW_VALUE, flow), (SPEED_VALUE, speed), (FAN_VALUE, fan),
                              (USE_FOR_HEIGHT + " height", height)):
        if values is not None:
            check(~np.isfinite(values), f"{attr_name} is not a number", values)
    check(speed * 60 < 1, f"{SPEED_VALUE} must be at least 1/60 mm/s (F1)", speed)
    check(flow < 0, f"{FLOW_VALUE} is negative", flow)
    low_z = np.minimum(P1[:, 2], P2[:, 2])
    check(low_z < -Z_TOLERANCE, "path goes below Z 0", low_z)

    if fan is not None:
        clipped = np.count_nonzero((fan < 0) | (fan > 255))
        if clipped:
            warnings.append(f"{FAN_VALUE} outside 0-255 on {clipped} moves, clipped")
        fan = np.clip(np.round(fan), 0, 255).astype(int)
    dist = np.linalg.norm(P2 - P1, axis=1)
    zero = dist < MIN_MOVE
    if zero.any():
        warnings.append(f"{np.count_nonzero(zero)} zero-length moves skipped")
    return {
        "name": name,
        "P1": P1, "P2": P2,
        "E": dist * height * width * flow / FILAMENT_AREA,
        "speed": speed, "fan": fan, "new_run": new_run, "zero": zero,
        "snippets": snippets, "warnings": warnings,
        "last_z": float(max(P1[-1, 2], P2[-1, 2])),
    }


def last_move_z(mesh, matrix_world):
    """World Z of the higher end of the last move, or None without
    face_number. A cheap stand-in for read_object_moves' last_z for the
    panel: face_number k is the move from vert k-1 to k, so the last move
    ends at the largest face_number. An enabled Solidify can renumber the
    evaluated verts, so this may be off while it is on (the export switches
    Solidify off and is not affected)."""
    attr = mesh.attributes.get(FACE_NUMBER) if mesh else None
    values = read_scalar_attribute(attr) if attr is not None and attr.domain == 'FACE' else None
    if values is None:
        return None
    n = int(values.max())
    if n < 1 or n >= len(mesh.vertices):
        return None
    return max((matrix_world @ mesh.vertices[i].co).z for i in (n - 1, n))


def mask_starts(mesh, mask_name):
    """How many times a stage's text is written: the times its mask turns
    on along the faces in face_number order (the order of the moves), or
    None when the mask or face_number can't be read."""
    mask = mesh.attributes.get(mask_name) if mesh else None
    number = mesh.attributes.get(FACE_NUMBER) if mesh else None
    on = read_boolean_attribute(mask) if mask is not None and mask.domain == 'FACE' else None
    order = read_scalar_attribute(number) if number is not None and number.domain == 'FACE' else None
    if on is None or order is None or len(on) != len(order):
        return None
    on = on[np.argsort(order, kind='stable')]
    return int(np.count_nonzero(on & ~np.r_[False, on[:-1]]))
