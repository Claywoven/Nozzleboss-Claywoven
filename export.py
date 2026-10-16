"""The export: which objects go into the file, export_gcode(), and the
Export to G-code operator."""

import os

import bpy
from bpy.props import BoolProperty
from bpy.types import Operator

from . import gcode_source, legacy
from .attributes import (END_G_CODE, EXPORT_SETTINGS, RETRACT_G_CODE, START_G_CODE, TOOL_PATH,
                         UNRETRACT_G_CODE)
from .gcode_write import write_path_moves
from .mesh_read import read_object_moves
from .printer import export_settings, find_printer, helper_tag, is_printer

def export_targets(context, obj):
    """(printer, objects to export): every visible mesh linked to obj's
    printer, obj included unless it is the printer. Without a printer, just obj."""
    printer = find_printer(obj)
    if printer is None:
        return None, [obj]
    targets = [o for o in context.scene.objects
               if o.type == 'MESH' and o.visible_get() and not is_printer(o)
               and not helper_tag(o) and find_printer(o) == printer]
    if obj not in targets and not is_printer(obj):
        targets.append(obj)
    return printer, targets


def not_exported(obj):
    """Why a wrapper or profile object can't be exported."""
    return (f"'{obj.name}' is the {helper_tag(obj).capitalize()}, which isn't exported - "
            f"select the G-code object or the printer")


def export_gcode(context, operator=None):
    """Export the active object and every other visible object linked to its
    printer to one G-code file, lowest last move first. Works from the
    printer too. Stops unless the printer has Start_G_code and End_G_code.
    Long travels retract with the printer's Retract_G_code / Unretract_G_code,
    or not at all without them.

    LEGACY: a mesh with no printer and no tool_path is exported alone from
    its colour attributes, with the Start/End text blocks."""

    if not bpy.data.filepath:
        raise ValueError("Save the .blend file before exporting - the G-code is written next to it")
    scene = context.scene
    nozzleboss = scene.nozzleboss
    filename = bpy.path.basename(bpy.data.filepath)
    filename = os.path.splitext(filename)[0] #strip .blend extension


    output_path = bpy.path.abspath("//") + bpy.path.basename(filename) + ".gcode"

    obj = context.active_object
    if helper_tag(obj):
        raise ValueError(not_exported(obj))

    notes = []
    warnings = []
    if legacy.uses_legacy_export(obj):  # LEGACY
        run, created = legacy.read_object_moves(context, obj, nozzleboss)  # LEGACY
        start_code, end_code = legacy.start_end(created)  # LEGACY
        runs, travel_speed = [run], nozzleboss.travel_speed  # LEGACY
        retraction = legacy.RETRACTION  # LEGACY
        warnings += run["warnings"]  # LEGACY
        notes.append(legacy.NOTE)  # LEGACY
        if created:  # LEGACY
            notes.append(f"created text blocks {', '.join(created)}")  # LEGACY
    else:
        printer, targets = export_targets(context, obj)
        start_code, end_code = gcode_source.start_end(printer)
        if start_code is None or end_code is None:
            # A file without the printer's own start code could heat and home
            # with the wrong settings, so the export stops instead.
            missing = " or ".join(name for name, code in ((START_G_CODE, start_code), (END_G_CODE, end_code))
                                  if code is None)
            if printer is None:
                raise ValueError(f"No Start / End code: no {missing} String node (no printer is linked)")
            raise ValueError(f"No Start / End code: no {missing} String node wired to the "
                             f"'{EXPORT_SETTINGS}' output of the printer '{printer.name}'")
        # An empty String counts as absent, as it does for retraction.
        empty = [name for name, code in ((START_G_CODE, start_code), (END_G_CODE, end_code))
                 if not code.strip()]
        if empty:
            raise ValueError(f"No Start / End code: the {' and '.join(empty)} "
                             f"String{'s' if len(empty) > 1 else ''} on the printer "
                             f"'{printer.name}' {'are' if len(empty) > 1 else 'is'} empty")
        retraction = gcode_source.retraction(printer)
        if retraction is None:
            notes.append(f"no retraction (no {RETRACT_G_CODE} / {UNRETRACT_G_CODE} on the printer)")
        if not targets:
            raise ValueError(f"no visible objects are linked to '{printer.name}'")
        width, travel_speed = export_settings(printer)
        runs = []
        for o in targets:
            run = read_object_moves(context, o, width)
            if run is None:
                warnings.append(f"'{o.name}' has no moves")
            else:
                runs.append(run)
        if not runs:
            raise ValueError(f"no {TOOL_PATH} moves to export")
        runs.sort(key=lambda run: (run["last_z"], run["name"]))
        for run in runs:
            prefix = f"{run['name']}: " if len(runs) > 1 else ""
            warnings += [prefix + w for w in run["warnings"]]
        if len(runs) > 1:
            notes.insert(0, f"{len(runs)} objects")

    _txt = [start_code + '\n']
    write_path_moves(_txt, runs, travel_speed, retraction)
    _txt.append('\n' + end_code)

    # Opened only once the G-code is built, so a failed export leaves an
    # existing file untouched.
    try:
        gcode_txt = open(output_path, "w")
    except PermissionError:
        if operator:
            operator.report({'ERROR'}, f"Could not write file — is it open in another program? {output_path}")
        return {'CANCELLED'}
    except OSError as e:
        if operator:
            operator.report({'ERROR'}, f"File write failed: {e}")
        return {'CANCELLED'}
    gcode_txt.write(''.join(_txt))
    gcode_txt.close()

    
    if operator:
        detail = "; ".join(warnings + notes)
        if warnings:
            operator.report({'WARNING'}, f"Exported to {output_path} - {detail}")
        else:
            operator.report({'INFO'}, f"G-code exported to {output_path}" + (f" ({detail})" if detail else ""))
    return {'FINISHED'}

class WM_OT_gcode_export(Operator):
    bl_idname = "wm.gcode_export"  
    bl_label = "Export to G-code"
    bl_description = ("Export the active object and every visible object linked to the same "
                      "printer to one G-code file, next to the .blend file")

    # LEGACY - set by the legacy panel, only to change the tooltip.
    legacy: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})  # LEGACY

    @classmethod
    def description(cls, context, properties):
        if properties.legacy:  # LEGACY
            return legacy.TOOLTIP  # LEGACY
        return cls.bl_description

    
    @classmethod
    def poll(cls, context):
        # The G-code is written next to the .blend, so an unsaved file has nowhere to go
        if not bpy.data.filepath:
            cls.poll_message_set("Save the .blend file before exporting - the G-code is written next to it")
            return False
        obj = context.active_object
        if helper_tag(obj):
            cls.poll_message_set(not_exported(obj))
            return False
        return obj is not None and obj.select_get() and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object

        if context.mode != 'OBJECT':
            self.report({'ERROR'}, "Switch to Object Mode before exporting")
            return {'CANCELLED'}

        try:
            return export_gcode(context, self)
        except Exception as e:
            self.report({'ERROR'}, f"Export failed: {e}")
            return {'CANCELLED'}

classes = (WM_OT_gcode_export,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
