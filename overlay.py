"""Viewport gradient overlay for NozzleBoss float attributes.

Colours the mesh by the value of a float attribute (Flow_value, Speed_value,
Fan_value) using Blender's Solid "Attribute" shading rather than a custom draw
handler or a material.

Two paths:
  A. The attribute exists on obj.data - bake a BYTE_COLOR corner attribute here.
  B. The geometry comes out of Geometry Nodes - a display modifier appended
     after the user's own modifiers produces the colour, because writes to
     evaluated geometry are discarded.

Path B needs Blender 5.2+. On 5.0 the GN colour renders black and on 5.1 it is
ignored entirely; both were verified by render test, and the documented
loose-geometry workaround does not fix either.
"""

import re

import bpy
import numpy as np
from bpy.types import Operator
from bpy.props import StringProperty

from . import colormaps
from .utils import read_boolean_attribute, read_scalar_attribute


OVERLAY_ATTR = "NB_Overlay"
NODE_GROUP = "NB_OverlayNodes"
# Bumped whenever the node layout changes, so a group left over in a saved
# file from an older version of the add-on is rebuilt rather than trusted.
NODE_GROUP_VERSION = 2
MODIFIER = "NB_OverlayDisplay"

# Inferno bottoms out near black, so the lowest values would vanish against the
# viewport background. Start a little way up the ramp instead.
RAMP_FLOOR = 0.08

# Path B renders correctly only from this version on.
GN_PATH_MIN_VERSION = (5, 2, 0)

COLORMAP = 'INFERNO'

# Each overlay uses a fixed range so colours mean the same thing from one
# object to the next. The range is only adjustable on the Map Range node
# inside the display node group.
# (attribute name, panel label, (range min, range max), integer valued)
ATTRIBUTES = (
    ("Flow_value", "Flow Multiplier:", (0.0, 3.0), False),
    ("Speed_value", "Speed Multiplier:", (0.0, 1.0), False),
    ("Fan_value", "Fan Speed:", (0.0, 255.0), True),
)
RANGES = {name: bounds for name, _, bounds, _ in ATTRIBUTES}
INTEGER_ATTRIBUTES = {name for name, _, _, integer in ATTRIBUTES if integer}

# Masked overlays are discovered on the mesh rather than listed here. The node
# tree emits "<Name>_value_pNN" (NN orders them for export and in the panel)
# together with a "<Name>_mask" boolean selecting the faces the value applies
# to. Only selected faces are coloured, and the colour range is fitted to
# their values instead of being fixed.
MASKED_VALUE_RE = re.compile(r"^(?P<base>.+)_value_p(?P<order>\d+)$")
MASK_SUFFIX = "_mask"

# Faces outside the mask are painted this colour.
UNMASKED_COLOUR = (1.0, 1.0, 1.0, 1.0)

_viewport_stash = {}
_active_colour_stash = {}


def gn_path_supported():
    return bpy.app.version >= GN_PATH_MIN_VERSION


def evaluated_mesh(obj):
    if not obj or obj.type != 'MESH':
        return None
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return obj.evaluated_get(depsgraph).data


def uses_geometry_nodes(obj, attr_name):
    """True when the attribute only exists after modifier evaluation."""
    return obj.data.attributes.get(attr_name) is None


# ------------------------------------------------------------ masked values


def masked_pair(attr_name):
    """(base name, mask name) for a "<base>_value_pNN" attribute, else None."""
    match = MASKED_VALUE_RE.match(attr_name)
    if match is None:
        return None
    base = match.group("base")
    return base, base + MASK_SUFFIX


def masked_attributes(mesh):
    """[(attribute name, base name)] for every masked value on the mesh,
    ordered by the _pNN suffix."""
    found = []
    for attr in mesh.attributes:
        match = MASKED_VALUE_RE.match(attr.name)
        if match is not None:
            found.append((int(match.group("order")), attr.name, match.group("base")))
    found.sort()
    return [(name, base) for _, name, base in found]


