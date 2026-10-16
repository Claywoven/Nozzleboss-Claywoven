"""Viewport gradient overlay for NozzleBoss float attributes.

Colours the mesh by the value of a float attribute (flow_value, speed_value,
fan_value) using Blender's Solid "Attribute" shading rather than a custom draw
handler or a material.

A display modifier appended after the user's own modifiers produces the
colour from the evaluated mesh, so the preview always shows what the panel
and the export read (writes to evaluated geometry are discarded, so the
colour can't be baked there). This is the only path: an earlier bake onto
obj.data (Path A) could show values a modifier had since changed.

The display modifier needs Blender 5.2+, which bl_info requires (on 5.0 and
5.1 the node-generated colour attribute renders black or is ignored).

What to restore on toggle-off (each viewport's shading, each mesh's active
colour attribute) is kept in the file as NB_ custom properties on the screen
and the mesh, so an overlay saved on reopens on and still turns off cleanly.
A load_post handler turns off an overlay that is no longer live, and
undo_post/redo_post bring the viewport shading in line with what undo
restored (shading is UI state, which undo doesn't touch).
"""

import bpy
import numpy as np
from bpy.app.handlers import persistent
from bpy.types import Operator, PropertyGroup
from bpy.props import FloatVectorProperty, PointerProperty, StringProperty

from . import colormaps, draw_cache
from .attributes import FAN_VALUE, FLOW_VALUE, SPEED_VALUE, mask_of_value
from . import attributes
from .mesh_read import read_boolean_attribute, read_scalar_attribute


OVERLAY_ATTR = attributes.OVERLAY_ATTR
NODE_GROUP = attributes.OVERLAY_NODE_GROUP
# Bumped whenever the node layout changes, so a group left over in a saved
# file from an older version of the add-on is rebuilt rather than trusted.
NODE_GROUP_VERSION = 4
MODIFIER = attributes.OVERLAY_MODIFIER

# Inferno bottoms out near black, so the lowest values would vanish against the
# viewport background. Start a little way up the ramp instead.
RAMP_FLOOR = 0.08

COLORMAP = 'INFERNO'

# Most overlays use a fixed range so colours mean the same thing from one
# object to the next. The range is only adjustable on the Map Range node
# inside the display node group. Either end may be None, which fits that end
# to the attribute's own min or max instead. Speed is anchored at 0 so the
# rest of the object keeps its colour while the first layer speed changes.
# (attribute name, panel label, (range min, range max), integer valued)
ATTRIBUTES = (
    (FLOW_VALUE, "Flow Multiplier:", (0.0, 3.0), False),
    (SPEED_VALUE, "Speed Range:", (0.0, None), False),
    (FAN_VALUE, "Fan Speed:", (0.0, 255.0), True),
)
RANGES = {name: bounds for name, _, bounds, _ in ATTRIBUTES}
INTEGER_ATTRIBUTES = {name for name, _, _, integer in ATTRIBUTES if integer}

# Masked overlays (staging group values, attributes.masked_stages) are discovered on the mesh
# rather than listed here. Only the faces their mask selects are coloured, and
# the colour range is fitted to their values instead of being fixed.

# Faces outside the mask are painted this colour.
UNMASKED_COLOUR = (1.0, 1.0, 1.0, 1.0)

# The panel shows the colormap as a row of read-only colour swatches
# (Blender has no gradient widget), sampled at the centre of each step.
SWATCH_COUNT = 16
SWATCH_COLOURS = tuple(
    tuple(rgb) for rgb in colormaps.build_lut(COLORMAP, 256)[
        np.round((np.arange(SWATCH_COUNT) + 0.5) / SWATCH_COUNT * 255).astype(int)].tolist())

# What restore_viewports() puts back where a viewport shows overlay shading
# but nothing was stashed for it (a file saved before the stash was kept in
# the file): Blender's own defaults.
DEFAULT_SHADING = {'type': 'SOLID', 'color_type': 'MATERIAL', 'light': 'STUDIO',
                   'show_cavity': False}


