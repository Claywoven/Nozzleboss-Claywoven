"""Every name the exporter shares with the node assets or leaves in a .blend
(Design/REGISTRY.md, rule 6). No other module spells these out."""

import re
from collections import namedtuple


# Mesh attributes the Geometry Nodes build writes for the exporter
TOOL_PATH = "tool_path"            # EDGE bool: top edge of the ribbon, the nozzle path
USE_FOR_HEIGHT = "use_for_height"  # EDGE bool: vertical edge measuring layer height
FACE_NUMBER = "face_number"        # FACE int from 1: face k is the move from vert k-1 to vert k
FLOW_VALUE = "flow_value"          # FACE float: flow multiplier
SPEED_VALUE = "speed_value"        # FACE float: speed in mm/s
FAN_VALUE = "fan_value"            # FACE int: fan speed 0-255

CONTINUOUS_VALUES = (FLOW_VALUE, SPEED_VALUE, FAN_VALUE)

# G-code snippet staging groups: a FACE bool "<label>_mask_pNN" selects the
# faces (NN orders the stages for export and in the panel), and its values are
# "<label>_value" (the {<label>} placeholder) and "<label>_value_<name>" ({<name>}).
MASK_RE = re.compile(r"^(?P<label>.+)_mask_p(?P<order>\d+)$")
VALUE_SUFFIX = "_value"
NAME_INFIXES = ("_mask", "_value")  # a label containing these is ambiguous

# The printer object: marked by a custom property, settings read at vertex 0
PRINTER_PROP = "cw_prop"           # on the object or its mesh
PRINTER_TAG = "printer"            # its value
# cw_prop on the helper objects the build uses, which are never exported
WRAPPER_TAG = "wrapper"            # the wrapper mesh the path is shaped on
PROFILE_TAG = "profile"            # the profile curve
HELPER_TAGS = (WRAPPER_TAG, PROFILE_TAG)
LINE_WIDTH = "line_width"          # POINT float, mm
PRINT_SPEED = "print_speed"        # POINT int, mm/s
TRAVEL_SPEED = "travel_speed"      # POINT int, mm/s

# Combine Bundle inputs read from String nodes
G_CODE_STRING = "G_code_string"    # a stage's snippet
LABEL = "Label"                    # its label, beside G_code_string
START_G_CODE = "Start_G_code"      # on the printer
END_G_CODE = "End_G_code"          # on the printer
RETRACT_G_CODE = "Retract_G_code"      # on the printer, before a long travel
UNRETRACT_G_CODE = "Unretract_G_code"  # on the printer, after it
# The printer modifier tree's Bundle output; the printer's strings count only
# when their Combine Bundle is wired to it
EXPORT_SETTINGS = "Export Settings"

# LEGACY - the colour-attribute files: corner colours painted per segment and
# the text blocks holding their G-code. Remove with legacy.py.
LEGACY_FLOW = "Flow"
LEGACY_SPEED = "Speed"
LEGACY_TOOL = "Tool"
LEGACY_FAN = "Fan"
LEGACY_COLOURS = (LEGACY_FLOW, LEGACY_SPEED, LEGACY_TOOL, LEGACY_FAN)
LEGACY_START_TEXT = "Start"
LEGACY_END_TEXT = "End"
LEGACY_TOOL_TEXTS = ("T0", "T1")   # T0 where the Tool colour is light, T1 where it is dark

# Data the overlay generates (removed on toggle-off and unregister)
OVERLAY_ATTR = "NB_Overlay"
OVERLAY_NODE_GROUP = "NB_OverlayNodes"
OVERLAY_MODIFIER = "NB_OverlayDisplay"
# What to restore when the overlay turns off, saved with the file so it
# survives reopening: on the mesh, its previous active colour attribute; on
# the screen, {area index: shading} for each 3D viewport the overlay changed.
OVERLAY_PREVIOUS_COLOUR = "NB_active_colour"
OVERLAY_PREVIOUS_SHADING = "NB_shading"


# One staging group's attributes on a mesh. values is ((placeholder, attribute), ...).
Stage = namedtuple("Stage", "label order mask values")


def masked_stages(mesh):
    """A Stage for each "<label>_mask_pNN" attribute on the mesh, in _pNN
    order (then label), with every "<label>_value" and "<label>_value_<name>"
    attribute as its values."""
    names = [attr.name for attr in mesh.attributes]
    stages = []
    for name in names:
        match = MASK_RE.match(name)
        if match is None:
            continue
        label = match.group("label")
        main = label + VALUE_SUFFIX
        values = [(label if other == main else other[len(main) + 1:], other)
                  for other in names if other == main or other.startswith(main + "_")]
        stages.append(Stage(label, int(match.group("order")), name, tuple(sorted(values))))
    stages.sort(key=lambda stage: (stage.order, stage.label))
    return stages


def mask_of_value(mesh, attr_name):
    """The mask selecting a staging group's value attribute, or None for any
    other attribute."""
    if mesh is None or attr_name in CONTINUOUS_VALUES:
        return None
    return next((stage.mask for stage in masked_stages(mesh)
                 if any(attr == attr_name for _, attr in stage.values)), None)