def attribute_status(mesh, attr_name):
    """((min, max), None) when the attribute can be reported, otherwise
    (None, reason). Masked attributes only count the faces their mask selects."""
    attr = mesh.attributes.get(attr_name) if mesh else None
    if attr is None:
        return None, "missing"
    values = read_scalar_attribute(attr)
    if values is None:
        return None, "missing"

    pair = masked_pair(attr_name)
    if pair is not None:
        mask = mesh.attributes.get(pair[1])
        selected = read_boolean_attribute(mask) if mask is not None else None
        if selected is None or len(selected) != len(values):
            return None, "no mask"
        values = values[selected]
        if len(values) == 0:
            return None, "empty mask"

    return (float(values.min()), float(values.max())), None


def is_integer_attribute(mesh, attr_name):
    if attr_name in INTEGER_ATTRIBUTES:
        return True
    attr = mesh.attributes.get(attr_name) if mesh else None
    return attr is not None and attr.data_type == 'INT'


# ---------------------------------------------------------------- node group


def _enabled_input(node, name):
    for socket in node.inputs:
        if socket.name == name and socket.enabled:
            return socket
    return node.inputs[name]


def _enabled_output(node, name):
    for socket in node.outputs:
        if socket.name == name and socket.enabled:
            return socket
    return node.outputs[name]


def ensure_node_group(colormap='INFERNO'):
    node_group = bpy.data.node_groups.get(NODE_GROUP)
    if node_group is not None and node_group.get("nb_version") == NODE_GROUP_VERSION:
        return node_group

    if node_group is None:
        node_group = bpy.data.node_groups.new(NODE_GROUP, 'GeometryNodeTree')
        interface = node_group.interface
        interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
        interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    else:
        node_group.nodes.clear()

    _build_nodes(node_group)
    node_group["nb_version"] = NODE_GROUP_VERSION
    apply_ramp(node_group, colormap)
    return node_group


# Node names used to find the nodes again when reconfiguring the group.
N_VALUE = "NB Value"
N_MASK = "NB Mask"
N_SELECT = "NB Selected"
N_STAT = "NB Range"
N_RANGE_LOW = "NB From Min"
N_RANGE_HIGH = "NB From Max"
N_COLOUR = "NB Colour"


