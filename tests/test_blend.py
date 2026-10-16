"""Export the reference node setup, Design/dev_assets/dev_assets.blend, and check
the G-code against what its nodes produce. The blend keeps changing, so the
checks are invariants (width, travel speed, move count, total E, snippet
count, print order) rather than a saved copy of the output.

Skipped when Design/ isn't there (the public repo). Run: tests/run.sh
"""
import sys, os, importlib, re, shutil, tempfile
import bpy
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.basename(REPO)
SOURCE = os.path.join(REPO, "Design", "dev_assets", "dev_assets.blend")


class Reporter:
    def __init__(self):
        self.reports = []

    def report(self, kind, message):
        self.reports.append((kind, message))


def main():
    if not os.path.exists(SOURCE):
        print("SKIP: no", SOURCE)
        return

    # Work on a copy: the G-code is written next to the .blend.
    here = tempfile.mkdtemp(prefix="nb_blend_test_")
    blend = os.path.join(here, "dev_assets.blend")
    shutil.copy(SOURCE, blend)
    bpy.ops.wm.open_mainfile(filepath=blend)

    sys.path.insert(0, os.path.dirname(REPO))
    nb = importlib.import_module(PKG)
    nb.register()
    gcode_source = importlib.import_module(PKG + ".gcode_source")
    context = bpy.context

    printers = [o for o in bpy.data.objects if nb.printer.is_printer(o)]
    assert len(printers) == 1, [o.name for o in printers]
    printer = printers[0]
    # The asset is saved unlinked (users bring the G-code object and the
    # printer in separately), so link it as a user would: the builder's
    # Printer input.
    linked = 0
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if mod.type != 'NODES' or mod.node_group is None:
                continue
            for item in mod.node_group.interface.items_tree:
                if (item.item_type == 'SOCKET' and item.in_out == 'INPUT'
                        and item.socket_type == 'NodeSocketObject' and item.name == "Printer"):
                    getattr(mod.properties.inputs, item.identifier).value = printer
                    obj.update_tag()  # setting it from Python doesn't re-evaluate
                    linked += 1
    assert linked, "no modifier with a Printer input"
    context.view_layer.objects.active = printer
    printer.select_set(True)
    targets = nb.export.export_targets(context, printer)[1]
    assert targets, "no objects linked to the printer"

    counts = (len(bpy.data.objects), len(bpy.data.meshes))
    rep = Reporter()
    assert nb.export.export_gcode(context, rep) == {'FINISHED'}
    assert rep.reports[-1][0] == {'INFO'}, rep.reports
    assert (len(bpy.data.objects), len(bpy.data.meshes)) == counts, "export must not add data"

    text = open(os.path.join(here, "dev_assets.gcode")).read()
    start, end = gcode_source.start_end(printer)
    assert text.startswith(start) and text.rstrip().endswith(end.rstrip())
    body = text[len(start):len(text) - len(end)].splitlines()

    # print order: the same as the panel's list
    order = [o.name for o, _ in nb.panel.NOZZLEBOSS_PT_Panel.print_objects(context, printer)]
    assert [l[len("; object: "):] for l in body if l.startswith("; object: ")] == order, order

    # nothing invalid in the moves; a bare "G1 E" only from the printer's
    # retraction text (G-code retraction, e.g. "G1 E-0.4 F1800")
    retraction = gcode_source.retraction(printer)
    retraction_lines = set() if retraction is None else {
        l for text in retraction for l in text.splitlines()}
    for pattern in (r"nan|inf", r"\bF0\b", r"^G1 ?$", r"^G1 E"):
        bad = [l for l in body if re.search(pattern, l) and l not in retraction_lines]
        assert not bad, (pattern, bad[:3])

    # travels at the printer's travel speed (mm/s -> mm/min)
    travel_F = round(nb.printer.printer_value(printer, "travel_speed") * 60)
    # a long travel is "G1 F<travel>", "G1 Z<hop>", "G1 X.. Y..", "G1 Z.."
    hops = [i for i, l in enumerate(body)
            if re.match(r"G1 Z[\d.]+$", l) and body[i + 1].startswith("G1 X") and " E" not in body[i + 1]]
    assert hops and {body[i - 1] for i in hops} == {f"G1 F{travel_F}"}, {body[i - 1] for i in hops}
    # with the printer's retraction, each hop is between its retract and unretract
    if retraction is not None:
        retract, unretract = (text.splitlines() for text in retraction)
        for i in hops:
            assert body[i - 1 - len(retract):i - 1] == retract, body[i - 4:i]
            assert body[i + 3:i + 3 + len(unretract)] == unretract, body[i:i + 8]
            assert re.match(r"G1 F\d+$", body[i + 3 + len(unretract)]), body[i:i + 8]

    # every move, with E from the printer's line width
    width = nb.printer.printer_value(printer, "line_width")
    moves = [nb.mesh_read.read_object_moves(context, o, width) for o in targets]
    expected_moves = sum(int(np.count_nonzero(~m["zero"])) for m in moves)
    expected_E = sum(float(m["E"][~m["zero"]].sum()) for m in moves)
    E = [float(x) for l in body for x in re.findall(r"^G1 [XYZ].* E([\d.]+)", l)]
    assert len(E) == expected_moves, (len(E), expected_moves)
    assert abs(sum(E) - expected_E) < 1e-4 * len(E) + 1e-6, (sum(E), expected_E)

    # each snippet line once each time its mask turns on, counted from the
    # mesh itself. Stages may share a line (two stages writing "M73 L{...}"),
    # so lines are counted per pattern over every stage that writes them.
    utils = nb.mesh_read
    depsgraph = context.evaluated_depsgraph_get()
    expected = {}  # line pattern -> times written
    for o in targets:
        mesh = o.evaluated_get(depsgraph).data
        start_verts, face, _ = utils.read_path_moves(mesh, utils.read_verts(mesh), utils.read_edges(mesh))
        templates = gcode_source.masked_templates(o)
        for stage in nb.attributes.masked_stages(mesh):
            if stage.label not in templates:
                continue
            on = utils.read_face_mask(mesh, stage.mask)[face]
            fired = int(np.count_nonzero(on & ~np.r_[False, on[:-1]]))
            assert fired == utils.mask_starts(mesh, stage.mask) > 0, (o.name, stage.label, fired)
            for line in templates[stage.label][0].splitlines():
                if line.strip():
                    pattern = "^" + r"-?[\d.]+".join(
                        re.escape(p) for p in re.split(r"\{[^}]*\}", line.rstrip())) + "$"
                    expected[pattern] = expected.get(pattern, 0) + fired
            print(f"{o.name}: '{stage.label}' (p{stage.order:02d}) written {fired} times")
    for pattern, count in expected.items():
        written = sum(1 for l in body if re.match(pattern, l))
        assert written == count, (pattern, count, written)

    print(f"{len(E)} moves, E {sum(E):.1f}, width {width:g} mm, travel F{travel_F}, order {order}")
    nb.unregister()
    print("ALL OK")


main()
