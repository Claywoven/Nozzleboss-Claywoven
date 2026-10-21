"""The printer object: finding it from an object's node trees, and reading
the settings the export uses from its attributes."""

import numpy as np

from . import node_tree, overlay
from .attributes import HELPER_TAGS, PRINTER_PROP, PRINTER_TAG, LINE_WIDTH, TRAVEL_SPEED
from .mesh_read import read_scalar_attribute


def is_printer(obj):
    """cw_prop == "printer" set on the object (Object tab) or its mesh (Data tab)."""
    if obj is None:
        return False
    if obj.get(PRINTER_PROP) == PRINTER_TAG:
        return True
    return obj.data is not None and obj.data.get(PRINTER_PROP) == PRINTER_TAG


def helper_tag(obj):
    """The cw_prop tag ("wrapper", "profile") of a helper object that is
    never exported, set on the object or its data in any case; else None."""
    if obj is None:
        return None
    for owner in (obj, obj.data):
        value = owner.get(PRINTER_PROP) if owner is not None else None
        if isinstance(value, str) and value.strip().lower() in HELPER_TAGS:
            return value.strip().lower()
    return None


def _is_connected(node):
    """True when any of the node's outputs has an unmuted link."""
    return any(not link.is_muted for socket in node.outputs for link in socket.links)


def find_printer(obj):
    """The printer object itself, or the first printer the object's live
    Geometry Nodes modifiers reference: an Object input on the modifier, or
    a connected Object Info node in any live group instance (traced up
    through group inputs). An Object Info node with nothing wired to its
    outputs is ignored, so a spare printer dropped into a tree doesn't take
    over. Walked by node_tree, the same rules as the G-code strings."""
    if obj is None:
        return None
    if is_printer(obj):
        return obj
    for mod in node_tree.live_modifiers(obj):
        inputs = mod.properties.inputs
        for item in mod.node_group.interface.items_tree:
            if (item.item_type == 'SOCKET' and item.in_out == 'INPUT'
                    and item.socket_type == 'NodeSocketObject'):
                socket = getattr(inputs, item.identifier, None)
                candidate = socket.value if socket is not None else None
                if is_printer(candidate):
                    return candidate
    for scope in node_tree.iter_scopes(obj):
        for node in scope.tree.nodes:
            if node.bl_idname != "GeometryNodeObjectInfo" or node.mute or not _is_connected(node):
                continue
            socket = node.inputs.get("Object")
            try:
                candidate = node_tree.resolve(socket, scope, node_tree.OBJECT_SOURCES,
                                              f"Object Info {node_tree.describe(scope, node)}")
            except ValueError:
                continue  # an object computed by other nodes can't be read
            if is_printer(candidate):
                return candidate
    return None


def printer_value(printer, attr_name):
    """The printer attribute's value at vertex 0, or None if it is missing."""
    mesh = overlay.evaluated_mesh(printer)
    if mesh is None:
        return None
    attr = mesh.attributes.get(attr_name)
    if attr is None:
        return None
    values = read_scalar_attribute(attr)
    return None if values is None else float(values[0])


def build_height(printer):
    """Top of the printer's build volume (mm): the highest point of its
    evaluated mesh in world space, which is the box Printer Dimensions
    builds from its Size. None when the mesh has no height (a printer that
    is a single settings vertex), so nothing is capped."""
    mesh = overlay.evaluated_mesh(printer)
    if mesh is None or not len(mesh.vertices):
        return None
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    matrix = np.array(printer.matrix_world)
    z = co.reshape(-1, 3) @ matrix[2, :3] + matrix[2, 3]
    top = float(z.max())
    return top if top > 0 else None


def export_settings(printer):
    """(line width mm, travel speed mm/s) from the printer's line_width and
    travel_speed. A missing or non-positive value stops the export: there is
    no hidden fallback to print with."""
    settings = []
    for attr_name, label in ((LINE_WIDTH, "Line Width"), (TRAVEL_SPEED, "Travel Speed")):
        value = printer_value(printer, attr_name) if printer else None
        where = f"'{printer.name}'" if printer else "no printer is linked"
        if value is None:
            raise ValueError(f"Printer has no {label}: no {attr_name} attribute on {where}")
        if not value > 0:  # also catches NaN
            raise ValueError(f"Printer has no {label}: {attr_name} on {where} is {value:g}")
        settings.append(value)
    return tuple(settings)