def _build_nodes(node_group):
    """Value -> Map Range -> Colour Ramp -> Store, with two switches:

    - the mask (a boolean attribute OR "no mask") selects which faces get the
      ramp colour; the rest are painted UNMASKED_COLOUR.
    - the Map Range bounds come either from constants (fixed-range overlays)
      or, for masked overlays, live from an Attribute Statistic over the
      selected faces, so the colours follow edits to the tree.
    """
    nodes = node_group.nodes
    links = node_group.links

    def new(bl_idname, name, location):
        node = nodes.new(bl_idname)
        node.name = node.label = name
        node.location = location
        return node

    group_in = new("NodeGroupInput", "Group Input", (-1200, 0))
    group_out = new("NodeGroupOutput", "Group Output", (600, 0))
    realize = new("GeometryNodeRealizeInstances", "Realize", (-960, 60))

    named = new("GeometryNodeInputNamedAttribute", N_VALUE, (-960, -200))
    named.data_type = 'FLOAT'
    mask = new("GeometryNodeInputNamedAttribute", N_MASK, (-960, -420))
    mask.data_type = 'BOOLEAN'
    select = new("FunctionNodeBooleanMath", N_SELECT, (-760, -420))
    select.operation = 'OR'

    stat = new("GeometryNodeAttributeStatistic", N_STAT, (-760, -60))
    stat.data_type = 'FLOAT'
    stat.domain = 'FACE'
    # A zero-width range would divide by zero inside Map Range.
    nudge = new("ShaderNodeMath", "Nudge", (-560, -60))
    nudge.operation = 'ADD'
    nudge.inputs[1].default_value = 1e-6
    widen = new("ShaderNodeMath", "Widen", (-380, -60))
    widen.operation = 'MAXIMUM'

    low = new("GeometryNodeSwitch", N_RANGE_LOW, (-380, -260))
    low.input_type = 'FLOAT'
    high = new("GeometryNodeSwitch", N_RANGE_HIGH, (-200, -260))
    high.input_type = 'FLOAT'

    map_range = new("ShaderNodeMapRange", "Map Range", (-20, -200))
    map_range.clamp = True
    ramp = new("ShaderNodeValToRGB", "Colour Ramp", (160, -200))
    colour = new("GeometryNodeSwitch", N_COLOUR, (400, -200))
    colour.input_type = 'RGBA'
    store = new("GeometryNodeStoreNamedAttribute", "Store", (400, 0))
    store.data_type = 'FLOAT_COLOR'
    store.domain = 'CORNER'
    store.inputs["Name"].default_value = OVERLAY_ATTR

    links.new(group_in.outputs["Geometry"], realize.inputs["Geometry"])
    links.new(realize.outputs["Geometry"], store.inputs["Geometry"])
    links.new(store.outputs["Geometry"], group_out.inputs[0])

    links.new(_enabled_output(mask, "Attribute"), select.inputs[0])

    links.new(realize.outputs["Geometry"], stat.inputs["Geometry"])
    links.new(select.outputs["Boolean"], stat.inputs["Selection"])
    links.new(_enabled_output(named, "Attribute"), _enabled_input(stat, "Attribute"))
    links.new(_enabled_output(stat, "Min"), nudge.inputs[0])
    links.new(_enabled_output(stat, "Max"), widen.inputs[0])
    links.new(nudge.outputs["Value"], widen.inputs[1])
    links.new(_enabled_output(stat, "Min"), _enabled_input(low, "True"))
    links.new(widen.outputs["Value"], _enabled_input(high, "True"))

    links.new(_enabled_output(named, "Attribute"), _enabled_input(map_range, "Value"))
    links.new(_enabled_output(low, "Output"), _enabled_input(map_range, "From Min"))
    links.new(_enabled_output(high, "Output"), _enabled_input(map_range, "From Max"))
    _enabled_input(map_range, "To Min").default_value = RAMP_FLOOR
    _enabled_input(map_range, "To Max").default_value = 1.0
    links.new(map_range.outputs["Result"], ramp.inputs["Fac"])

    links.new(select.outputs["Boolean"], _enabled_input(colour, "Switch"))
    _enabled_input(colour, "False").default_value = UNMASKED_COLOUR
    links.new(ramp.outputs["Color"], _enabled_input(colour, "True"))
    links.new(_enabled_output(colour, "Output"), _enabled_input(store, "Value"))


def configure_node_group(node_group, attr_name, low, high, colormap, mask_name=None):
    """Drive the overlay from node defaults rather than modifier inputs.

    A Named Attribute node whose Name comes from a group input socket reads as
    zero in Blender 5.2 even though the modifier reports the string correctly,
    so the names and range are written straight onto the nodes instead.

    With a mask the range constants are ignored: the switches take the live
    min/max of the selected faces instead.
    """
    nodes = node_group.nodes
    masked = mask_name is not None
    nodes[N_VALUE].inputs["Name"].default_value = attr_name
    nodes[N_MASK].inputs["Name"].default_value = mask_name or ""
    # OR-ing with True selects every face when there is no mask.
    nodes[N_SELECT].inputs[1].default_value = not masked
    if high - low < 1e-9:
        high = low + 1e-6
    for node, value in ((nodes[N_RANGE_LOW], low), (nodes[N_RANGE_HIGH], high)):
        _enabled_input(node, "Switch").default_value = masked
        _enabled_input(node, "False").default_value = value
    apply_ramp(node_group, colormap)


def apply_ramp(node_group, colormap):
    ramp = next((n for n in node_group.nodes if n.bl_idname == "ShaderNodeValToRGB"), None)
    if ramp is None:
        return
    stops = colormaps.ramp_stops(colormap)
    elements = ramp.color_ramp.elements
    while len(elements) > len(stops):
        elements.remove(elements[-1])
    for index, (position, colour) in enumerate(stops):
        if index < len(elements):
            element = elements[index]
            element.position = position
        else:
            element = elements.new(position)
        element.color = (*colour, 1.0)


# ---------------------------------------------------------------- path B


