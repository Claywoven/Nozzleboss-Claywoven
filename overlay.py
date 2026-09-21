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

import bpy
import numpy as np
from bpy.types import Operator
from bpy.props import StringProperty 

from . import colormaps
from .utils import get_attribute_range, read_scalar_attribute


OVERLAY_ATTR = "NB_Overlay"
NODE_GROUP = "NB_OverlayNodes"
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
    if node_group is not None:
        return node_group

    node_group = bpy.data.node_groups.new(NODE_GROUP, 'GeometryNodeTree')
    interface = node_group.interface
    interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')

    nodes = node_group.nodes
    links = node_group.links

    group_in = nodes.new("NodeGroupInput")
    group_out = nodes.new("NodeGroupOutput")
    realize = nodes.new("GeometryNodeRealizeInstances")
    named = nodes.new("GeometryNodeInputNamedAttribute")
    named.data_type = 'FLOAT'
    map_range = nodes.new("ShaderNodeMapRange")
    map_range.clamp = True
    ramp = nodes.new("ShaderNodeValToRGB")
    store = nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = 'FLOAT_COLOR'
    store.domain = 'CORNER'
    store.inputs["Name"].default_value = OVERLAY_ATTR

    group_in.location = (-800, 0)
    named.location = (-560, -180)
    map_range.location = (-380, -180)
    ramp.location = (-180, -180)
    realize.location = (-560, 60)
    store.location = (120, 0)
    group_out.location = (320, 0)

    links.new(group_in.outputs["Geometry"], realize.inputs["Geometry"])
    links.new(realize.outputs["Geometry"], store.inputs["Geometry"])
    links.new(_enabled_output(named, "Attribute"), _enabled_input(map_range, "Value"))
    _enabled_input(map_range, "To Min").default_value = RAMP_FLOOR
    _enabled_input(map_range, "To Max").default_value = 1.0
    links.new(map_range.outputs["Result"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], _enabled_input(store, "Value"))
    links.new(store.outputs["Geometry"], group_out.inputs[0])

    apply_ramp(node_group, colormap)
    return node_group


def _overlay_nodes(node_group):
    def find(bl_idname):
        return next(n for n in node_group.nodes if n.bl_idname == bl_idname)
    return (find("GeometryNodeInputNamedAttribute"),
            find("ShaderNodeMapRange"),
            find("GeometryNodeStoreNamedAttribute"))


def configure_node_group(node_group, attr_name, low, high, colormap):
    """Drive the overlay from node defaults rather than modifier inputs.

    A Named Attribute node whose Name comes from a group input socket reads as
    zero in Blender 5.2 even though the modifier reports the string correctly,
    so the name and range are written straight onto the nodes instead.
    """
    named, map_range, store = _overlay_nodes(node_group)
    named.inputs["Name"].default_value = attr_name
    store.inputs["Name"].default_value = OVERLAY_ATTR
    if high - low < 1e-9:
        high = low + 1e-6
    _enabled_input(map_range, "From Min").default_value = low
    _enabled_input(map_range, "From Max").default_value = high
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


def add_display_modifier(obj, attr_name, low, high, colormap):
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
    update_display_modifier(obj, attr_name, low, high, colormap)


def update_display_modifier(obj, attr_name, low, high, colormap):
    modifier = obj.modifiers.get(MODIFIER)
    if modifier is None or modifier.node_group is None:
        return
    configure_node_group(modifier.node_group, attr_name, low, high, colormap)
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


def bake_overlay(obj, attr_name, low, high, colormap):
    mesh = obj.data
    source = mesh.attributes.get(attr_name)
    if source is None:
        return False

    values = read_scalar_attribute(source)
    if values is None:
        return False
    count = len(values)

    span = high - low
    if span > 1e-9:
        t = np.clip((values - low) / span, 0.0, 1.0)
    else:
        t = np.zeros(count, dtype=np.float64)
    t = RAMP_FLOOR + t * (1.0 - RAMP_FLOOR)

    lut = colormaps.build_lut(colormap)
    indices = np.clip((t * (len(lut) - 1)).round().astype(np.int32), 0, len(lut) - 1)
    colours = lut[indices]

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


# ---------------------------------------------------------------- lifecycle


def _target_object(context):
    obj = context.object
    return obj if obj and obj.type == 'MESH' else None


def enable(context, obj, attr_name):
    settings = context.scene.nozzleboss
    low, high = RANGES.get(attr_name, (0.0, 1.0))

    if uses_geometry_nodes(obj, attr_name):
        if not gn_path_supported():
            return False, (
                "Geometry Nodes overlay needs Blender %d.%d+ "
                "(this build ignores node-generated colour attributes)"
                % GN_PATH_MIN_VERSION[:2]
            )
        add_display_modifier(obj, attr_name, low, high, COLORMAP)
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
        if not bake_overlay(obj, attr_name, low, high, COLORMAP):
            return False, "Could not read '%s' from the mesh" % attr_name

    stash_viewports()
    apply_viewport_shading(settings.overlay_cavity)
    settings.overlay_attribute = attr_name
    return True, ""


def disable(context, obj=None):
    settings = context.scene.nozzleboss
    targets = [obj] if obj else [o for o in bpy.data.objects if o.type == 'MESH']
    for target in targets:
        if target is None:
            continue
        remove_display_modifier(target)
        clear_baked_overlay(target)
    restore_viewports()
    settings.overlay_attribute = ""


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

        if settings.overlay_attribute == self.attribute and overlay_is_live(obj):
            disable(context, obj)
            return {'FINISHED'}

        # Validate before tearing anything down, so clicking a missing
        # attribute does not kill a preview that is already running.
        mesh = evaluated_mesh(obj)
        found = get_attribute_range(mesh, self.attribute) if mesh else None
        if found is None:
            self.report({'WARNING'}, "'%s' not found on this object" % self.attribute)
            return {'CANCELLED'}

        if settings.overlay_attribute:
            disable(context, obj)

        ok, message = enable(context, obj, self.attribute)
        if not ok:
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        return {'FINISHED'}


# ---------------------------------------------------------------- shared UI


def draw_overlay_settings(layout, context):
    settings = context.scene.nozzleboss
    active = settings.overlay_attribute

    column = layout.column(align=True)
    if not active:
        column.label(text="NozzleBoss overlay: off")
        return

    low, high = RANGES.get(active, (0.0, 1.0))
    fmt = "%d - %d" if active in INTEGER_ATTRIBUTES else "%.2f - %.2f"
    column.label(text="NozzleBoss: %s  (%s)" % (active, fmt % (low, high)))
    column.prop(settings, "overlay_cavity")
    row = column.row()
    row.operator("nozzleboss.toggle_overlay", text="Turn Off", icon='X').attribute = active


def _overlay_popover(self, context):
    if context.scene.nozzleboss.overlay_attribute:
        draw_overlay_settings(self.layout, context)


classes = (
    NOZZLEBOSS_OT_toggle_overlay,
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
