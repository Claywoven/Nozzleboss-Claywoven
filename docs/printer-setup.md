# Printer setup

[Back to the README](../README.md)

The printer is an object in the scene (with `cw_prop` = `printer`) whose node tree holds
everything specific to one printer, material and nozzle: its Start and End G-code,
retraction, line width and travel speed. The Claywoven printer assets come set up; this
page explains what the export reads from them, for editing one or making your own.

## Linking an object to a printer
An object is linked to a printer when one of its enabled Geometry Nodes modifiers
references it, in either of two ways:

- an Object input on the modifier set to the printer, such as `Printer` on the builder
  modifier;
- an Object Info node with the printer selected, anywhere in the modifier's node tree,
  including inside groups, with at least one of its outputs connected. To add one, drag
  the printer object from the Outliner into the node editor, then connect it. An Object
  Info node with nothing connected is ignored, so a spare printer dropped into a tree
  doesn't take over.

The G-code object and the printer assets are brought in separately, so they start
unlinked: set the builder's **Printer** input yourself. Without a link the export stops
with "no printer is linked".

Link each object to one printer only. If an object references two printers, the export
uses the first one it finds: modifier inputs first, then connected Object Info nodes.

The linked printer gives the export its Start and End G-code, its Line Width and Travel
Speed, and the other objects that go into the same file (see
[Several objects in one file](building-your-own.md#several-objects-in-one-file)).

## Start and End G-code
On the printer object's node tree, String nodes wired into Combine Bundle inputs
`Start_G_code` and `End_G_code` supply the start and end of the file. They count only when
that Combine Bundle is wired to the `Export Settings` output of the printer's modifier tree
(through reroutes and out of groups), so a printer profile left unplugged in the tree is
ignored. Both are required: without them, with an empty String, or without a printer, the
export stops with "No Start / End code", so a file never goes out with another printer's
start code. (Only legacy colour files, which have no printer, use the `Start` and `End`
text blocks: see [Legacy colour files](legacy-colour-files.md).)

## Retraction
Two more Combine Bundle inputs on the printer, `Retract_G_code` and `Unretract_G_code`,
hold the G-code written before and after each long travel. They are wired to
`Export Settings` like Start and End, and any G-code works:

- G-code retraction, as slicers write it: `G1 E-0.4 F1800` and `G1 E0.4 F1800`
- firmware retraction, with the length and speed set in the printer's firmware (Klipper's
  `[firmware_retraction]`, Marlin's `FWRETRACT`): `G10` and `G11`

They are optional. Without either, travels don't retract and the export result says
"no retraction". With only one of the two, or one whose String is empty, the export stops,
since a retract that is never undone would starve the next line. Legacy colour files keep
`G10`/`G11`.

## Printer Settings
The export reads the printer mesh's `line_width` (mm) and `travel_speed` (mm/s) attributes
at its first point; the panel shows them under Export Settings. Both are required and must be
above 0: without them the export stops with "Printer has no Line Width" or "Printer has no
Travel Speed". The panel also shows the printer's `print_speed`, for reference only: the
moves use `speed_value`.

Line width and travel speed apply to every object linked to the printer. To put more or
less material down on one object, change its flow multiplier instead: 1.2 extrudes 20%
more than the printer's line width. The flow multiplier only changes the G-code; the
ribbon in the viewport doesn't get wider.

Next: [Inserting G-code](gcode-snippets.md)
