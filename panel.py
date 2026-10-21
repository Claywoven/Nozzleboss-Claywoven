"""The N-panel: printer settings, the values of the selected object (or of
the whole print when the printer is selected), the overlay and export.

Each section is worked out as plain data by a *_report function and kept in
draw_cache until the scene changes, so a redraw doesn't read the meshes."""

import bpy

from . import draw_cache, legacy, overlay
from .attributes import FACE_NUMBER, LINE_WIDTH, PRINT_SPEED, TRAVEL_SPEED, masked_stages
from .export import export_targets, order_number, print_order_key
from .mesh_read import last_move_z, mask_starts
from .printer import find_printer, helper_tag, is_printer, printer_value

# (label, attribute on the printer, unit, format spec)
PRINTER_ROWS = (
    ("Extrusion Line Width", LINE_WIDTH, "mm", ".2f"),
    ("Print Speed", PRINT_SPEED, "mm/s", "g"),
    ("Travel Speed", TRAVEL_SPEED, "mm/s", "g"),
)

class NOZZLEBOSS_PT_Panel(bpy.types.Panel):
 
    bl_label = "NozzleBoss (Claywoven fork)"
    bl_idname = 'NOZZLEBOSS_PT_Panel' 
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "nozzleboss"

    # Width of the label column; the min/max button takes the rest.
    label_width = 1 / 3

    def draw_report_header(self, col):
        split = col.split(factor=self.label_width)
        split.label(text=" ")
        header = split.row()
        for text in ("min:", "max:"):
            sub = header.row()
            sub.alignment = 'CENTER'
            sub.label(text=text)

    def draw_report_row(self, col, context, row):
        """A right-aligned label and one button reporting the attribute's
        min - max, which toggles its viewport overlay. Problems (missing
        attribute or mask) show as a red button instead. row is one of
        object_report's rows."""
        attr_name, label, minmax, problem, integer = row
        split = col.split(factor=self.label_width)
        name = split.row()
        name.alignment = 'RIGHT'
        name.label(text=label)

        value = split.row(align=True)
        if problem is not None:
            value.alert = True
            value.operator("nozzleboss.toggle_overlay", text=problem).attribute = attr_name
            return

        button = value.operator(
            "nozzleboss.toggle_overlay",
            text=self.minmax_text(minmax, integer),
            depress=overlay.is_showing(context, context.object, attr_name),
        )
        button.attribute = attr_name

    @staticmethod
    def minmax_text(minmax, integer):
        if integer:
            return f"{round(minmax[0])}  -  {round(minmax[1])}"
        return f"{minmax[0]:.3f}  -  {minmax[1]:.3f}"

    def draw_summary_row(self, col, row):
        """draw_report_row for the whole print: min - max over every object
        as plain text, without the overlay toggle. row is one of
        print_report's rows."""
        _attr_name, label, minmax, problem, integer = row
        split = col.split(factor=self.label_width)
        name = split.row()
        name.alignment = 'RIGHT'
        name.label(text=label)

        value = split.row()
        value.alignment = 'CENTER'
        if problem is not None:
            value.alert = True
            value.label(text=problem)
            return
        value.label(text=self.minmax_text(minmax, integer))

    def draw_stage_heading(self, col, stage):
        """A staging group's label and order, and how many times its text is
        written. stage is one of the stage reports' entries."""
        heading, starts, _rows = stage
        split = col.split(factor=self.label_width)
        name = split.row()
        name.alignment = 'RIGHT'
        name.label(text=heading)
        value = split.row()
        value.alignment = 'CENTER'
        if starts is None:
            value.alert = True
            value.label(text=f"no {FACE_NUMBER}")
        elif starts == 0:
            value.alert = True
            value.label(text="mask empty")
        else:
            value.label(text=f"written {starts} time{'s' if starts != 1 else ''}")

    @staticmethod
    def stage_heading(label, order):
        return f"{label} (p{order:02d}):"

    @staticmethod
    def printer_report(obj):
        """None without a printer, else (label, value or None, unit, format
        spec) for each of PRINTER_ROWS, read from obj's printer."""
        printer = find_printer(obj)
        if printer is None:
            return None
        return tuple((label, printer_value(printer, attr_name), unit, fmt)
                     for label, attr_name, unit, fmt in PRINTER_ROWS)

    @staticmethod
    def object_report(obj):
        """(rows, stages) for obj's evaluated mesh. Each row is (attribute,
        label, (min, max) or None, problem or None, integer); each stage is
        (heading, times its text is written or None, value rows)."""
        mesh = overlay.evaluated_mesh(obj)

        def row(attr_name, label):
            minmax, problem = overlay.attribute_status(mesh, attr_name)
            return attr_name, label, minmax, problem, overlay.is_integer_attribute(mesh, attr_name)

        rows = tuple(row(attr_name, label) for attr_name, label, _bounds, _integer in overlay.ATTRIBUTES)
        # Staging groups only exist when the node tree makes them.
        stages = tuple(
            (NOZZLEBOSS_PT_Panel.stage_heading(stage.label, stage.order),
             mask_starts(mesh, stage.mask),
             tuple(row(attr_name, name + ":") for name, attr_name in stage.values))
            for stage in masked_stages(mesh)) if mesh else ()
        return rows, stages

    @staticmethod
    def print_objects(context, printer):
        """[(object, evaluated mesh)] for every object the export would write
        from this printer, in print order (export.print_order_key: numbered
        names first, then lowest last move first; objects without
        face_number last)."""
        depsgraph = context.evaluated_depsgraph_get()
        pairs = [(o, o.evaluated_get(depsgraph).data) for o in export_targets(context, printer)[1]]
        keys = {o.name: last_move_z(mesh, o.matrix_world) for o, mesh in pairs}
        return sorted(pairs, key=lambda p: print_order_key(p[0].name, keys[p[0].name]))

    @classmethod
    def print_report(cls, context, printer):
        """(order, rows, stages) for the whole print, with the printer
        selected. order is (object name, has face_number) in print order;
        rows and stages are as in object_report, combined over every object."""
        objects = cls.print_objects(context, printer)
        order = tuple((o.name, mesh.attributes.get(FACE_NUMBER) is not None) for o, mesh in objects)
        if not objects:
            return order, (), ()
        named = [(o.name, mesh) for o, mesh in objects]

        def row(attr_name, label):
            minmax, problem = overlay.combined_status(named, attr_name)
            mesh = next((m for _, m in named if m.attributes.get(attr_name) is not None), None)
            return attr_name, label, minmax, problem, overlay.is_integer_attribute(mesh, attr_name)

        rows = tuple(row(attr_name, label) for attr_name, label, _bounds, _integer in overlay.ATTRIBUTES)
        # {(order, label): [times written or None, {attribute: placeholder}]}
        merged = {}
        for _, mesh in named:
            for stage in masked_stages(mesh):
                entry = merged.setdefault((stage.order, stage.label), [None, {}])
                starts = mask_starts(mesh, stage.mask)
                if starts is not None:
                    entry[0] = (entry[0] or 0) + starts
                entry[1].update((attr_name, name) for name, attr_name in stage.values)
        stages = tuple(
            (cls.stage_heading(label, stage_order), starts,
             tuple(row(attr_name, name + ":") for attr_name, name in sorted(values.items(), key=lambda v: v[1])))
            for (stage_order, label), (starts, values) in sorted(merged.items()))
        return order, rows, stages

    def draw_print_values(self, col, report):
        """With the printer selected: each value's min - max over every
        object the export would write."""
        order, rows, stages = report
        if not order:
            col.label(text="No visible objects linked to this printer")
            return
        self.draw_report_header(col)
        for row in rows:
            self.draw_summary_row(col, row)
        if stages:
            col.separator(factor=1.5)
            self.draw_report_header(col)
            for stage in stages:
                self.draw_stage_heading(col, stage)
                for row in stage[2]:
                    self.draw_summary_row(col, row)

    @staticmethod
    def draw_print_order(layout, order):
        """The objects in the order the export writes them. Drawn below the
        export button so the panel above doesn't move when the printer is
        selected."""
        col = layout.box().column(align=True)
        numbered = any(order_number(name) is not None for name, _ in order)
        col.label(text="Print Order (by name number):" if numbered else "Print Order:")
        col.separator()
        for k, (name, has_face_number) in enumerate(order, 1):
            row = col.row()
            if not has_face_number:
                row.alert = True
                row.label(text=f"{name}: no {FACE_NUMBER}")
            else:
                row.label(text=f"{k}. {name}")

    def draw(self, context): 
        layout = self.layout 

        scn = context.scene
        nozzleboss = scn.nozzleboss 
        obj = context.object
        name = obj.name if obj else None

        # A wrapper or profile: no values to show, and the export is greyed out.
        tag = draw_cache.get(("helper", name), lambda: helper_tag(obj))
        if tag:
            col = layout.box().column(align=True)
            col.label(text=f"This is the {tag.capitalize()} - it isn't exported.", icon='ERROR')
            col.label(text="Select the G-code object or the printer.")
            col.separator(factor=2)
            row = col.row(align=True)
            row.scale_y = 1.5
            row.operator("wm.gcode_export")
            return

        if obj and draw_cache.get(("legacy", name), lambda: legacy.uses_legacy_export(obj)):  # LEGACY
            fan = draw_cache.get(("legacy fan", name), lambda: legacy.has_fan(obj))  # LEGACY
            legacy.draw_panel(layout, nozzleboss, fan)  # LEGACY
            return  # LEGACY

        col = layout.box().column(align=True)
        col.label(text="Export Settings:")
        col.separator()
        # The export reads line_width and travel_speed from the printer.
        printer_rows = draw_cache.get(("printer", name), lambda: self.printer_report(obj))
        if printer_rows:
            table = col.column(align=True)
            for label, value, unit, fmt in printer_rows:
                split = table.split(factor=0.6)
                split.label(text=label)
                cell = split.row()
                cell.alignment = 'RIGHT'
                if value is None:
                    cell.alert = True
                    cell.label(text="missing")
                else:
                    cell.label(text=f"{value:{fmt}} {unit}")

        col = layout.box().column(align=True)

        print_report = (draw_cache.get(("print", name), lambda: self.print_report(context, obj))
                        if is_printer(obj) else None)
        if print_report is not None:
            self.draw_print_values(col, print_report)
        else:
            rows, stages = draw_cache.get(("object", name), lambda: self.object_report(obj))
            self.draw_report_header(col)
            for row in rows:
                self.draw_report_row(col, context, row)

            # Staging groups only exist when the node tree makes them, so the
            # section appears only then.
            if stages:
                col.separator(factor=1.5)
                self.draw_report_header(col)
                for stage in stages:
                    self.draw_stage_heading(col, stage)
                    for row in stage[2]:
                        self.draw_report_row(col, context, row)

        if nozzleboss.overlay_attribute:
            col.separator(factor=1.5)
            overlay.draw_overlay_settings(col, context)

        col.separator(factor=2)
        row = col.row(align=True)
        row.scale_y = 1.5
        row.operator("wm.gcode_export")

        if print_report and print_report[0]:
            self.draw_print_order(layout, print_report[0])


classes = (NOZZLEBOSS_PT_Panel,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