def evaluated_mesh(obj, depsgraph=None):
    """obj's mesh after modifiers. Pass the depsgraph when reading several
    objects, so it is fetched once."""
    if not obj or obj.type != 'MESH':
        return None
    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()
    return obj.evaluated_get(depsgraph).data


# ------------------------------------------------------------ masked values


def attribute_status(mesh, attr_name):
    """((min, max), None) when the attribute can be reported, otherwise
    (None, reason). Masked attributes only count the faces their mask selects."""
    attr = mesh.attributes.get(attr_name) if mesh else None
    if attr is None:
        return None, "missing"
    values = read_scalar_attribute(attr)
    if values is None:
        return None, "missing"

    mask_name = mask_of_value(mesh, attr_name)
    if mask_name is not None:
        mask = mesh.attributes.get(mask_name)
        selected = read_boolean_attribute(mask) if mask is not None else None
        if selected is None or len(selected) != len(values):
            return None, "no mask"
        values = values[selected]
        if len(values) == 0:
            return None, "empty mask"

    return (float(values.min()), float(values.max())), None


def combined_status(named_meshes, attr_name):
    """attribute_status over several objects, given as (name, mesh) pairs.
    A plain attribute must be on every mesh. A masked value only counts the
    meshes that have it, since a stage may be on some objects only."""
    masked = attr_name not in RANGES
    lows, highs = [], []
    problem = None
    for name, mesh in named_meshes:
        if masked and (mesh is None or mesh.attributes.get(attr_name) is None):
            continue
        minmax, reason = attribute_status(mesh, attr_name)
        if reason is not None:
            if not masked:
                return None, f"{reason} on {name}"
            problem = problem or f"{reason} on {name}"
            continue
        lows.append(minmax[0])
        highs.append(minmax[1])
    if not lows:
        return None, problem or "missing"
    return (min(lows), max(highs)), None


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
    - each Map Range bound comes either from a constant (a fixed end) or, for
      masked overlays and fitted ends, live from an Attribute Statistic over
      the selected faces, so the colours follow edits to the tree.
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

    With a mask, both range constants are ignored. Otherwise an end given as
    None is fitted: its switch takes the live min or max of the faces instead.
    """
    nodes = node_group.nodes
    masked = mask_name is not None
    fit_low = masked or low is None
    fit_high = masked or high is None
    nodes[N_VALUE].inputs["Name"].default_value = attr_name
    nodes[N_MASK].inputs["Name"].default_value = mask_name or ""
    # OR-ing with True selects every face when there is no mask.
    nodes[N_SELECT].inputs[1].default_value = not masked
    low = 0.0 if low is None else low
    high = 1.0 if high is None else high
    if not (fit_low or fit_high) and high - low < 1e-9:
        high = low + 1e-6
    for node, value, fitted in ((nodes[N_RANGE_LOW], low, fit_low),
                                (nodes[N_RANGE_HIGH], high, fit_high)):
        _enabled_input(node, "Switch").default_value = fitted
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


# ---------------------------------------------------------------- display modifier


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
    _stash_active_colour(mesh)
    mesh.attributes.active_color_name = OVERLAY_ATTR
    obj.update_tag()


def overlay_is_displayed(obj):
    mesh = evaluated_mesh(obj)
    if mesh is None or mesh.color_attributes.get(OVERLAY_ATTR) is None:
        return False
    return OVERLAY_ATTR in (mesh.attributes.active_color_name, mesh.attributes.default_color_name)


def _stash_active_colour(mesh):
    """Remember the mesh's active colour attribute the first time the overlay
    takes it over. Kept on the mesh, so it is saved with the file."""
    if attributes.OVERLAY_PREVIOUS_COLOUR not in mesh:
        mesh[attributes.OVERLAY_PREVIOUS_COLOUR] = mesh.attributes.active_color_name


def clear_overlay_colour(obj):
    """Give the mesh back its previous active colour attribute. Also removes
    an NB_Overlay colour attribute baked onto the mesh by older versions
    (Path A), so files saved with one are cleaned up."""
    mesh = obj.data
    attribute = mesh.color_attributes.get(OVERLAY_ATTR)
    if attribute is not None:
        mesh.color_attributes.remove(attribute)
    previous = mesh.pop(attributes.OVERLAY_PREVIOUS_COLOUR, None)
    if previous is not None and previous != OVERLAY_ATTR:
        mesh.attributes.active_color_name = previous
    elif mesh.attributes.active_color_name == OVERLAY_ATTR:
        # Never leave the active name pointing at an attribute we just
        # removed. Nothing stashed (a file saved before the stash was kept
        # in the file): fall back to the mesh's default colour attribute.
        default = mesh.attributes.default_color_name
        mesh.attributes.active_color_name = default if default != OVERLAY_ATTR else ""


# ---------------------------------------------------------------- viewport


def _view3d_areas():
    """(screen, area index as a string, space) for every 3D viewport."""
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        for index, area in enumerate(screen.areas):
            if area.type == 'VIEW_3D':
                yield screen, str(index), area.spaces.active


def _view3d_spaces():
    for _screen, _key, space in _view3d_areas():
        yield space


def stash_viewports():
    """Remember each viewport's shading the first time we touch it. Kept on
    the screen, so it is saved with the file."""
    for screen, key, space in _view3d_areas():
        stash = screen.get(attributes.OVERLAY_PREVIOUS_SHADING)
        if stash is not None and key in stash:
            continue
        shading = space.shading
        saved = {
            'type': shading.type,
            'color_type': shading.color_type,
            'light': shading.light,
            'show_cavity': shading.show_cavity,
        }
        if stash is None:
            screen[attributes.OVERLAY_PREVIOUS_SHADING] = {key: saved}
        else:
            stash[key] = saved


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


def _shows_overlay_shading(shading):
    return shading.type == 'SOLID' and shading.color_type == 'VERTEX' and shading.light == 'FLAT'


def restore_viewports(fallback=False):
    """Put back each viewport's stashed shading. With fallback (the overlay
    was on), a viewport still in overlay shading with nothing stashed gets
    DEFAULT_SHADING rather than staying flat."""
    for screen, key, space in _view3d_areas():
        stash = screen.get(attributes.OVERLAY_PREVIOUS_SHADING)
        saved = stash.get(key) if stash is not None else None
        shading = space.shading
        if saved is None:
            if not (fallback and _shows_overlay_shading(shading)):
                continue
            saved = DEFAULT_SHADING
        shading.type = saved['type']
        shading.color_type = saved['color_type']
        shading.light = saved['light']
        shading.show_cavity = bool(saved['show_cavity'])
    for screen in bpy.data.screens:
        screen.pop(attributes.OVERLAY_PREVIOUS_SHADING, None)


def apply_cavity(show_cavity):
    for space in _view3d_spaces():
        space.shading.show_cavity = show_cavity


def overlay_is_live(obj):
    """Whether an overlay is actually present, as opposed to just named in the
    scene settings - overlay_attribute is saved in the .blend but the modifier
    is not always still there when the file reopens."""
    if obj is None or obj.type != 'MESH':
        return False
    return obj.modifiers.get(MODIFIER) is not None


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
    low, high = RANGES.get(attr_name) or (None, None)
    mask_name = mask_of_value(evaluated_mesh(obj), attr_name)

    clear_overlay_colour(obj)  # an older version's bake
    add_display_modifier(obj, attr_name, low, high, COLORMAP, mask_name)
    activate_overlay_colour(obj)
    if not overlay_is_displayed(obj):
        remove_display_modifier(obj)
        clear_overlay_colour(obj)
        return False, (
            "Cannot display the overlay: this object's node tree builds the mesh "
            "from scratch and already stores a colour attribute, which Blender "
            "keeps as the displayed one"
        )

    stash_viewports()
    apply_viewport_shading(settings.overlay_cavity)
    settings.overlay_attribute = attr_name
    settings.overlay_object = obj.name
    return True, ""


def _local_meshes():
    """Mesh objects the overlay may change: not linked from a library,
    whose data can't be edited."""
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and obj.library is None and obj.data.library is None:
            yield obj


