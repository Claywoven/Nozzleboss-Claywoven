# Inserting G-code

[Back to the README](../README.md)

G-code snippets put your own G-code into the file at faces you select in the node tree:
a layer-change marker, a pause, a temperature change, extruding in place. They replace
the `T0`/`T1` text blocks of the original nozzleboss, and they are saved with the
object, so there are no separate text files to keep track of.

## The G-code Snippet Insert modifier
In the Claywoven assets the snippet stages ("Add G-code" groups) sit in their own
modifier, `G-code Snippet Insert`, after the builder modifier. Keeping them separate means
their G-code survives applying the builder: the stages recalculate their masks from the
attributes stored on the mesh, and their String nodes stay readable.

- The stages are muted when the asset comes in. The modifier has to be enabled, and its
  stages unmuted, for their G-code to be exported.
- Its node group has a `Printer` output. To link the object to a printer from this
  modifier, drag the printer object from the Outliner into the `G-code Snippet Insert` node
  group. Blender adds an Object Info node with the printer selected; connect its Geometry
  to the `Printer` output. Until it is connected, the export ignores it. This is how an
  object stays linked after the builder is applied (see
  [Editing values by hand](editing-by-hand.md)).

## G-code at masked faces
Each "Add G-code" stage in the node group marks faces with a mask, named after the
stage's Name and its order (for example `extrude_mask_p03`), and stores its values on
the same faces: `extrude_value`, plus any extras such as `extrude_value_speed`. The
G-code to insert lives in the same stage group: a **String** node wired (directly or
through reroutes) into a Combine Bundle input named `G_code_string`. A `Label` input on
the same bundle, wired from the stage's Name, ties the text to the mask, e.g.

    G1 E{extrude:.2f} F{speed}

- Placeholders are the stage's values, taken at the move where the mask starts: `{extrude}`
  is `extrude_value` (the stage's Name) and `{speed}` is `extrude_value_speed`. Other
  attributes on the mesh can't be used directly; store a copy as a value of the stage
  first (e.g. `extrude_value_layer_height` for `{layer_height}`).
- A placeholder with no matching value stops the export. Values the text doesn't use are
  ignored, and a stage needs no values at all for fixed codes such as `M600`.
- Values are rounded to 4 decimals like the move lines.
  Format specs like `:.2f` work, as in the Format String node, and override the rounding.
  Whole-number (Int) values print without decimals, so `T{tool}` gives `T1`.
  Write `{{` and `}}` for literal braces.
- A Name can't contain `_mask` or `_value`, or be `flow`, `speed` or `fan` (their
  `_value` attributes are the print values).
- The text and the Name must be typed into a String node, or into a text input on the
  stage's group node (or the modifier) that `G_code_string` or `Label` is wired to through
  the Group Input. Strings built by other nodes (Format String, Join Strings) can't be
  read by the exporter.
- One stage group can be reused: each copy is read on its own, so copies with different
  text (and Names) each give their own snippet. Copies with the same label and text count
  as one stage; the same label with different text is an error.
- Each stage needs its own Name. Two masks with the same Name stop the export with an error.
- Stages that are muted, or whose Geometry output isn't connected, are ignored, as are
  disabled modifiers. The same rules decide which objects are linked to a printer. The export
  warns about a mask with no G-code and about G-code whose Name has no mask.

## Where the text goes
- The text is inserted once where the mask starts; consecutive masked faces don't repeat it,
  and neither does a travel inside a masked area. A mask usually selects one face, but a
  mask over a whole layer works too: its text is written once, at the start of that layer.
- When several masks start on the same move, they are inserted in the order of their
  `_pNN`, which is the order the stages were added.
- It is inserted after any travel and fan change, with the nozzle at the start of the first
  masked move - so it can extrude or move relative to that point.
- After each snippet the export writes `G90` and `M83` and the next move restates its feed
  rate, so a snippet may switch to relative positioning or change F. Anything else it changes
  (for example the flow percentage) it must put back itself.

The panel lists every stage with how many times its G-code is written, and the viewport
preview can show a stage's values on its masked faces (see the
[More Details](more-details.md#viewport-preview)).

Next: [Building your own node tree](building-your-own.md)
