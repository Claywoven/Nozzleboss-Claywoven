bl_info = {
    "name": "NozzleBoss (Claywoven Fork)",
    "description": "Exports Geometry Nodes tool paths to G-code",
    "author": "Jasper - Claywoven (based on Heinz Löpmeier's nozzleboss)",
    "version": (0, 1, 0),
    "blender": (5, 2, 0),   # the GN viewport overlay needs 5.2+
    "location": "3D View > nozzleboss",
    "category": "Import-Export"
}



# Dependency order: each module only imports the ones above it.
_MODULES = ("attributes", "colormaps", "draw_cache", "gcode_write", "node_tree", "gcode_source",
            "mesh_read", "overlay", "printer", "legacy", "settings", "export", "panel")

if "bpy" in locals():
    import importlib
    for _name in _MODULES:
        importlib.reload(globals()[_name])
else:
    from . import (attributes, colormaps, draw_cache, gcode_write, node_tree, gcode_source,
                   mesh_read, overlay, printer, legacy, settings, export, panel)

import bpy


# Registered in this order, unregistered in reverse: the panel's cache and
# settings first (the overlay and panel read scene.nozzleboss), the panel last.
_REGISTERED = (draw_cache, settings, overlay, export, panel)


def register():
    for module in _REGISTERED:
        module.register()


def unregister():
    for module in reversed(_REGISTERED):
        module.unregister()