def disable(context):
    """Turn the overlay off everywhere. Every mesh object is swept, not just
    the one named in the settings: the display modifiers all share one node
    group, so a modifier left on any object would follow the next overlay."""
    settings = context.scene.nozzleboss
    _turn_off(settings, fallback=bool(settings.overlay_attribute))


def _turn_off(settings, fallback):
    """Remove the overlay from every mesh, restore the viewports (see
    restore_viewports for fallback) and clear the settings."""
    for target in _local_meshes():
        remove_display_modifier(target)
        clear_overlay_colour(target)
    restore_viewports(fallback=fallback)
    settings.overlay_attribute = ""
    settings.overlay_object = ""


def _has_overlay_remnants():
    return (any(target.modifiers.get(MODIFIER) is not None
                or target.data.color_attributes.get(OVERLAY_ATTR) is not None
                or target.data.attributes.active_color_name == OVERLAY_ATTR
                for target in _local_meshes())
            or any(attributes.OVERLAY_PREVIOUS_SHADING in screen for screen in bpy.data.screens))


def _live_settings():
    """The settings of a scene whose overlay is actually present, or None."""
    for scene in bpy.data.scenes:
        settings = getattr(scene, "nozzleboss", None)
        if (settings is not None and settings.overlay_attribute
                and overlay_is_live(bpy.data.objects.get(settings.overlay_object))):
            return settings
    return None


