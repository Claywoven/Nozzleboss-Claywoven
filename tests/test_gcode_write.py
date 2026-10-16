"""The G-code writer on plain arrays, with no mesh and no Blender: E and F
changes, fan, the hop travel between runs and objects with and without
retraction text, snippets, skipped
zero-length moves, and snippet formatting. Also checks gcode_write.py
imports neither bpy nor mathutils.

Runs in Blender (tests/run.sh) or in any Python with numpy.
"""
import importlib.util
import os
import re

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, "gcode_write.py")

source = open(PATH).read()
assert not re.search(r"^\s*(import|from)\s+(bpy|mathutils)\b", source, re.M), \
    "gcode_write.py must not import bpy or mathutils"

# Loaded from its file, not through the add-on package, so no bpy is imported.
spec = importlib.util.spec_from_file_location("gcode_write", PATH)
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)


def run(name, P1, P2, E, speed, fan, new_run, zero=None, snippets=None):
    n = len(P1)
    return {"name": name, "P1": np.array(P1, float), "P2": np.array(P2, float),
            "E": np.array(E, float), "speed": np.array(speed, float), "fan": np.array(fan),
            "new_run": np.array(new_run), "zero": np.zeros(n, bool) if zero is None else np.array(zero),
            "snippets": snippets or {}}


a = run("a", P1=[(10, 0, 1), (12, 0, 1), (14, 0, 1)], P2=[(12, 0, 1), (14, 0, 1), (16, 0, 1)],
        E=[0.5, 0.5, 0.5], speed=[20, 20, 35], fan=[0, 0, 128], new_run=[True, False, False],
        snippets={1: ["M600\n"]})
b = run("b", P1=[(100, 0, 1), (102, 0, 1)], P2=[(102, 0, 1), (102, 0, 1)],
        E=[0.25, 0.0], speed=[35, 35], fan=[128, 128], new_run=[True, False], zero=[False, True])

RETRACTION = ("G1 E-0.4 F1800\n", "G1 E0.4 F1800 ; unretract\n")
txt = []
gw.write_path_moves(txt, [a, b], travel_speed=60, retraction=RETRACTION)
lines = "".join(txt).splitlines()
print("\n".join(lines))
assert lines == [
    "G90 ; absolute positioning (nozzleboss moves)",  # whatever the start G-code left
    "M83 ; relative extrusion (nozzleboss moves)",
    "; object: a",
    "G1 E-0.4 F1800", "G1 F3600", "G1 Z10.0", "G1 X10.0 Y0.0", "G1 Z1.0",
    "G1 E0.4 F1800 ; unretract",  # its F1800 is replaced by the extrusion speed next
    "G1 F1200",
    "M106 S0",
    "G1 X12.0 E0.5",              # F already set by the travel
    "M600",                       # snippet, then the modes and F are restated
    "G90", "M83",
    "G1 X14.0 E0.5 F1200",
    "M106 S128",
    "G1 X16.0 E0.5 F2100",        # 35 mm/s
    "; object: b",
    "G1 E-0.4 F1800", "G1 F3600", "G1 Z11.0", "G1 X100.0 Y0.0", "G1 Z1.0",
    "G1 E0.4 F1800 ; unretract",
    "G1 F2100",
    "G1 X102.0 E0.25",            # the zero-length move after it is skipped
], lines

# without retraction text the same travels hop with no retract (a printer
# with no Retract_G_code / Unretract_G_code)
txt = []
gw.write_path_moves(txt, [a, b], travel_speed=60)
unretracted = [l for l in lines if "E-0.4" not in l and "unretract" not in l]
assert "".join(txt).splitlines() == unretracted, txt

# a short travel (under HOP_MIN_TRAVEL) doesn't retract or hop
txt = []
gw.travel_to(txt, (0, 0, 1), (0.5, 0, 1), 1.0, 20, 60, RETRACTION)
assert "".join(txt) == "G1 F3600\nG1 X0.5 \nG1 F1200\n", txt

# the first travel states X, Y and Z even when it starts near the origin,
# where a 0 coordinate used to be left out (review 1.6)
c = run("c", P1=[(0.5, 0, 0.2)], P2=[(1.5, 0, 0.2)], E=[0.1], speed=[20], fan=[0], new_run=[True])
txt = []
gw.write_path_moves(txt, [c], travel_speed=60)
lines = "".join(txt).splitlines()
assert lines[3:8] == ["G1 F3600", "G1 Z10.0", "G1 X0.5 Y0.0", "G1 Z0.2", "G1 F1200"], lines

# a new run that starts where the last move ended writes no bare "G1"
d = run("d", P1=[(10, 0, 1), (12, 0, 1)], P2=[(12, 0, 1), (14, 0, 1)], E=[0.5, 0.5],
        speed=[20, 30], fan=[0, 0], new_run=[True, True])
txt = []
gw.write_path_moves(txt, [d], travel_speed=60)
lines = "".join(txt).splitlines()
assert not any(l.strip() == "G1" for l in lines), lines
assert lines[-3:] == ["G1 X12.0 E0.5", "G1 F1800", "G1 X14.0 E0.5"], lines

# fan None (a legacy file without a Fan colour): no M106, the snippets control the fan
e = run("e", P1=[(10, 0, 1), (12, 0, 1)], P2=[(12, 0, 1), (14, 0, 1)], E=[0.5, 0.5],
        speed=[20, 20], fan=[0, 0], new_run=[True, False], snippets={1: ["M107\n"]})
e["fan"] = None
txt = []
gw.write_path_moves(txt, [e], travel_speed=60)
lines = "".join(txt).splitlines()
assert not any(l.startswith("M106") for l in lines) and "M107" in lines, lines

# F is rounded, not truncated: 0.2 stored as 0.19999 mm/s is F12, not F11
assert gw.feed_rate(np.float32(0.01) * 20) == 12 and gw.feed_rate(29.99999) == 1800
f = run("f", P1=[(0, 0, 1)], P2=[(2, 0, 1)], E=[0.5], speed=[0.19999], fan=[0], new_run=[True])
txt = []
gw.write_path_moves(txt, [f], travel_speed=60)
assert "G1 F12" in "".join(txt).splitlines(), txt

# snippet placeholders: bare floats round to 4 decimals, a spec wins, ints stay ints
assert gw.format_snippet("w", "G1 X{a}", {"a": np.float64(1.0591715574264526)}) == "G1 X1.0592\n"
assert gw.format_snippet("w", "{a:.2f}", {"a": 1.0591715574264526}) == "1.06\n"
assert gw.format_snippet("w", "T{a}", {"a": 1}) == "T1\n"
assert gw.format_snippet("w", "G1 E{a} F{b}", {"a": 0.5, "b": 300}) == "G1 E0.5 F300\n"
assert gw.format_snippet("w", "M600", {}) == "M600\n"
for values, available in (({"a": 1.0, "b": 2}, "available: {a}, {b}"), ({}, "available: none")):
    try:
        gw.format_snippet("w", "{other}", values)
    except ValueError as e:
        assert "uses {other}; " + available in str(e), e
    else:
        raise AssertionError("an unknown placeholder must be an error")

print("ALL OK")
