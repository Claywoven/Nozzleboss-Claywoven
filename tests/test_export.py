"""Headless test of the G-code export: face-attribute path, masked snippets,
value checks, several objects per printer, the node-tree walker, the panel's
print summary and the legacy fallback. Builds its own meshes.

Run all tests:  tests/run.sh
Run this one:   blender --background --factory-startup --python-exit-code 1 \
                    --python tests/test_export.py
"""
import sys, os, importlib, re
import bpy
import numpy as np

# The add-on uses relative imports, so load it as a package named after the
# repo folder, from the folder above it.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.basename(REPO)
sys.path.insert(0, os.path.dirname(REPO))
nb = importlib.import_module(PKG)
nb.register()

import tempfile
HERE = tempfile.mkdtemp(prefix="nb_export_test_")
BLEND = os.path.join(HERE, "test_export.blend")
GCODE = os.path.join(HERE, "test_export.gcode")

# top path verts 0..6, bottom verts 7..13; run A = 0-3, run B = 4-6 far away
top = [(0,0,1), (2,0,1), (4,0,1), (6,0,1), (30,0,1.2), (30,3,1.2), (30,6,1.2)]
heights = [0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5]
N = len(top)
bottom = [(x, y, z - h) for (x, y, z), h in zip(top, heights)]
starts = [0, 1, 2, 4, 5]
faces = [(i, i + 1, i + 1 + N, i + N) for i in starts]
face_number = [i + 1 for i in starts]
flow = [1.0, 1.5, 2.0, 0.5, 1.0]
speed = [20.0, 20.0, 35.0, 10.0, 12.5]
fan = [0, 0, 128, 255, 255]


def geometry_group(name):
    ng = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    gi = ng.nodes.new("NodeGroupInput"); go = ng.nodes.new("NodeGroupOutput")
    ng.links.new(gi.outputs[0], go.inputs[0])
    return ng


def string_bundle(ng, items):
    """Combine Bundle with one String node (via a reroute) per item; returns the String nodes."""
    b = ng.nodes.new("NodeCombineBundle")
    b.bundle_items.clear()
    strings = {}
    for item, text in items:
        b.bundle_items.new('STRING', item)
        s = ng.nodes.new("FunctionNodeInputString"); s.string = text
        r = ng.nodes.new("NodeReroute")
        ng.links.new(s.outputs[0], r.inputs[0]); ng.links.new(r.outputs[0], b.inputs[item])
        strings[item] = s
    return b, strings


