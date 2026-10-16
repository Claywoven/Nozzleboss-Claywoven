# Building your own node tree

[Back to the README](../README.md)

The export doesn't need the Claywoven assets: it reads a few attributes from the
evaluated mesh, so any node tree (or mesh) that writes them can be exported. This page
lists them and explains how they become G-code moves, which applies to the assets too.

## The export mesh
The exporter reads these attributes from the evaluated mesh. The node assets write them:

| Attribute | Domain, type | Meaning |
|---|---|---|
| `tool_path` | Edge, Boolean | The nozzle path: the top edge of the ribbon |
| `use_for_height` | Edge, Boolean | The vertical edge at each path point; its length is the layer height |
| `face_number` | Face, Integer | Counts from 1: face *k* holds the values for the move from vertex *k*-1 to vertex *k* |
| `flow_value` | Face, Float | Flow multiplier |
| `speed_value` | Face, Float | Print speed in mm/s |
| `fan_value` | Face, Integer | Fan speed, 0-255 |

- Each `tool_path` edge must join consecutive vertices (0-1, 1-2, ...). A gap in the
  numbering starts a new run, reached by a travel move.
- Mesh operations such as subdivide or rip can scramble the vertex order. The export then
  stops and names the edge.
- A node tree that outputs instances needs a Realize Instances node at the end.
- Enabled Solidify modifiers are switched off while the mesh is read, then back on, so a
  Solidify used to preview the wall thickness doesn't change the print.
- The object also needs a printer: see [Printer setup](printer-setup.md).

## Flow, speed and fan
- **Extrusion (E):** move length x layer height x line width x `flow_value`, converted to
  mm of 1.75 mm filament. The line width is the printer's `line_width` (see
  [Printer Settings](printer-setup.md#printer-settings)).
- **Speed (F):** `speed_value` in mm/s, written as mm/min. It must be at least 1/60 mm/s (F1).
- **Fan:** `M106 S<fan_value>` wherever the value changes. Values outside 0-255 are
  clipped, with a warning.
- **Checks:** the export stops on values that would write broken or dangerous G-code, and
  names the `face_number`. That covers a value that isn't a number, a negative flow, a
  speed below F1, and a path below Z 0.

## Travel moves
A travel longer than 1 mm retracts and lifts to 10 mm above the highest point printed so
far. It then moves across, lowers, and unretracts. The retract and unretract are the
printer's own G-code (see [Retraction](printer-setup.md#retraction)); without it the travel
still lifts but doesn't retract. Shorter travels move directly, without retracting.

## Several objects in one file
Export from the printer object or any object linked to it. Every visible mesh object
whose modifiers reference that printer goes into one G-code file, with one Start and
one End block. Objects print one after another, sorted by the Z of their last move,
lowest first. Between objects the nozzle hops above everything printed so far.

- Select the printer to see the whole print in the panel: the objects in print order
  and each value's min - max over all of them (read-only; select an object to preview it).
- Hidden objects are left out.
- The hop only clears the nozzle. The print head body and gantry can still hit an
  earlier object when printing next to it, so leave enough space between objects.

Next: [Editing values by hand](editing-by-hand.md)
