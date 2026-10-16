"""LEGACY - export real colour-attribute files (Design/legacy  test files/)
and check the moves against what the old duplicate-and-convert export wrote
for them: the same number of moves and total E (to rounding), with nothing
left behind in the file.

Skipped when the files aren't there (the public repo). Run: tests/run.sh
Remove with legacy.py.
"""
import sys, os, importlib, re, shutil, tempfile
import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.basename(REPO)
FILES = os.path.join(REPO, "Design", "legacy  test files")

# file -> {object: (moves, total E) from the old export, or the error expected}
EXPECTED = {
    "legacy test1.blend": {
        "G-Code.001": (750, 820.0073),
        "GCode_from_curve.001": (37878, 253773.6442),   # no colours at all: the white defaults
    },
    "legacy test2.blend": {
        "Gcode.base.generator": (3600, 21899.8191),     # its modifiers drop the colours
        "Gcode.wall.generator": "is not an edge of a face",  # Subdivision on: the old export failed too
    },
    "legacy test3.blend": {
        "Spiral.001": (1861, 536.7621),
        "Spiral mesh.001": "is hidden",                  # in a hidden collection
    },
}


class Reporter:
    def __init__(self):
        self.reports = []

    def report(self, kind, message):
        self.reports.append((kind, message))


def main():
    if not os.path.isdir(FILES):
        print("SKIP: no", FILES)
        return
    sys.path.insert(0, os.path.dirname(REPO))
    here = tempfile.mkdtemp(prefix="nb_legacy_test_")
    for file_name, objects in EXPECTED.items():
        blend = os.path.join(here, file_name)
        shutil.copy(os.path.join(FILES, file_name), blend)
        bpy.ops.wm.open_mainfile(filepath=blend)
        nb = importlib.import_module(PKG)
        nb.register()
        try:
            check_file(nb, blend, objects)
        finally:
            nb.unregister()


def check_file(nb, blend, objects):
    context = bpy.context
    gcode = os.path.splitext(blend)[0] + ".gcode"
    for name, expected in objects.items():
        obj = bpy.data.objects[name]
        assert nb.legacy.uses_legacy_export(obj), name
        for other in context.view_layer.objects:
            other.select_set(False)
        context.view_layer.objects.active = obj
        counts = (len(bpy.data.objects), len(bpy.data.meshes))
        if os.path.exists(gcode):
            os.remove(gcode)
        rep = Reporter()
        try:
            result = nb.export.export_gcode(context, rep)
        except ValueError as e:
            print(f"{name}: {e}")
            assert isinstance(expected, str) and expected in str(e), (name, e)
            assert not os.path.exists(gcode)
            continue
        assert not isinstance(expected, str), f"{name} should fail with '{expected}'"
        assert result == {'FINISHED'}, (name, rep.reports)
        assert (len(bpy.data.objects), len(bpy.data.meshes)) == counts, f"{name}: export must not add data"
        assert "legacy colour export" in rep.reports[-1][1], rep.reports

        lines = open(gcode).read().splitlines()
        body = lines[lines.index(f"; object: {name}"):]
        ext = [l for l in body if re.match(r'G1 [XYZ].* E', l)]
        total_e = sum(float(re.search(r' E(-?[\d.]+)', l).group(1)) for l in ext)
        moves, old_e = expected
        print(f"{name}: {len(ext)} moves, total E {total_e:.4f} (old {old_e})")
        assert len(ext) == moves, (name, len(ext), moves)
        assert abs(total_e - old_e) < 0.01, (name, total_e, old_e)


main()