def build(with_tool_path=True, scramble=False, drop_face_number=False, masks=False, printer=True):
    """The ribbon mesh; with printer, linked to a printer that has
    Start_G_code / End_G_code (the export stops without them)."""
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    me = bpy.data.meshes.new("ribbon")
    me.from_pydata(top + bottom, [], faces)
    me.update()
    ev = np.zeros(len(me.edges) * 2, dtype=int)
    me.edges.foreach_get("vertices", ev)
    ev = ev.reshape(-1, 2)
    tp = np.array([set(e) in [{i, i + 1} for i in starts] for e in ev])
    if scramble:
        # mark a bottom-to-top diagonal? use a vertical edge instead: (0, 7)
        tp |= np.array([set(e) == {1, 1 + N} for e in ev])
    ufh = np.array([abs(e[0] - e[1]) == N for e in ev])
    if with_tool_path:
        me.attributes.new("tool_path", 'BOOLEAN', 'EDGE').data.foreach_set("value", tp)
    me.attributes.new("use_for_height", 'BOOLEAN', 'EDGE').data.foreach_set("value", ufh)
    if not drop_face_number:
        me.attributes.new("face_number", 'INT', 'FACE').data.foreach_set("value", face_number)
    me.attributes.new("flow_value", 'FLOAT', 'FACE').data.foreach_set("value", flow)
    me.attributes.new("speed_value", 'FLOAT', 'FACE').data.foreach_set("value", speed)
    me.attributes.new("fan_value", 'INT', 'FACE').data.foreach_set("value", fan)
    if masks:
        for label, order, mask, values in MASKS:
            me.attributes.new(f"{label}_mask_p{order:02d}", 'BOOLEAN', 'FACE').data.foreach_set("value", mask)
            for attr_name, column in values.items():
                kind = 'INT' if label == "tool" else 'FLOAT'
                me.attributes.new(attr_name, kind, 'FACE').data.foreach_set("value", column)
    obj = bpy.data.objects.new("ribbon", me)
    bpy.context.scene.collection.objects.link(obj)
    if printer:
        tree = geometry_group("Link Printer")
        printer_info(tree, add_printer())
        obj.modifiers.new("Link", 'NODES').node_group = tree
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def printer_info(tree, obj):
    """An Object Info node for obj, wired to a new Printer output of the
    tree: an unconnected Object Info node doesn't link to a printer."""
    oi = tree.nodes.new("GeometryNodeObjectInfo")
    oi.inputs["Object"].default_value = obj
    tree.interface.new_socket("Printer", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    out = next(n for n in tree.nodes if n.bl_idname == "NodeGroupOutput")
    tree.links.new(oi.outputs["Geometry"], out.inputs["Printer"])
    return oi


def add_printer():
    """A printer object with the old scene defaults (so the expected E and F
    values hold) and Start/End and firmware Retract/Unretract G-code in its
    node tree."""
    pme = bpy.data.meshes.new("printer"); pme.from_pydata([(0, 0, 0)], [], [])
    pobj = bpy.data.objects.new("printer", pme); pobj["cw_prop"] = "printer"
    pme.attributes.new("line_width", 'FLOAT', 'POINT').data[0].value = 0.6
    pme.attributes.new("travel_speed", 'INT', 'POINT').data[0].value = 60
    bpy.context.scene.collection.objects.link(pobj)
    pg = geometry_group("Printer Tree")
    pg.interface.new_socket("Export Settings", in_out='OUTPUT', socket_type='NodeSocketBundle')
    b, _ = string_bundle(pg, [("Start_G_code", "G28 ;printer start\nM83; relative extrusion mode (REQUIRED)"),
                              ("End_G_code", "M84 ;printer end"),
                              ("Retract_G_code", "G10"), ("Unretract_G_code", "G11")])
    pg.links.new(b.outputs[0], pg.nodes["Group Output"].inputs["Export Settings"])
    pobj.modifiers.new("P", 'NODES').node_group = pg
    return pobj


def printer_profile(name, start, end):
    """A group holding a Start/End Combine Bundle wired to its Bundle output,
    like a printer profile; returns the group tree."""
    ng = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    ng.interface.new_socket("Bundle", in_out='OUTPUT', socket_type='NodeSocketBundle')
    go = ng.nodes.new("NodeGroupOutput")
    b, _ = string_bundle(ng, [("Start_G_code", start), ("End_G_code", end)])
    ng.links.new(b.outputs[0], go.inputs["Bundle"])
    return ng


# (label, order, mask per face, {value attribute: values per face}); faces
# are in move order. extrude has a second value, {speed}; pause has none.
MASKS = (
    ("no_move", 5, [False, True, False, False, False], {"no_move_value": [0, 250.4, 0, 0, 0]}),
    ("extrude", 3, [False, True, True, False, True],
     {"extrude_value": [0, 1.234, 9.9, 0, 4.5], "extrude_value_speed": [0, 300, 450, 0, 600]}),
    ("tool", 7, [True, False, False, False, False], {"tool_value": [1, 0, 0, 0, 0]}),
    ("pause", 9, [False, False, False, True, False], {}),
)


class Reporter:
    def __init__(self):
        self.reports = []

    def report(self, kind, message):
        self.reports.append((kind, message))


def export():
    return bpy.ops.wm.gcode_export()


# --- unsaved file: button greyed out, and export_gcode refuses
build()
assert not bpy.data.filepath
assert not bpy.ops.wm.gcode_export.poll(), "export must be unavailable before saving"
try:
    nb.export.export_gcode(bpy.context)
except ValueError as e:
    assert "Save the .blend" in str(e), e
else:
    raise AssertionError("export_gcode must refuse an unsaved file")

bpy.ops.wm.save_as_mainfile(filepath=BLEND)
assert bpy.ops.wm.gcode_export.poll()

# --- new path
build()
assert export() == {'FINISHED'}
lines = open(GCODE).read().splitlines()
body = lines[lines.index('M83; relative extrusion mode (REQUIRED)') + 1:]
ext = [l for l in body if l.startswith('G1 ') and ' E' in l]
assert len(ext) == 5, ext
width = 0.6  # the test printer's line_width
ends = [s + 1 for s in starts]
for k, (line, s) in enumerate(zip(ext, starts)):
    P1, P2 = np.array(top[s]), np.array(top[s + 1])
    E = np.linalg.norm(P2 - P1) * heights[s] * width * flow[k] / 2.405281875
    got = float(re.search(r'E(\S+)', line).group(1))
    assert abs(got - round(E, 4)) < 1e-9, (line, E)
    for ax, i in (('X', 0), ('Y', 1)):
        m = re.search(ax + r'(\S+)', line)
        if m:
            assert float(m.group(1)) == round(P2[i], 4), (line, P2)
# F changes: 20 on first travel restore, then 35, then 10 at travel, 12.5
fs = [l for l in body if re.match(r'G1 .*F', l)]
print("\n".join(body))
assert any(l.endswith('F2100') for l in ext), "35 mm/s -> F2100 on move 3"
assert 'G1 F600' in body and 'G1 F1200' in body  # restore at run starts (10, 20 mm/s)
assert ext[4].endswith('F750'), ext[4]
# the first travel hops and states X, Y and Z (the nozzle position is
# unknown after the start G-code), then one hop between run A and B
first = body.index('; object: ribbon') + 1
assert body[first:first + 5] == ['G10', 'G1 F3600', 'G1 Z10.0', 'G1 X0.0 Y0.0', 'G1 Z1.0'], body[:8]
assert body.count('G11') == 2
hop = body.index('G10', first + 1)
assert ext[2] in body[:hop] and ext[3] in body[hop:]
assert 'G1 Z11.0' in body, "hop above max z 1.0 + 10"
# after the last move: retract and hop above everything printed (max z 1.2),
# not capped (the test printer is one vertex, no build height)
end = body.index('; end of print: retract and lift clear of the print')
assert body[end + 1:end + 4] == ['G10', 'G1 F3600', 'G1 Z11.2'], body[end:]
assert body.count('G10') == 3 and 'G1 Z11.2' not in body[:end]
assert [l for l in body if l.startswith('M106')] == ['M106 S0', 'M106 S128', 'M106 S255'], body

# --- negative: scrambled path
build(scramble=True)
open(GCODE, 'w').write('keep')
try:
    r = export()
except RuntimeError as e:
    r = str(e)
print("scrambled:", r)
assert open(GCODE).read() == 'keep', "failed export must not truncate the file"

build(drop_face_number=True)
try:
    r = export()
except RuntimeError as e:
    r = str(e)
print("no face_number:", r)
assert open(GCODE).read() == 'keep'

# --- value checks (1.5): errors stop the export, warnings don't
def export_error():
    """The operator's error text, or None if it exported."""
    try:
        export()
    except RuntimeError as e:
        return str(e)
    return None


def nbx_export(reporter):
    return nb.export.export_gcode(bpy.context, reporter)


def set_face(name, index, value):
    bpy.data.objects["ribbon"].data.attributes[name].data[index].value = value


for attr_name, value, fragment in (
        ("speed_value", 0.0, "speed_value must be at least 1/60 mm/s (F1) (0) at face_number 3"),
        ("speed_value", 0.01, "at least 1/60 mm/s"),
        ("speed_value", -5.0, "(-5) at face_number 3"),
        ("speed_value", float("nan"), "speed_value is not a number (nan) at face_number 3"),
        ("flow_value", -1.0, "flow_value is negative (-1) at face_number 3"),
        ("flow_value", float("inf"), "flow_value is not a number (inf)")):
    build()
    set_face(attr_name, 2, value)
    open(GCODE, 'w').write('keep')
    err = export_error()
    assert err and fragment in err, (attr_name, value, err)
    assert open(GCODE).read() == 'keep', "a failed check must not write the file"

build()
bpy.data.objects["ribbon"].location.z = -5
err = export_error()
assert err and "path goes below Z 0" in err and "face_number 1" in err, err

# fan: out of range is clipped with a warning; NaN is an error
build()
set_face("fan_value", 0, 300); set_face("fan_value", 1, -5)
rep = Reporter()
assert nbx_export(rep) == {'FINISHED'}
assert "fan_value outside 0-255 on 2 moves, clipped" in rep.reports[-1][1], rep.reports
assert open(GCODE).read().count('M106 S255') >= 1 and 'M106 S0' in open(GCODE).read()

# zero-length move: skipped with a warning, no bare "G1 E0" line
build()
me = bpy.data.objects["ribbon"].data
me.vertices[2].co = me.vertices[1].co
me.vertices[2 + N].co = me.vertices[1 + N].co
rep = Reporter()
assert nbx_export(rep) == {'FINISHED'}
assert "1 zero-length moves skipped" in rep.reports[-1][1], rep.reports
body = open(GCODE).read().splitlines()
assert not any(re.fullmatch(r'G1 E[\d.]+( F\d+)?', l) for l in body), "no axis-less extrusion"
assert len([l for l in body if re.match(r'G1 [XYZ].* E', l)]) == 4

# build height: the top of the printer mesh (world Z) caps the travel hops
# and the end lift; a print above it is warned about
build()
pobj = bpy.data.objects["printer"]
pobj.data.vertices.add(1)
pobj.data.vertices[1].co = (0, 0, 4.0)
pobj.location.z = 1.0  # world space: the top is at 5.0
bpy.context.view_layer.update()
assert nb.printer.build_height(pobj) == 5.0
rep = Reporter()
assert nbx_export(rep) == {'FINISHED'}
body = open(GCODE).read().splitlines()
hops = [l for l in body if re.fullmatch(r'G1 Z[\d.]+', l) and float(l[4:]) > 1.2]
assert hops and set(hops) == {'G1 Z5.0'}, hops
assert "hops not capped" not in rep.reports[-1][1] and "build height" not in rep.reports[-1][1], rep.reports
pobj.location.z = 0.0
pobj.data.vertices[1].co = (0, 0, 1.1)
bpy.context.view_layer.update()
rep = Reporter()
assert nbx_export(rep) == {'FINISHED'}
assert "the print reaches Z 1.2, above the build height 1.1 of 'printer'" in rep.reports[-1][1], rep.reports

# --- LEGACY: colour-attribute files (no tool_path, no printer)
srgb_to_linear = nb.colormaps.srgb_to_linear


def legacy_ribbon(colours=None, tool_blocks=None):
    """The old layout: per island, path verts first, then the bottom verts in
    the same order. Island A (verts 0-7) at Z 2, island B (8-13) at Z 1, so
    B prints first. colours: {name: sRGB value per face}, as painted."""
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for t in list(bpy.data.texts):
        bpy.data.texts.remove(t)
    verts, faces = [], []
    for xs, z in (([0, 2, 4, 6], 2.0), ([20, 20, 20], 1.0)):
        n, base = len(xs), len(verts)
        top = [(x, 3.0 * i if x == 20 else 0.0, z) for i, x in enumerate(xs)]
        verts += top + [(x, y, z - 0.25) for x, y, z in top]
        faces += [(base + i, base + i + 1, base + i + 1 + n, base + i + n) for i in range(n - 1)]
    me = bpy.data.meshes.new("legacy")
    me.from_pydata(verts, [], faces)
    for name, per_face in (colours or {}).items():
        attr = me.color_attributes.new(name, 'FLOAT_COLOR', 'CORNER')
        for poly, value in zip(me.polygons, per_face):
            lin = float(srgb_to_linear(np.array([value]))[0])
            for li in poly.loop_indices:
                attr.data[li].color = (lin, lin, lin, 1.0)
    for name, text in (tool_blocks or {}).items():
        bpy.data.texts.new(name).write(text)
    obj = bpy.data.objects.new("legacy", me)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


s_ = bpy.context.scene.nozzleboss
s_.nozzle_diameter, s_.extrusion_speed, s_.travel_speed = 0.4, 30, 60
s_.min_flow, s_.max_flow, s_.min_speed, s_.max_speed, s_.min_fan, s_.max_fan = 0.5, 1.5, 0.2, 1.0, 0.0, 1.0

# faces 0-2 are island A (x 0-6), faces 3-4 island B (y 0-6)
legacy_ribbon({"Flow": [0.5, 1.0, 0.0, 0.5, 0.5], "Speed": [1.0, 1.0, 0.0, 1.0, 1.0],
               "Tool": [1.0, 1.0, 0.0, 0.0, 0.0], "Fan": [0.0, 1.0, 1.0, 0.0, 0.0]})
n_objects, n_meshes = len(bpy.data.objects), len(bpy.data.meshes)
assert export() == {'FINISHED'}
assert (len(bpy.data.objects), len(bpy.data.meshes)) == (n_objects, n_meshes), "the export leaves nothing behind"
rep = Reporter()
assert nbx_export(rep) == {'FINISHED'}
kind, message = rep.reports[-1]
assert kind == {'INFO'} and "legacy colour export" in message and "created text blocks" not in message, rep.reports
assert sorted(t.name for t in bpy.data.texts) == ["End", "Start", "T0", "T1"]
lines = open(GCODE).read().splitlines()
assert lines[0] == ';nozzleboss', "Start text block"
body = lines[lines.index('M83 ; relative extrusion (nozzleboss moves)') + 1:]
print("\n".join(body))
ext = [l for l in body if re.match(r'G1 [XYZ].* E', l)]
assert len(ext) == 5, ext
# island B (lower) first: its moves go along Y at X 20
assert ext[0].startswith('G1 Y3.0 E') and ext[1].startswith('G1 Y6.0 E'), ext
E = lambda dist, flow: round(dist * 0.25 * 0.6 * flow / 2.405281875, 4)
assert [float(re.search(r'E(\S+)', l).group(1)) for l in ext] == [
    E(3, 1.0), E(3, 1.0), E(2, 1.0), E(2, 1.5), E(2, 0.5)], ext
assert ext[0].endswith('F1800') and ext[4].endswith('F360'), "white 30 mm/s, black 0.2 x 30"
# the first travel states X, Y and Z; tool blocks where Tool changes, then G90/M83 and F again
assert body[1:6] == ['G10 ', 'G1 F3600', 'G1 Z10.0', 'G1 X20.0 Y0.0', 'G1 Z1.0'], body[:8]
t1, t0 = body.index('T1; switch to extruder T1 (any G-code macro can be passed here)'), \
    body.index('T0; switch to extruder T0 (any G-code macro can be passed here)')
assert t1 < body.index(ext[0]) and body.index(ext[1]) < t0 < body.index(ext[2]), body
assert body[t0 + 2:t0 + 4] == ['G90', 'M83'] and body[body.index(ext[2])].endswith('F1800')
assert [l for l in body if l.startswith('M106')] == ['M106 S0', 'M106 S255'], body  # B, B, A: 0, 0, 0, 255, 255
assert body.count('G10 ') == 2, "hop between the islands"

# no Fan colour: the export writes no M106, so T0/T1 alone control the fan
legacy_ribbon({"Tool": [1.0, 1.0, 1.0, 0.0, 0.0]}, {"T0": "T0\nM106 S80", "T1": "T1\nM107"})
rep = Reporter()
assert nbx_export(rep) == {'FINISHED'}
assert "no Flow, Speed, Fan colour" in rep.reports[-1][1], rep.reports
assert "created text blocks Start, End" in rep.reports[-1][1], rep.reports
body = open(GCODE).read().splitlines()
assert not any(l.startswith('M106 S0') or l == 'M106 S255' for l in body), body
fans = [l for l in body if l.startswith(('M106', 'M107'))]
assert fans == ['M107', 'M106 S80'], fans
assert [l for l in body if re.match(r'G1 [XYZ].* E', l)][0].endswith('F1800'), "no Speed: max speed"

# a mesh linked to a printer never falls back: no tool_path is an error
obj = build(with_tool_path=False)
assert not nb.legacy.uses_legacy_export(obj)
err = export_error()
assert err and "has no 'tool_path' attribute" in err, err

# not a ribbon: a clear error, no text blocks created, the file untouched
legacy_ribbon()
me = bpy.data.objects["legacy"].data
me.vertices[1].co.z += 5   # still a ribbon, but scramble the order:
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.sort_elements(type='RANDOMIZE', elements={'VERT'}, seed=3)
bpy.ops.object.mode_set(mode='OBJECT')
open(GCODE, 'w').write('keep')
err = export_error()
assert err and "is not an edge of a face" in err, err
assert open(GCODE).read() == 'keep' and len(bpy.data.texts) == 0, "a failed export changes nothing"

# only meshes can be exported (a curve used to fail and leak a mesh)
curve = bpy.data.objects.new("curve", bpy.data.curves.new("curve", 'CURVE'))
bpy.context.scene.collection.objects.link(curve)
bpy.context.view_layer.objects.active = curve
curve.select_set(True)
assert not bpy.ops.wm.gcode_export.poll()

# --- G-code from String nodes in the node tree
format_snippet = nb.gcode_write.format_snippet

# bare float placeholders round to 4 decimals; a format spec wins; ints stay ints
assert format_snippet("w", "G1 X{a}", {"a": np.float64(1.0591715574264526)}) == "G1 X1.0592\n"
assert format_snippet("w", "{a:.2f}", {"a": np.float64(1.0591715574264526)}) == "1.06\n"
assert format_snippet("w", "T{a}", {"a": 1}) == "T1\n"
assert format_snippet("w", "{a} F{b}", {"a": 250.4, "b": 300}) == "250.4 F300\n"

for name in ("extrude", "no_move", "Start", "End"):
    if name in bpy.data.texts:
        bpy.data.texts.remove(bpy.data.texts[name])


def stage(label, text, name):
    """A staging group: its bundle's Label and G_code_string String nodes."""
    ng = geometry_group(name)
    _, strings = string_bundle(ng, [("Label", label), ("G_code_string", text)])
    return ng, strings["G_code_string"], strings["Label"]


def setup(printer=True):
    """Ribbon with masks and a modifier: Main -> stage groups; Object Info -> printer."""
    obj = build(masks=True, printer=False)
    main = geometry_group("Main")
    gi, go = main.nodes["Group Input"], main.nodes["Group Output"]
    stages = {}
    prev = gi.outputs[0]
    for key, text in (("extrude", "G1 E{extrude:.2f} F{speed:.0f}"),
                      ("no_move", "G4 P{no_move:.0f}"),
                      ("tool", "T{tool}"),
                      ("pause", "M601 ; pause")):
        ng, s, label = stage(key, text, "Stage " + key)
        node = main.nodes.new("GeometryNodeGroup"); node.node_tree = ng
        main.links.new(prev, node.inputs[0]); prev = node.outputs[0]
        stages[key] = (node, s, label)
    main.links.new(prev, go.inputs[0])
    if printer:
        printer_info(main, add_printer())
    obj.modifiers.new("GN", 'NODES').node_group = main
    bpy.context.view_layer.objects.active = obj
    return obj, main, stages


def run():
    rep = Reporter()
    result = nb.export.export_gcode(bpy.context, rep)
    return result, rep.reports[-1] if rep.reports else None


def gcode_body():
    lines = open(GCODE).read().splitlines()
    return lines, lines[lines.index('M83; relative extrusion mode (REQUIRED)') + 1:]


def expect_error(fragment):
    open(GCODE, 'w').write('keep')
    try:
        run()
    except ValueError as e:
        print("error ok:", e)
        assert fragment in str(e), (fragment, str(e))
    else:
        raise AssertionError("expected an error containing " + fragment)
    assert open(GCODE).read() == 'keep'


# 1. templates + printer Start/End
obj, main, stages = setup()
result, report = run()
assert result == {'FINISHED'} and report[0] == {'INFO'} and "text block" not in report[1], report
lines, body = gcode_body()
print("\n".join(lines))
assert lines[0] == 'G28 ;printer start' and lines[-1] == 'M84 ;printer end', (lines[0], lines[-1])
moves = [i for i, l in enumerate(body) if re.match(r'G1 [XY]', l) and ' E' in l]
ex = [i for i, l in enumerate(body) if l.startswith('G1 E')]
nm = [i for i, l in enumerate(body) if l.startswith('G4 P')]
assert [body[i] for i in ex] == ['G1 E1.23 F300', 'G1 E4.50 F600'], "two values on one mask"
assert [body[i] for i in nm] == ['G4 P250']
assert ex[0] < nm[0] < moves[1] and moves[0] < ex[0] and moves[3] < ex[1] < moves[4], (ex, nm, moves)
assert body[moves[1]].endswith('F1200') and body[moves[4]].endswith('F750')
assert body.count('T1') == 1 and body.index('T1') < moves[0], "INT value formats as T1"
assert body.count('M601 ; pause') == 1 and moves[2] < body.index('M601 ; pause') < moves[3], "a mask with no values"

# 1b. line width and travel speed come from the printer
assert 'G1 F3600' in body, body
E_before = [float(m) for m in re.findall(r' E([\d.]+)$', "\n".join(body[i] for i in moves), re.M)]
pme = bpy.data.objects["printer"].data
pme.attributes["line_width"].data[0].value = 0.8
pme.attributes["travel_speed"].data[0].value = 50
result, report = run()
assert report[0] == {'INFO'}, report
lines, body = gcode_body()
moves = [i for i, l in enumerate(body) if re.match(r'G1 [XY]', l) and ' E' in l]
E_after = [float(m) for m in re.findall(r' E([\d.]+)$', "\n".join(body[i] for i in moves), re.M)]
assert len(E_after) == len(E_before) and all(abs(b - a * 0.8 / 0.6) < 1e-3 for a, b in zip(E_before, E_after)), (E_before, E_after)
assert 'G1 F3000' in body and 'G1 F3600' not in body, body
pme.attributes["line_width"].data[0].value = 0.6
pme.attributes["travel_speed"].data[0].value = 60

# 2. fixed code: a stage's values don't have to be used
stages["extrude"][1].string = "M600"
assert run()[0] == {'FINISHED'}
assert gcode_body()[1].count('M600') == 2

# 3. errors
stages["extrude"][1].string = "G1 E{amount}"
expect_error("It uses {amount}, but 'extrude' has only {extrude}, {speed}.")
stages["extrude"][1].string = "G1 E{extrude:.2f}"
stages["pause"][1].string = "M601 {pause}"
expect_error("It uses {pause}, but 'pause' has no values.")
stages["pause"][1].string = "M601 ; pause"
stages["extrude"][1].string = "G1 E{}"
expect_error("needs a name")
# a typed ')' or '(' instead of a curly bracket names the stage, the line and the typo
stages["extrude"][1].string = "M600\nG1 E{extrude)"
# one point per line, so Blender's error popup can show them as rows
expect_error("'ribbon':\nThe G-code for 'extrude' in 'Main' > 'Group' / 'Combine Bundle':\n"
             "A '{' is not closed on line 2:\n    G1 E{extrude)\nDid you type ')' instead of '}'?\n"
             "Placeholders need curly brackets on both sides")
stages["extrude"][1].string = "G1 E(extrude}"
expect_error("A '}' has no opening '{' on line 1:\n    G1 E(extrude}\nDid you type '(' instead of '{'?")
stages["extrude"][1].string = "M117 {{literal}} {extrude:.2f}"
assert run()[0] == {'FINISHED'}, "escaped braces are fine"
stages["extrude"][1].string = "G1 E{extrude:.2f}"
stages["no_move"][2].string = "extrude"
expect_error("Two G-code stages are named")
for bad, fragment in (("", "the Label is empty"), ("no_value", "can't contain '_value'"),
                      ("a_mask", "can't contain '_mask'")):
    stages["no_move"][2].string = bad
    expect_error(fragment)
stages["no_move"][2].string = "no_move"
ext_tree = stages["extrude"][0].node_tree
bundle = next(n for n in ext_tree.nodes if n.bl_idname == "NodeCombineBundle")
for item in ("G_code_string", "Label"):
    # computed strings can't be read without evaluating the tree
    old = bundle.inputs[item].links[0].from_socket
    fmt = ext_tree.nodes.new("FunctionNodeFormatString")
    link = ext_tree.links.new(fmt.outputs[0], bundle.inputs[item])
    expect_error(f"{item} on 'Main' > 'Group' / 'Combine Bundle': must come from a String node")
    ext_tree.links.remove(link)
    ext_tree.links.new(old, bundle.inputs[item])
bundle.bundle_items.remove(bundle.bundle_items["Label"])
expect_error("no Label in the bundle")
bundle.bundle_items.new('STRING', "Label")
ext_tree.links.new(stages["extrude"][2].outputs[0].links[0].to_node.outputs[0], bundle.inputs["Label"])
assert run()[0] == {'FINISHED'}

# 4. template with no mask, and mask with no template
stages["no_move"][2].string = "missing"
result, report = run()
assert result == {'FINISHED'} and report[0] == {'WARNING'}, report
assert "'missing' unused (no missing_mask_pNN" in report[1] and "no G_code_string for 'no_move'" in report[1], report
assert 'G4' not in open(GCODE).read()
stages["no_move"][2].string = "no_move"

# 5. dead stages are ignored: muted, or geometry output unconnected
stages["no_move"][0].mute = True
result, report = run()
assert "no G_code_string for 'no_move'" in report[1], report
stages["no_move"][0].mute = False
node = stages["no_move"][0]
for l in list(node.outputs[0].links):
    main.links.remove(l)
main.links.new(node.inputs[0].links[0].from_socket, stages["tool"][0].inputs[0])
result, report = run()
assert "no G_code_string for 'no_move'" in report[1], report

# 6. no Start / End code stops the export (no text-block fallback)
setup(printer=False)
expect_error("No Start / End code: no Start_G_code or End_G_code String node (no printer is linked)")
setup()
tree = bpy.data.objects["printer"].modifiers["P"].node_group
bundle = next(n for n in tree.nodes if n.bl_idname == "NodeCombineBundle")
bundle.bundle_items.remove(bundle.bundle_items["End_G_code"])
expect_error("No Start / End code: no End_G_code String node wired to the 'Export Settings' "
             "output of the printer 'printer'")
assert "End" not in bpy.data.texts, "no text block written"
# an empty (or blank) String counts as absent, as for retraction
setup()
tree = bpy.data.objects["printer"].modifiers["P"].node_group
bundle = next(n for n in tree.nodes if n.bl_idname == "NodeCombineBundle")
node = bundle.inputs["Start_G_code"].links[0].from_node
while node.bl_idname == "NodeReroute":
    node = node.inputs[0].links[0].from_node
node.string = " \n"
expect_error("No Start / End code: the Start_G_code String on the printer 'printer' is empty")

# Start/End count only when their bundle is wired to the printer tree's
# Export Settings output
setup()
tree = bpy.data.objects["printer"].modifiers["P"].node_group
go = tree.nodes["Group Output"]
bundle = next(n for n in tree.nodes if n.bl_idname == "NodeCombineBundle")
tree.links.remove(go.inputs["Export Settings"].links[0])
expect_error("No Start / End code: no Start_G_code or End_G_code String node wired to the 'Export Settings'")
# a profile group, through a reroute, wins over the unplugged bundle; a
# second, unplugged profile is ignored rather than "More than one"
profile = tree.nodes.new("GeometryNodeGroup")
profile.node_tree = printer_profile("A1 Mini", "G28 ;profile start\nM83", "M84 ;profile end")
spare = tree.nodes.new("GeometryNodeGroup")
spare.node_tree = printer_profile("Other printer", "G28 ;other start", "M84 ;other end")
reroute = tree.nodes.new("NodeReroute")
tree.links.new(profile.outputs["Bundle"], reroute.inputs[0])
tree.links.new(reroute.outputs[0], go.inputs["Export Settings"])
result, report = run()
assert result == {'FINISHED'}, report
out = open(GCODE).read()
assert out.startswith("G28 ;profile start") and out.rstrip().endswith("M84 ;profile end"), out[-200:]
assert "other" not in out and "printer start" not in out, "only the wired bundle is read"
# a muted link, or an output with another name, doesn't count
reroute.outputs[0].links[0].is_muted = True
expect_error("No Start / End code")
tree.links.remove(reroute.outputs[0].links[0])
tree.interface.new_socket("Settings", in_out='OUTPUT', socket_type='NodeSocketBundle')
tree.links.new(reroute.outputs[0], go.inputs["Settings"])
expect_error("No Start / End code")

# retraction is the printer's Retract_G_code / Unretract_G_code text around
# each long travel, any G-code (here the slicer's own E moves)
setup()
tree = bpy.data.objects["printer"].modifiers["P"].node_group
bundle = next(n for n in tree.nodes if n.bl_idname == "NodeCombineBundle")
strings = {item.name: bundle.inputs[item.name].links[0].from_node.inputs[0].links[0].from_node
           for item in bundle.bundle_items}
strings["Retract_G_code"].string = "G1 E-0.4 F1800"
strings["Unretract_G_code"].string = "G1 E0.4 F1800\n"
result, report = run()
assert result == {'FINISHED'} and "no retraction" not in report[1], report
lines, body = gcode_body()
first = body.index('G1 E-0.4 F1800')
# the unretract's F1800 must not stay in force: the extrusion speed follows it
assert body[first + 1:first + 7] == ['G1 F3600', 'G1 Z10.0', 'G1 X0.0 Y0.0', 'G1 Z1.0', 'G1 E0.4 F1800',
                                     'G1 F1200'], body[first:first + 8]
# one pair per travel, plus a retract with no unretract at the end of the print
assert body.count('G1 E-0.4 F1800') == 3 and body.count('G1 E0.4 F1800') == 2, body
end = body.index('; end of print: retract and lift clear of the print')
assert body[end + 1:end + 4] == ['G1 E-0.4 F1800', 'G1 F3600', 'G1 Z11.2'], body[end:]
assert 'G10' not in body and 'G11' not in body
# only one of the two stops the export: the retract would never be undone
strings["Unretract_G_code"].string = "  \n"  # blank counts as absent
expect_error("The printer 'printer' has Retract_G_code but no Unretract_G_code")
bundle.bundle_items.remove(bundle.bundle_items["Retract_G_code"])
strings["Unretract_G_code"].string = "G11"
expect_error("has Unretract_G_code but no Retract_G_code")
# neither: travels still hop, with no retraction, and the result says so
bundle.bundle_items.remove(bundle.bundle_items["Unretract_G_code"])
result, report = run()
assert result == {'FINISHED'} and report[0] == {'INFO'}, report
assert "no retraction (no Retract_G_code / Unretract_G_code on the printer)" in report[1], report
lines, body = gcode_body()
assert 'G10' not in body and 'G11' not in body and 'G1 Z10.0' in body, body

# a printer without line_width / travel_speed (or with 0) stops the export: no hidden fallback
setup()
pme = bpy.data.objects["printer"].data
pme.attributes["line_width"].data[0].value = 0
expect_error("Printer has no Line Width: line_width on 'printer' is 0")
pme.attributes.remove(pme.attributes["line_width"])
expect_error("Printer has no Line Width: no line_width attribute on 'printer'")
setup()
pme = bpy.data.objects["printer"].data
pme.attributes.remove(pme.attributes["travel_speed"])
expect_error("Printer has no Travel Speed: no travel_speed attribute on 'printer'")

# 7. through the operator (applied duplicate; strings read from the original)
setup()
bpy.context.active_object.select_set(True)
assert bpy.ops.wm.gcode_export() == {'FINISHED'}
out = open(GCODE).read()
assert 'G1 E1.23 F300' in out and out.startswith('G28 ;printer start'), out[:200]
assert len(bpy.data.objects) == 2, [o.name for o in bpy.data.objects]

# 8. every visible object linked to the printer, lowest last move first
first = bpy.data.objects["ribbon"]
low = first.copy(); low.data = first.data.copy(); low.name = "low"
low.location = (100, 0, -0.5)  # last move at z 0.7, below ribbon's 1.2
bpy.context.scene.collection.objects.link(low)
hidden = first.copy(); hidden.name = "hidden"  # linked but hidden
bpy.context.scene.collection.objects.link(hidden); hidden.hide_set(True)
plain = bpy.data.objects.new("plain", first.data)  # visible but not linked
bpy.context.scene.collection.objects.link(plain)
counts = (len(bpy.data.objects), len(bpy.data.meshes))
bpy.context.view_layer.objects.active = first; first.select_set(True)
assert bpy.ops.wm.gcode_export() == {'FINISHED'}
assert (len(bpy.data.objects), len(bpy.data.meshes)) == counts, "export must not add data"
out = open(GCODE).read(); lines = out.splitlines()
assert out.count('G28 ;printer start') == 1 and out.count('M84 ;printer end') == 1
objects = [l for l in lines if l.startswith('; object:')]
assert objects == ['; object: low', '; object: ribbon'], objects
split = lines.index('; object: ribbon')
low_part, ribbon_part = lines[:split], lines[split:]
assert all(float(m) >= 100 for m in re.findall(r'^G1 X([\d.]+) .*E', "\n".join(low_part), re.M))
assert ribbon_part[1:5] == ['G10', 'G1 F3600', 'G1 Z10.7', 'G1 X0.0 Y0.0'], ribbon_part[:6]
for part in (low_part, ribbon_part):
    assert 'G1 E1.23 F300' in part and 'T1' in part, "each object gets its own snippets"
ext_moves = [l for l in lines if re.match(r'G1 [XY].* E', l)]
assert len(ext_moves) == 10, ext_moves
rep = Reporter()
assert nb.export.export_gcode(bpy.context, rep) == {'FINISHED'}
assert rep.reports[-1][0] == {'INFO'} and "2 objects" in rep.reports[-1][1], rep.reports

# 9. from the printer: the same file
bpy.context.view_layer.objects.active = bpy.data.objects["printer"]
bpy.data.objects["printer"].select_set(True)
assert bpy.ops.wm.gcode_export() == {'FINISHED'}
assert open(GCODE).read() == out

# 10. Solidify is switched off while reading, and back on afterwards
sol = low.modifiers.new("Solidify", 'SOLIDIFY'); sol.thickness = 0.5
assert bpy.ops.wm.gcode_export() == {'FINISHED'}
assert open(GCODE).read() == out
assert sol.show_viewport
low.modifiers.remove(sol)

# 10b. a number at the start of the name sets the print order
names = ("10.Cup", "010 Cup", "2.5_Lid", "30-Base", "01.G-code", "2.5.Lid", "3D Logo", "Cup")
assert [nb.export.order_number(n) for n in names] == [10, 10, 2.5, 30, 1, 2.5, None, None]


def export_order():
    rep = Reporter()
    assert nb.export.export_gcode(bpy.context, rep) == {'FINISHED'}
    return [l for l in open(GCODE).read().splitlines() if l.startswith('; object:')], rep.reports[-1][1]


# numbered: by number, even when the first ends higher (and the export says so)
first.name, low.name = "20.ribbon", "30.low"
objects, report = export_order()
assert objects == ['; object: 20.ribbon', '; object: 30.low'], objects
assert "'20.ribbon' (ends at Z 1.2) prints before '30.low' (Z 0.7): check the gantry clears it" in report, report
# unnumbered objects print after the numbered ones
low.name = "low"
objects, report = export_order()
assert objects == ['; object: 20.ribbon', '; object: low'], objects
assert "1 object without an order number prints after the numbered ones" in report, report
# a number used twice: those objects keep the default order, with a warning
low.name = "20.low"
objects, report = export_order()
assert objects == ['; object: 20.low', '; object: 20.ribbon'], objects
assert "order number 20 is on 20.low, 20.ribbon: printed lowest last move first" in report, report
first.name, low.name = "ribbon", "low"
assert export_order()[0] == ['; object: low', '; object: ribbon'], "no numbers: the default order"

# 11. panel with the printer selected: combined values and print order
ov = importlib.import_module(PKG + ".overlay")
dg = bpy.context.evaluated_depsgraph_get()
named = [(o.name, o.evaluated_get(dg).data) for o in (first, low)]
assert ov.combined_status(named, "speed_value") == ov.attribute_status(named[0][1], "speed_value")
runs = [nb.mesh_read.read_object_moves(bpy.context, o, 0.6) for o in (first, low)]
for o, moves in zip((first, low), runs):
    assert abs(nb.mesh_read.last_move_z(o.evaluated_get(dg).data, o.matrix_world) - moves["last_z"]) < 1e-6


class FakeLayout:
    """Records label and operator text instead of drawing."""
    def __init__(self, out):
        self.out = out
    def __getattr__(self, name):  # alert, alignment, scale_y... are set, not read
        return lambda *a, **k: FakeLayout(self.out) if name in ("box", "column", "row", "split") else None
    def __setattr__(self, name, value):
        if name == "alert" and value:
            self.out.append("ALERT")
        object.__setattr__(self, name, value)
    def label(self, text="", **kw):
        self.out.append(text)
    def operator(self, idname, text="", **kw):
        self.out.append(text)
        return type("Op", (), {})()


# The panel's methods without registering a Panel subclass
FakePanel = type("FakePanel", (), {k: v for k, v in nb.panel.NOZZLEBOSS_PT_Panel.__dict__.items()
                                   if not k.startswith("__")})


def panel_text(active):
    if bpy.context.view_layer.objects.active != active:  # setting it is a scene change
        bpy.context.view_layer.objects.active = active
    # Blender's event loop evaluates the depsgraph before each redraw, which
    # clears the panel's cache after a change (draw_cache).
    bpy.context.view_layer.update()
    panel = FakePanel(); out = []
    panel.layout = FakeLayout(out)
    panel.draw(bpy.context)
    return out

text = panel_text(bpy.data.objects["printer"])
print("\n".join(text))
assert "Print Order:" in text and text.index("1. low") < text.index("2. ribbon"), text
assert text.index("Fan Speed:") < text.index("Print Order:"), "print order below the values and export button"
first.name, low.name = "20.ribbon", "30.low"  # the panel lists the same order as the export
numbered = panel_text(bpy.data.objects["printer"])
assert "Print Order (by name number):" in numbered, numbered
assert numbered.index("1. 20.ribbon") < numbered.index("2. 30.low"), numbered
first.name, low.name = "ribbon", "low"
summary = text[text.index("Flow Multiplier:"):]
assert "ALERT" not in summary and "missing" not in " ".join(summary), summary
assert "10.000  -  35.000" in summary, "speed over both objects"
assert "1.234  -  9.900" in summary, "masked extrude value, masked faces only"
stage_rows = summary[summary.index("extrude (p03):"):]
assert stage_rows[:5] == ["extrude (p03):", "written 4 times", "extrude:", "1.234  -  9.900", "speed:"], stage_rows
assert summary.index("extrude (p03):") < summary.index("no_move (p05):") < summary.index("pause (p09):"), "in _pNN order"
assert summary[summary.index("pause (p09):") + 1] == "written 2 times", "a mask with no values"
bpy.data.objects["low"].data.attributes.remove(bpy.data.objects["low"].data.attributes["fan_value"])
text = panel_text(bpy.data.objects["printer"])
assert "missing on low" in text and "ALERT" in text[text.index("Flow Multiplier:"):], text
assert "Print Order:" not in panel_text(first), "object view unchanged"

# 11b. the panel keeps its values between redraws until the scene changes
calls = {"status": 0, "printer": 0}
real_status, real_find = ov.attribute_status, nb.panel.find_printer
def counted_status(*a):
    calls["status"] += 1
    return real_status(*a)
def counted_find(*a):
    calls["printer"] += 1
    return real_find(*a)
ov.attribute_status, nb.panel.find_printer = counted_status, counted_find
try:
    for active in (first, bpy.data.objects["printer"]):
        nb.draw_cache.clear()
        start = dict(calls)
        text = panel_text(active)
        before = dict(calls)
        assert before["status"] > start["status"] and before["printer"] > start["printer"]
        assert panel_text(active) == text, "a repeat draw shows the same"
        assert calls == before, f"a repeat draw of {active.name} recomputed: {before} -> {calls}"
    speed_attr = first.data.attributes["speed_value"]
    old = speed_attr.data[0].value
    speed_attr.data[0].value = 77.5
    first.data.update()
    assert "10.000  -  77.500" in panel_text(first), "an edit shows at the next draw"
    speed_attr.data[0].value = old
    first.data.update()
finally:
    ov.attribute_status, nb.panel.find_printer = real_status, real_find

# 12. a NaN masked value where its snippet fires stops the export; in a
#     value the text doesn't use it doesn't
first.data.attributes["extrude_value"].data[1].value = float("nan")
bpy.context.view_layer.objects.active = first
err = export_error()
assert err and "extrude_value is not a number (nan) at face_number 2" in err and "'ribbon'" in err, err
first.data.attributes["extrude_value"].data[1].value = 1.234
unused = first.data.attributes.new("extrude_value_unused", 'FLOAT', 'FACE')
unused.data[1].value = float("nan")
assert "unused" not in (export_error() or ""), "a value the text doesn't use isn't read"
first.data.attributes.remove(unused)

# 12b. stage names the export can't tell apart stop it
for attr_name, kind, fragment in (
        ("extrude_mask_p09", 'BOOLEAN', "extrude_mask_p03 and extrude_mask_p09 are two stages named 'extrude'"),
        ("extrude_value_extrude", 'FLOAT', "extrude_value and extrude_value_extrude are both {extrude}"),
        ("fan_mask_p01", 'BOOLEAN', "fan_mask_p01: the Name 'fan' clashes with fan_value")):
    extra = first.data.attributes.new(attr_name, kind, 'FACE')
    err = export_error()
    assert err and fragment in err and "'ribbon'" in err, (fragment, err)
    first.data.attributes.remove(extra)
err = export_error() or ""
assert "two stages" not in err and "both" not in err and "clashes" not in err, err  # 'low' lost fan_value in 11

# 13. one node-tree walker: disabled modifiers don't link to the printer
obj, main, stages = setup()
printer = bpy.data.objects["printer"]
other = obj.copy(); other.data = obj.data.copy(); other.name = "other"
other.location.x = 100
bpy.context.scene.collection.objects.link(other)
assert nb.printer.find_printer(other) == printer
other.modifiers["GN"].show_viewport = False
assert nb.printer.find_printer(other) is None, "disabled modifier must not link to the printer"
assert nb.export.export_targets(bpy.context, printer)[1] == [obj]
bpy.data.objects.remove(other)

# 14. a printer passed down two group levels through group inputs
oi = next(n for n in main.nodes if n.bl_idname == "GeometryNodeObjectInfo")
main.nodes.remove(oi)
assert nb.printer.find_printer(obj) is None
inner = bpy.data.node_groups.new("Inner", 'GeometryNodeTree')
inner.interface.new_socket("Printer", in_out='INPUT', socket_type='NodeSocketObject')
inner.interface.new_socket("Location", in_out='OUTPUT', socket_type='NodeSocketVector')
inner_info = inner.nodes.new("GeometryNodeObjectInfo")
inner.links.new(inner.nodes.new("NodeGroupInput").outputs["Printer"], inner_info.inputs["Object"])
inner.links.new(inner_info.outputs["Location"], inner.nodes.new("NodeGroupOutput").inputs["Location"])
outer = bpy.data.node_groups.new("Outer", 'GeometryNodeTree')
outer.interface.new_socket("Printer", in_out='INPUT', socket_type='NodeSocketObject')
inner_node = outer.nodes.new("GeometryNodeGroup"); inner_node.node_tree = inner
outer.links.new(outer.nodes.new("NodeGroupInput").outputs["Printer"], inner_node.inputs["Printer"])
outer_node = main.nodes.new("GeometryNodeGroup"); outer_node.node_tree = outer
outer_node.inputs["Printer"].default_value = printer
assert nb.printer.find_printer(obj) == printer, "printer through Main > Outer > Inner"
outer_node.mute = True
assert nb.printer.find_printer(obj) is None, "muted group must not link to the printer"
outer_node.mute = False
# an unconnected Object Info node doesn't count, even for an earlier
# printer; connected (by any output, through an unmuted link) it does
spare = printer.copy(); spare.data = printer.data.copy(); spare.name = "spare printer"
bpy.context.scene.collection.objects.link(spare)
loose = main.nodes.new("GeometryNodeObjectInfo"); loose.inputs["Object"].default_value = spare
assert nb.printer.find_printer(obj) == printer, "an unconnected Object Info is ignored"
for l in list(inner_info.outputs["Location"].links):
    l.is_muted = True
assert nb.printer.find_printer(obj) is None, "a muted link doesn't connect"
for l in list(inner_info.outputs["Location"].links):
    l.is_muted = False
bpy.data.objects.remove(spare)

# 15. one stage group reused, its G_code_string fed from the group input
# and its Label from the group's Name input
reusable = geometry_group("Reusable")
reusable.interface.new_socket("Name", in_out='INPUT', socket_type='NodeSocketString')
reusable.interface.new_socket("Text", in_out='INPUT', socket_type='NodeSocketString')
bundle = reusable.nodes.new("NodeCombineBundle")
bundle.bundle_items.clear()
for item, socket in (("Label", "Name"), ("G_code_string", "Text")):
    bundle.bundle_items.new('STRING', item)
    reusable.links.new(reusable.nodes["Group Input"].outputs[socket], bundle.inputs[item])
for key in ("no_move", "tool"):
    stages[key][0].mute = True  # the per-stage copies are replaced by two instances
go = main.nodes["Group Output"]
prev = go.inputs[0].links[0].from_socket
copies = []
for label, text in (("no_move", "G4 P{no_move:.0f}"), ("tool", "T{tool}")):
    node = main.nodes.new("GeometryNodeGroup"); node.node_tree = reusable
    node.inputs["Name"].default_value = label
    node.inputs["Text"].default_value = text
    main.links.new(prev, node.inputs[0]); prev = node.outputs[0]
    copies.append(node)
main.links.new(prev, go.inputs[0])
result, report = run()
assert result == {'FINISHED'} and report[0] == {'INFO'}, report
lines, body = gcode_body()
assert body.count('G4 P250') == 1 and body.count('T1') == 1, "one snippet per instance"

# the same label and text from two instances is one stage; different text is an error
copies[1].inputs["Name"].default_value = "no_move"
copies[1].inputs["Text"].default_value = "G4 P{no_move:.0f}"
result, report = run()
assert result == {'FINISHED'} and gcode_body()[1].count('G4 P250') == 1, report
copies[1].inputs["Text"].default_value = "G4 P{no_move:.1f}"
expect_error("Two G-code stages are named")

# 16. the preview always uses the display modifier, so it shows the
#     evaluated values: on a plain mesh, a pass-through tree, and a tree that
#     changes a value stored on the mesh (the old bake onto obj.data showed
#     the stored value there, unlike the panel)
def preview_colours(obj):
    mesh = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    colour = mesh.color_attributes.get(nb.attributes.OVERLAY_ATTR)
    assert colour is not None, "no preview colour on the evaluated mesh"
    values = np.empty(len(colour.data) * 4, dtype=np.float32)
    colour.data.foreach_get("color", values)
    return values.reshape(-1, 4)

def check_preview(obj, attr):
    ok, message = ov.enable(bpy.context, obj, attr)
    assert ok, message
    assert obj.modifiers[-1].name == nb.attributes.OVERLAY_MODIFIER, "appended after the user's"
    assert obj.data.color_attributes.get(nb.attributes.OVERLAY_ATTR) is None, "nothing baked on the mesh"
    colours = preview_colours(obj)
    ov.disable(bpy.context)
    assert ov.overlay_is_live(obj) is False and not obj.modifiers.get(nb.attributes.OVERLAY_MODIFIER)
    return colours

plain = build(printer=False)
assert len(plain.modifiers) == 0
assert len(np.unique(check_preview(plain, "speed_value").round(4), axis=0)) == 4, "4 speeds, 4 colours"
linked = build()
assert len(np.unique(check_preview(linked, "speed_value").round(4), axis=0)) == 4, "pass-through tree"
override = geometry_group("Override Speed")
store = override.nodes.new("GeometryNodeStoreNamedAttribute")
store.data_type, store.domain = 'FLOAT', 'FACE'
store.inputs["Name"].default_value = "speed_value"
store.inputs["Value"].default_value = 30.0
gi, go = override.nodes["Group Input"], override.nodes["Group Output"]
override.links.new(gi.outputs[0], store.inputs["Geometry"])
override.links.new(store.outputs["Geometry"], go.inputs[0])
linked.modifiers.new("Override", 'NODES').node_group = override
assert ov.attribute_status(ov.evaluated_mesh(linked), "speed_value")[0] == (30.0, 30.0)
assert len(np.unique(check_preview(linked, "speed_value").round(4), axis=0)) == 1, \
    "the preview shows the modifier's value, as the panel does"

# LEGACY: a colour-attribute object shows the original nozzleboss panel (no
# import box), not three "missing" rows; Fan only with a Fan colour
text = panel_text(legacy_ribbon({"Flow": [1.0] * 5}))
assert "Export settings:" in text and "Start G-code:" in text and "Fan Speed:" not in text, text
assert "Import settings:" not in text and "Export Settings:" not in text, text
assert "Fan Speed:" in panel_text(legacy_ribbon({"Fan": [1.0] * 5}))
text = panel_text(build())
assert "Export Settings:" in text and "Start G-code:" not in text, "node-tree objects are unchanged"

# a wrapper or profile (cw_prop, any case, on the object or its data) is
# never a legacy file, never exported, and greys out the export button
wrapper = legacy_ribbon({"Flow": [1.0] * 5})
assert nb.legacy.uses_legacy_export(wrapper)
wrapper.data["cw_prop"] = "wrapper"
assert nb.printer.helper_tag(wrapper) == "wrapper" and not nb.legacy.uses_legacy_export(wrapper)
text = panel_text(wrapper)
assert text[:2] == ["This is the Wrapper - it isn't exported.", "Select the G-code object or the printer."], text
assert len(text) == 3 and "cw_prop" not in " ".join(text), text  # + export button
assert "Export settings:" not in text and "Flow Multiplier:" not in text, "no legacy or value rows"
wrapper.select_set(True)
assert not bpy.ops.wm.gcode_export.poll(), "export greyed out on the wrapper"
try:
    nb.export.export_gcode(bpy.context, Reporter())
except ValueError as e:
    assert "'legacy' is the Wrapper, which isn't exported" in str(e), e
else:
    raise AssertionError("exporting the wrapper must stop")
profile = bpy.data.objects.new("profile", bpy.data.curves.new("profile", 'CURVE'))
profile["cw_prop"] = "Profile"
bpy.context.scene.collection.objects.link(profile)
assert "This is the Profile - it isn't exported." in panel_text(profile)
# linked to the printer, a wrapper is still left out of the export
ribbon = build()
linked = ribbon.copy(); linked.data = ribbon.data.copy(); linked.name = "linked wrapper"
linked.data["cw_prop"] = "Wrapper"
bpy.context.scene.collection.objects.link(linked)
assert nb.export.export_targets(bpy.context, bpy.data.objects["printer"])[1] == [ribbon]

# the panel's cache is cleared by undo, redo and frame changes too (and file
# load: test_overlay.py); last, since undo invalidates the objects above
bpy.ops.ed.undo_push(message="before")
bpy.context.scene.frame_set(2)
bpy.ops.ed.undo_push(message="after")
for step in (bpy.ops.ed.undo, bpy.ops.ed.redo, lambda: bpy.context.scene.frame_set(3)):
    nb.draw_cache.get(("sentinel",), lambda: 1)
    step()
    assert ("sentinel",) not in nb.draw_cache._cache, step

nb.unregister()
for name in nb.draw_cache._HANDLERS:
    assert nb.draw_cache._clear not in getattr(bpy.app.handlers, name), f"{name} handler left"
assert not nb.draw_cache._cache
print("ALL OK")