def add_display_modifier(obj, attr_name, low, high, colormap, mask_name=None):
    node_group = ensure_node_group(colormap)
    # modifiers.new() makes the new modifier active, which drags the node
    # editor away from whatever tree the user was working on.
    previous_active = obj.modifiers.active
    modifier = obj.modifiers.get(MODIFIER)
    if modifier is None:
        modifier = obj.modifiers.new(MODIFIER, 'NODES')
    modifier.node_group = node_group
    modifier.show_in_editmode = True
    modifier.show_render = False
    modifier.show_group_selector = False
    modifier.show_manage_panel = False
    if previous_active is not None and previous_active != modifier:
        obj.modifiers.active = previous_active
    update_display_modifier(obj, attr_name, low, high, colormap, mask_name)


def update_display_modifier(obj, attr_name, low, high, colormap, mask_name=None):
    modifier = obj.modifiers.get(MODIFIER)
    if modifier is None or modifier.node_group is None:
        return
    configure_node_group(modifier.node_group, attr_name, low, high, colormap, mask_name)
    obj.update_tag()


def remove_display_modifier(obj):
    modifier = obj.modifiers.get(MODIFIER)
    if modifier is None:
        return
    previous_active = obj.modifiers.active
    restore = previous_active if previous_active != modifier else None
    obj.modifiers.remove(modifier)
    if restore is not None:
        obj.modifiers.active = restore


# ---------------------------------------------------------------- path A


def bake_overlay(obj, attr_name, low, high, colormap, mask_name=None):
    mesh = obj.data
    source = mesh.attributes.get(attr_name)
    if source is None:
        return False

    values = read_scalar_attribute(source)
    if values is None:
        return False
    count = len(values)

    selected = None
    if mask_name is not None:
        mask = mesh.attributes.get(mask_name)
        selected = read_boolean_attribute(mask) if mask is not None else None
        if selected is None or len(selected) != count or not selected.any():
            return False
        low, high = float(values[selected].min()), float(values[selected].max())

    span = high - low
    if span > 1e-9:
        t = np.clip((values - low) / span, 0.0, 1.0)
    else:
        t = np.zeros(count, dtype=np.float64)
    t = RAMP_FLOOR + t * (1.0 - RAMP_FLOOR)

    lut = colormaps.build_lut(colormap)
    indices = np.clip((t * (len(lut) - 1)).round().astype(np.int32), 0, len(lut) - 1)
    colours = lut[indices]
    if selected is not None:
        colours[~selected] = UNMASKED_COLOUR[:3]

    if source.domain == 'FACE':
        loop_totals = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.polygons.foreach_get("loop_total", loop_totals)
        colours = np.repeat(colours, loop_totals, axis=0)
    elif source.domain == 'POINT':
        loop_verts = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", loop_verts)
        colours = colours[loop_verts]

    if len(colours) != len(mesh.loops):
        return False

    attribute = mesh.color_attributes.get(OVERLAY_ATTR)
    if attribute is not None and (attribute.data_type != 'BYTE_COLOR' or attribute.domain != 'CORNER'):
        mesh.color_attributes.remove(attribute)
        attribute = None
    if attribute is None:
        attribute = mesh.color_attributes.new(name=OVERLAY_ATTR, type='BYTE_COLOR', domain='CORNER')

    buffer = np.ones((len(mesh.loops), 4), dtype=np.float32)
    buffer[:, :3] = colours
    attribute.data.foreach_set("color_srgb", buffer.ravel())

    _active_colour_stash.setdefault(obj.name, mesh.attributes.active_color_name)
    mesh.attributes.active_color_name = OVERLAY_ATTR
    mesh.update()
    return True


def activate_overlay_colour(obj):
    """Make NB_Overlay the displayed colour attribute.

    Solid "Attribute" shading shows the evaluated mesh's active colour
    attribute, falling back to the default one. When the node tree passes the
    original geometry through, both names are inherited from the original mesh
    - so setting the active name there is honoured even though NB_Overlay
    itself only exists after evaluation. The previous name is stashed so
    toggling off restores the user's own paint layer.
    """
    mesh = obj.data
    _active_colour_stash.setdefault(obj.name, mesh.attributes.active_color_name)
    mesh.attributes.active_color_name = OVERLAY_ATTR
    obj.update_tag()


def overlay_is_displayed(obj):
    mesh = evaluated_mesh(obj)
    if mesh is None or mesh.color_attributes.get(OVERLAY_ATTR) is None:
        return False
    return OVERLAY_ATTR in (mesh.attributes.active_color_name, mesh.attributes.default_color_name)


