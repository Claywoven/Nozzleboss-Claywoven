"""The add-on's settings, stored on the scene as scene.nozzleboss."""

import bpy
from bpy.props import (BoolProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty,
                       StringProperty)

from . import overlay


def _overlay_cavity_update(self, context):
    overlay.apply_cavity(self.overlay_cavity)


class gcode_settings(bpy.types.PropertyGroup):

    # The viewport overlay
    overlay_attribute: StringProperty(
        name="",
        description="Attribute currently previewed in the viewport, empty when the overlay is off",
        default=""
        )
    overlay_object: StringProperty(
        name="Overlay Object",
        description="Object carrying the overlay; it follows the active object",
        default="",
        )
    overlay_cavity: BoolProperty(
        name="Cavity",
        description="Add edge definition - flat lighting is required for the gradient to stay readable, "
                    "but it removes form cues",
        default=True,
        update=_overlay_cavity_update
        )

    # LEGACY - the colour export's width and travel speed. The node-tree export
    # reads line_width / travel_speed from the printer and stops without them.
    nozzle_diameter: FloatProperty(
        name = "",
        description = "Nozzle diameter - the line width is 1.5 times this",
        default = 0.4
        )
    travel_speed: IntProperty(
        name = "",
        description = "In mm/s. Exported G-code will have mm/min though",
        default = 60
        )

    # LEGACY - only read by the colour-attribute export (legacy.py): the ranges
    # the Flow/Speed/Fan colours map to. Remove with it.
    min_flow: FloatProperty(
        name = "",
        description = "Factor the extrusion value is multiplied with based on the underlying vertex color of that segment",
        default = 0.4
        )
    max_flow: FloatProperty(
        name = "",
        description = "Factor the extrusion value is multiplied with based on the underlying vertex color of that segment",
        default = 1.
        )
    min_speed: FloatProperty(
        name = "",
        description = "Factor the extrusion speed value is multiplied with based on the underlying vertex color of that segment",
        default = 0.2
        )
    max_speed: FloatProperty(
        name = "",
        description = "Factor the extrusion speed value is multiplied with based on the underlying vertex color of that segment",
        default = 1.
        )
    min_fan: FloatProperty(
        name = "",
        description = "Fan speed (0-1, mapped to M106 S0-S255) at the low end of the underlying vertex color of that segment",
        default = 0.,
        min = 0.,
        max = 1.
        )
    max_fan: FloatProperty(
        name = "",
        description = "Fan speed (0-1, mapped to M106 S0-S255) at the high end of the underlying vertex color of that segment",
        default = 1.,
        min = 0.,
        max = 1.
        )
    extrusion_speed: IntProperty(
        name = "",
        description = "In mm/s. Exported G-code will have mm/min though",
        default = 30
        )

    # LEGACY - shown only so the legacy panel looks like the original
    # nozzleboss one. The export reads none of them, as the original didn't:
    # it always uses the T0/T1/Start/End text blocks by name and changes tool
    # where the Tool colour crosses 0.5. Same names as before, so saved
    # values come back.
    tool_color: BoolProperty(
        name="Toolchange based on color:",
        description="Will insert textblock associated with that color. Color value is taken from 'Tool' Vertex Color map",
        default = False
        )
    color_T0: FloatVectorProperty(
        name="Color",
        description="When ever this color is reached on export, add custom G-code in T0 textblock to export file",
        subtype='COLOR', size=3, min=0, max=1, precision=3, step=0.1,
        default=(1.0, 1.0, 1.0)
        )
    color_T1: FloatVectorProperty(
        name="Color",
        description="When ever this color is reached on export, add custom G-code in T1 textblock to export file",
        subtype='COLOR', size=3, min=0, max=1, precision=3, step=0.1,
        default=(0.0, 0.0, 0.0)
        )
    T0: StringProperty(
        name="Custom G-code per color",
        default="T0"
        )
    T1: StringProperty(
        name="Custom G-code per color",
        default="T1"
        )
    start: StringProperty(
        name="Start G-code to append to every exported G-code file",
        default="Start"
        )
    end: StringProperty(
        name="End G-code to append to every exported G-code file",
        default="End"
        )


classes = (gcode_settings,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.nozzleboss = PointerProperty(type=gcode_settings)


def unregister():
    del bpy.types.Scene.nozzleboss
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
