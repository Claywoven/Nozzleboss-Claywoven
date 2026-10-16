# Editing values by hand

[Back to the README](../README.md)

To change values on faces a node tree can't easily select, apply the builder modifiers
and edit the stored attributes: in Edit Mode with Face Select, pick the attribute in the
Attributes panel, select the faces, then use **Mesh > Set Attribute**. Keep
`G-code Snippet Insert` as a modifier; don't apply it.

Applying is one-way. Keep a hidden copy of the object if you might want to rebuild it
from the node tree. After applying:

- **The printer link goes with the builder.** Add the printer back in
  `G-code Snippet Insert` (see
  [The G-code Snippet Insert modifier](gcode-snippets.md#the-g-code-snippet-insert-modifier)).
  This brings back the Start and End G-code and puts the object back into its printer's
  export with the other objects.
- **Settings the node tree worked out are now fixed on the mesh**: the speeds
  (`speed_value`), flow and fan values, layer heights and the path itself. Changing the
  printer's Print Speed, Layer Height or other build settings no longer changes this
  object. Edit the values on the mesh instead.
- **Line Width and Travel Speed are still read from the printer when you export.** Line
  width sets how much filament each move extrudes (E), so changing it changes E even
  though the ribbon in the viewport keeps the width it was built with. Keep the printer's
  Line Width matching the width the object was built with.
- **If the object has a base, don't change the printer's Line Width after applying.** The
  base's lines were spaced for the line width it was built with, and the applied mesh can
  no longer adjust to a new one. A wider line over-extrudes the base, a narrower one
  under-extrudes it.
- `G-code Snippet Insert` recalculates its masks on every update, so a mask edited by hand
  under the same Name as one of its stages is overwritten.

Next: [Legacy colour files](legacy-colour-files.md)