def clear_baked_overlay(obj):
    mesh = obj.data
    attribute = mesh.color_attributes.get(OVERLAY_ATTR)
    if attribute is not None:
        mesh.color_attributes.remove(attribute)
    previous = _active_colour_stash.pop(obj.name, None)
    if previous is not None and previous != OVERLAY_ATTR:
        mesh.attributes.active_color_name = previous
    elif mesh.attributes.active_color_name == OVERLAY_ATTR:
        # Never leave the active name pointing at an attribute we just removed.
        mesh.attributes.active_color_name = ""


# ---------------------------------------------------------------- viewport


def _view3d_spaces():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                yield area.spaces.active


def stash_viewports():
    """Remember each viewport's shading the first time we touch it."""
    for space in _view3d_spaces():
        key = space.as_pointer()
        if key in _viewport_stash:
            continue
        shading = space.shading
        _viewport_stash[key] = {
            'type': shading.type,
            'color_type': shading.color_type,
            'light': shading.light,
            'show_cavity': shading.show_cavity,
        }


def apply_viewport_shading(show_cavity):
    """Force Solid + Attribute shading. Idempotent, so it also repairs a
    viewport the user has since switched back to another shading mode."""
    for space in _view3d_spaces():
        shading = space.shading
        shading.type = 'SOLID'
        shading.color_type = 'VERTEX'
        # Studio lighting multiplies the surface colour, which destroys the
        # colormap's perceptual uniformity - identical values would read as
        # different colours depending on face orientation.
        shading.light = 'FLAT'
        shading.show_cavity = show_cavity


def restore_viewports():
    for space in _view3d_spaces():
        saved = _viewport_stash.get(space.as_pointer())
        if saved is None:
            continue
        shading = space.shading
        shading.type = saved['type']
        shading.color_type = saved['color_type']
        shading.light = saved['light']
        shading.show_cavity = saved['show_cavity']
    _viewport_stash.clear()


def apply_cavity(show_cavity):
    for space in _view3d_spaces():
        space.shading.show_cavity = show_cavity


def overlay_is_live(obj):
    """Whether an overlay is actually present, as opposed to just named in the
    scene settings - overlay_attribute is saved in the .blend but the modifier
    and baked attribute are not always still there when the file reopens."""
    if obj is None or obj.type != 'MESH':
        return False
    return (obj.modifiers.get(MODIFIER) is not None
            or obj.data.color_attributes.get(OVERLAY_ATTR) is not None)


def is_showing(context, obj, attr_name):
    """True when this attribute's overlay is on and this object carries it.
    The overlay follows the active object, so on any other object the
    button reads as off even though the scene-level setting is on."""
    settings = context.scene.nozzleboss
    return (obj is not None
            and settings.overlay_attribute == attr_name
            and settings.overlay_object == obj.name
            and overlay_is_live(obj))


# ---------------------------------------------------------------- lifecycle


def _target_object(context):
    obj = context.object
    return obj if obj and obj.type == 'MESH' else None


def enable(context, obj, attr_name):
    settings = context.scene.nozzleboss
    low, high = RANGES.get(attr_name, (0.0, 1.0))
    pair = masked_pair(attr_name)
    mask_name = pair[1] if pair else None

    if uses_geometry_nodes(obj, attr_name):
        if not gn_path_supported():
            return False, (
                "Geometry Nodes overlay needs Blender %d.%d+ "
                "(this build ignores node-generated colour attributes)"
                % GN_PATH_MIN_VERSION[:2]
            )
        add_display_modifier(obj, attr_name, low, high, COLORMAP, mask_name)
        activate_overlay_colour(obj)
        if not overlay_is_displayed(obj):
            remove_display_modifier(obj)
            clear_baked_overlay(obj)
            return False, (
                "Cannot display the overlay: this object's node tree builds the mesh "
                "from scratch and already stores a colour attribute, which Blender "
                "keeps as the displayed one"
            )
    else:
        clear_baked_overlay(obj)
        if not bake_overlay(obj, attr_name, low, high, COLORMAP, mask_name):
            return False, "Could not read '%s' from the mesh" % attr_name

    stash_viewports()
    apply_viewport_shading(settings.overlay_cavity)
    settings.overlay_attribute = attr_name
    settings.overlay_object = obj.name
    return True, ""