def _tear_down_stale():
    """Turn off settings naming an overlay that isn't there, and overlay
    data or stashes the settings don't know about."""
    scenes = [scene for scene in bpy.data.scenes if hasattr(scene, "nozzleboss")]
    stale = [scene.nozzleboss for scene in scenes if scene.nozzleboss.overlay_attribute]
    if scenes and (stale or _has_overlay_remnants()):
        for settings in stale:
            settings.overlay_attribute = ""
            settings.overlay_object = ""
        _turn_off(scenes[0].nozzleboss, fallback=True)


@persistent
def _repair_after_load(_file):
    """A live overlay reopens as it was saved: what to restore is in the
    file. Anything stale is turned off. Only tears state down - it never
    fits ranges."""
    if _live_settings() is None:
        _tear_down_stale()


@persistent
def _sync_after_undo(*_args):
    """Undo and redo restore the overlay's data and settings but not the
    viewport shading, which lives in the UI. Bring the shading in line:
    an overlay undone away is turned off and its shading restored; one
    brought back (undoing Turn Off, redoing a toggle) whose viewports were
    already restored gets its shading again. A viewport still under the
    overlay (its stash is there) is left as the user set it. Never fits
    ranges: the range is in the restored node group."""
    settings = _live_settings()
    if settings is None:
        _tear_down_stale()
    elif not any(attributes.OVERLAY_PREVIOUS_SHADING in screen for screen in bpy.data.screens):
        stash_viewports()
        apply_viewport_shading(settings.overlay_cavity)


def cleanup_all():
    """Remove every trace of the overlay - used on unregister."""
    was_on = any(getattr(scene, "nozzleboss", None) is not None and scene.nozzleboss.overlay_attribute
                 for scene in bpy.data.scenes)
    for obj in _local_meshes():
        modifier = obj.modifiers.get(MODIFIER)
        if modifier is not None:
            obj.modifiers.remove(modifier)
        clear_overlay_colour(obj)
    node_group = bpy.data.node_groups.get(NODE_GROUP)
    if node_group is not None:
        bpy.data.node_groups.remove(node_group)
    restore_viewports(fallback=was_on)


# ---------------------------------------------------------------- operators


