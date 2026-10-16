# Legacy colour files

[Back to the README](../README.md)

Files made with the original nozzleboss workflow still export.
A mesh with no printer and no `tool_path` is exported on its own from its painted
face-corner colours. The panel then shows the original nozzleboss export settings, so
the original documentation still matches:

![3_settings_nozzleboss_small](https://user-images.githubusercontent.com/17910445/151602354-62088802-b811-4b28-ba15-ea538a656761.png)  
_3 different min and max Flow/Speed multiplier settings_

- **Flow, Speed, Fan:** the colour's brightness (0 black - 1 white) maps between the
  panel's min and max values. Speed is a multiplier of Extrusion Speed. The line width is
  1.5 x Nozzle Size. A **Fan Speed** row (0-1, written as `M106 S0`-`S255`) appears only
  when the object has a `Fan` colour.
- **Tool:** where the colour changes, the text block `T0` (light, brightness 0.5 and up)
  or `T1` (dark) is inserted. A tool block may switch the fan.
- **Missing colours** read as white (max flow, max speed, `T0` once at the start), except
  Fan: without a `Fan` colour the export writes no `M106` at all, so the start code and the
  tool blocks alone control the fan.
- The file starts and ends with the `Start` and `End` text blocks. Missing text blocks are
  created with the old defaults, and the export says so.
- As in the original, the tool-colour checkbox, the two colours and the T0/T1/Start/End
  pickers don't change the export. It always uses the text blocks with those names.
- Settings saved in an old file (Nozzle Size, speeds, min/max values) load with it.
- A mesh linked to a printer never falls back to this: without `tool_path` its export stops.

The export's result says "legacy colour export" when this path was used. Legacy files
stay supported, but new features only go into the node assets.

![scratch_extrasmall](https://user-images.githubusercontent.com/17910445/150961183-9e54d273-54b1-474b-a630-9ebda929d559.gif)  
_Original nozzleboss: building extrusion paths from scratch and changing colors every other layer with the 'Tool' vertex color map_

[Back to the README](../README.md)