def disable(context):
    """Turn the overlay off everywhere. Every mesh object is swept, not just
    the one named in the settings: the display modifiers all share one node
    group, so a modifier left on any object would follow the next overlay."""
    settings = context.scene.nozzleboss
    for target in bpy.data.objects:
        if target.type == 'MESH':
            remove_display_modifier(target)
            clear_baked_overlay(target)
    restore_viewports()
    settings.overlay_attribute = ""
    settings.overlay_object = ""


def cleanup_all():
    """Remove every trace of the overlay - used on unregister."""
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        modifier = obj.modifiers.get(MODIFIER)
        if modifier is not None:
            obj.modifiers.remove(modifier)
        attribute = obj.data.color_attributes.get(OVERLAY_ATTR)
        if attribute is not None:
            obj.data.color_attributes.remove(attribute)
    node_group = bpy.data.node_groups.get(NODE_GROUP)
    if node_group is not None:
        bpy.data.node_groups.remove(node_group)
    restore_viewports()
    _active_colour_stash.clear()


# ---------------------------------------------------------------- operators


class NOZZLEBOSS_OT_toggle_overlay(Operator):
    bl_idname = "nozzleboss.toggle_overlay"
    bl_label = ""
    bl_description = ("Preview this attribute on the mesh as an inferno gradient.\n"
                      "Note: solid shading colour mode is per-viewport, so this affects "
                      "every object in the viewport")
    bl_options = {'INTERNAL'}

    attribute: StringProperty()

    def execute(self, context):
        settings = context.scene.nozzleboss
        obj = _target_object(context)
        if obj is None:
            self.report({'WARNING'}, "Select a mesh object")
            return {'CANCELLED'}

        if is_showing(context, obj, self.attribute):
            disable(context)
            return {'FINISHED'}

        # Validate before tearing anything down, so clicking a missing
        # attribute does not kill a preview that is already running.
        _range, problem = attribute_status(evaluated_mesh(obj), self.attribute)
        if problem is not None:
            self.report({'WARNING'}, "'%s': %s on this object" % (self.attribute, problem))
            return {'CANCELLED'}

        # Same attribute on a different object, or a different attribute:
        # either way the overlay moves, so clear it everywhere first.
        if settings.overlay_attribute:
            disable(context)

        ok, message = enable(context, obj, self.attribute)
        if not ok:
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        return {'FINISHED'}


class NOZZLEBOSS_OT_overlay_off(Operator):
    bl_idname = "nozzleboss.overlay_off"
    bl_label = "Turn Off"
    bl_description = "Remove the NozzleBoss overlay and restore the viewport shading"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        disable(context)
        return {'FINISHED'}


# ---------------------------------------------------------------- shared UI


def draw_overlay_settings(layout, context):
    settings = context.scene.nozzleboss
    active = settings.overlay_attribute

    column = layout.column(align=True)
    if not active:
        column.label(text="NozzleBoss overlay: off")
        return

    pair = masked_pair(active)
    if pair is not None:
        detail = "masked by %s" % pair[1]
    else:
        low, high = RANGES.get(active, (0.0, 1.0))
        fmt = "%d - %d" if active in INTEGER_ATTRIBUTES else "%.2f - %.2f"
        detail = fmt % (low, high)
    column.label(text="NozzleBoss: %s on %s  (%s)" % (active, settings.overlay_object, detail))
    column.prop(settings, "overlay_cavity")
    column.operator("nozzleboss.overlay_off", icon='X')


def _overlay_popover(self, context):
    if context.scene.nozzleboss.overlay_attribute:
        draw_overlay_settings(self.layout, context)


classes = (
    NOZZLEBOSS_OT_toggle_overlay,
    NOZZLEBOSS_OT_overlay_off,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_PT_overlay_geometry.append(_overlay_popover)


def unregister():
    bpy.types.VIEW3D_PT_overlay_geometry.remove(_overlay_popover)
    cleanup_all()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