class NOZZLEBOSS_OT_toggle_overlay(Operator):
    bl_idname = "nozzleboss.toggle_overlay"
    bl_label = "NozzleBoss Overlay"
    bl_description = ("Preview this attribute on the mesh as an inferno gradient.\n"
                      "Note: solid shading colour mode is per-viewport, so this affects "
                      "every object in the viewport")
    # UNDO: Ctrl+Z undoes the toggle itself; _sync_after_undo fixes the shading.
    bl_options = {'INTERNAL', 'UNDO'}

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
    # UNDO: Ctrl+Z undoes the toggle itself; _sync_after_undo fixes the shading.
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        disable(context)
        return {'FINISHED'}


# ---------------------------------------------------------------- shared UI


def overlay_range(obj, attr_name):
    """(low, high, integer valued) the overlay maps the colormap to. Fixed
    ends come from RANGES. Fitted ends (masked overlays, None in RANGES) are
    the min / max of the selected faces, as the node group fits them; None
    when they can't be read."""
    low, high = RANGES.get(attr_name) or (None, None)
    mesh = evaluated_mesh(obj)
    if low is None or high is None:
        minmax, _problem = attribute_status(mesh, attr_name)
        if minmax is not None:
            low = minmax[0] if low is None else low
            high = minmax[1] if high is None else high
    return low, high, is_integer_attribute(mesh, attr_name)


def draw_overlay_settings(layout, context):
    settings = context.scene.nozzleboss
    active = settings.overlay_attribute

    column = layout.column(align=True)
    if not active:
        column.label(text="NozzleBoss overlay: off")
        return

    low, high, integer = draw_cache.get(
        ("overlay_range", settings.overlay_object, active),
        lambda: overlay_range(bpy.data.objects.get(settings.overlay_object), active))
    fmt = "%d" if integer else "%.2f"
    column.label(text="Overlay Range:")
    ends = column.split(factor=0.5)
    ends.label(text="?" if low is None else fmt % low)
    right = ends.row()
    right.alignment = 'RIGHT'
    right.label(text="?" if high is None else fmt % high)
    draw_gradient(column, context)
    column.prop(settings, "overlay_cavity")
    # Same gap as between this button and the panel's export button
    column.separator(factor=2)
    column.operator("nozzleboss.overlay_off", icon='X')


def _swatch_getter(k):
    return lambda self: SWATCH_COLOURS[k]


class NOZZLEBOSS_PG_swatches(PropertyGroup):
    """One colour per swatch. Colour buttons fill their width, so the row
    stretches with the panel. COLOR_GAMMA shows the sRGB LUT as it is. No
    set, so they are read-only (if Blender draws them dimmed, add
    set=lambda self, value: None: they stay editable but snap back)."""
    __annotations__ = {
        f"c{k}": FloatVectorProperty(name="", description="Overlay colormap (read-only)",
                                     subtype='COLOR_GAMMA', size=3, get=_swatch_getter(k))
        for k in range(SWATCH_COUNT)
    }


def draw_gradient(layout, context):
    """The colormap, low to high, as a read-only row of swatches."""
    swatches = context.window_manager.nb_swatches
    row = layout.row(align=True)
    for k in range(SWATCH_COUNT):
        row.prop(swatches, f"c{k}", text="")


def _overlay_popover(self, context):
    if context.scene.nozzleboss.overlay_attribute:
        draw_overlay_settings(self.layout, context)


classes = (
    NOZZLEBOSS_PG_swatches,
    NOZZLEBOSS_OT_toggle_overlay,
    NOZZLEBOSS_OT_overlay_off,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_PT_overlay_geometry.append(_overlay_popover)
    bpy.types.WindowManager.nb_swatches = PointerProperty(type=NOZZLEBOSS_PG_swatches)
    bpy.app.handlers.load_post.append(_repair_after_load)
    bpy.app.handlers.undo_post.append(_sync_after_undo)
    bpy.app.handlers.redo_post.append(_sync_after_undo)


def unregister():
    if _repair_after_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_repair_after_load)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _sync_after_undo in handlers:
            handlers.remove(_sync_after_undo)
    bpy.types.VIEW3D_PT_overlay_geometry.remove(_overlay_popover)
    del bpy.types.WindowManager.nb_swatches
    cleanup_all()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
