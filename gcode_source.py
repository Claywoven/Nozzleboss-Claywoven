"""G-code text taken from String nodes in an object's Geometry Nodes tree.

Strings can't be stored on geometry yet, so the text is read from the node
tree rather than the evaluated mesh. A String node wired into a Combine Bundle
input carries it:

  G_code_string  - a snippet inserted where a mask starts. The Label item
                   in the same bundle (wired from the staging group's Name)
                   ties it to <label>_mask_pNN on the mesh; its placeholders
                   are the stage's values (attributes.masked_stages).
  Start_G_code / End_G_code, Retract_G_code / Unretract_G_code - on the
                   printer object, counted only when the Combine Bundle is
                   wired to the "Export Settings" output of the printer's
                   modifier tree.

The bundle itself is never evaluated. The trees are walked by node_tree:
only enabled modifiers and live groups count, each group instance is read
on its own, and a string fed from a Group Input is taken from the parent
group node (or the modifier).
"""

import re
import string

from . import node_tree


from .attributes import (END_G_CODE, EXPORT_SETTINGS, G_CODE_STRING, LABEL, NAME_INFIXES,
                         RETRACT_G_CODE, START_G_CODE, UNRETRACT_G_CODE)


def bundle_strings(obj, item_name, output=None):
    """[(text, where)] for every Combine Bundle input called item_name in the
    object's live node trees, once per group instance. With output, only the
    bundles wired to the modifier tree's output of that name."""
    found = []
    for scope in node_tree.iter_scopes(obj):
        for node in scope.tree.nodes:
            if node.bl_idname != "NodeCombineBundle" or node.mute:
                continue
            socket = node.inputs.get(item_name)
            if socket is None:
                continue
            if output is not None and not node_tree.reaches_output(node.outputs[0], scope, output):
                continue
            where = f"{item_name} on {node_tree.describe(scope, node)}"
            found.append((node_tree.resolve(socket, scope, node_tree.STRING_SOURCES, where), where))
    return found


BRACE_HINT = ("Placeholders need curly brackets on both sides, e.g. {name}.",
              "Write {{ or }} for a literal brace.")


def snippet_error(where, *lines):
    """A ValueError for a snippet, one point per line: Blender's error popup
    shows each line on its own row."""
    return ValueError("\n".join((where + ":",) + lines))


def _check_braces(template, where):
    """Stop on a placeholder that isn't closed, or a '}' that wasn't opened,
    naming the line - the format parser only says what it expected."""
    for number, line in enumerate(template.splitlines(), 1):
        k = 0
        while k < len(line):
            pair = line[k:k + 2]
            if pair in ("{{", "}}"):
                k += 2
                continue
            if line[k] == "{":
                # the matching '}', allowing one nested field in a format spec: {a:{w}}
                depth, end = 0, -1
                for j in range(k, len(line)):
                    depth += {"{": 1, "}": -1}.get(line[j], 0)
                    if depth == 0:
                        end = j
                        break
                if end < 0:
                    typo = next((c for c in ")]>" if c in line[k:]), None)
                    guess = (f"Did you type '{typo}' instead of '}}'?",) if typo else ()
                    raise snippet_error(where, f"A '{{' is not closed on line {number}:",
                                        "    " + line.strip(), *guess, *BRACE_HINT)
                k = end + 1
                continue
            if line[k] == "}":
                typo = next((c for c in "([<" if c in line[:k]), None)
                guess = (f"Did you type '{typo}' instead of '{{'?",) if typo else ()
                raise snippet_error(where, f"A '}}' has no opening '{{' on line {number}:",
                                    "    " + line.strip(), *guess, *BRACE_HINT)
            k += 1


def placeholders(template, where):
    """The placeholder names in a snippet: {speed:.2f} and {speed} are both 'speed'."""
    _check_braces(template, where)
    def fields(text):
        for _, field, spec, _ in string.Formatter().parse(text):
            if field is not None:
                yield field
                yield from fields(spec or "")  # {a:.{digits}f}
    try:
        fields = list(fields(template))
    except ValueError as e:
        raise snippet_error(where, f"{e}.", *BRACE_HINT)
    if "" in fields:
        raise snippet_error(where, "'{}' needs a name, e.g. {extrude}.")
    return {re.split(r"[.\[]", f, maxsplit=1)[0] for f in fields}


def masked_templates(obj):
    """{label: (template, where)} for every G_code_string in the object's
    tree, labelled by the Label item of the same bundle. The same label and
    text from two group instances is one stage (e.g. one stage on two
    branches that are joined); the same label with different text is an
    error."""
    templates = {}
    for scope in node_tree.iter_scopes(obj):
        for node in scope.tree.nodes:
            if node.bl_idname != "NodeCombineBundle" or node.mute:
                continue
            socket = node.inputs.get(G_CODE_STRING)
            if socket is None:
                continue
            where = f"{G_CODE_STRING} on {node_tree.describe(scope, node)}"
            label_socket = node.inputs.get(LABEL)
            if label_socket is None:
                raise ValueError(f"{where}: no {LABEL} in the bundle - wire the stage's Name to a "
                                 f"{LABEL} item")
            label = node_tree.resolve(label_socket, scope, node_tree.STRING_SOURCES,
                                      f"{LABEL} on {node_tree.describe(scope, node)}").strip()
            if not label:
                raise ValueError(f"{where}: the {LABEL} is empty - give the stage a Name")
            bad = [infix for infix in NAME_INFIXES if infix in label]
            if bad:
                raise ValueError(f"{where}: the Name '{label}' can't contain '{bad[0]}'")
            template = node_tree.resolve(socket, scope, node_tree.STRING_SOURCES, where)
            where = f"The G-code for '{label}' in {node_tree.describe(scope, node)}"
            if not template.strip():
                raise snippet_error(where, "The String node is empty.")
            placeholders(template, where)
            if label in templates and templates[label][0] == template:
                continue
            if label in templates:
                raise ValueError(f"Two G-code stages are named '{label}' with different text:\n"
                                 f"{templates[label][1]}\n{where}")
            templates[label] = (template, where)
    return templates


def printer_string(printer, name):
    """The text of the printer's String for bundle item name, or None when
    absent. Only bundles wired to the Export Settings output count, so an
    unplugged printer profile left in the tree is ignored."""
    found = bundle_strings(printer, name, EXPORT_SETTINGS) if printer is not None else []
    if len(found) > 1:
        raise ValueError(f"More than one {name}: " + "; ".join(w for _, w in found))
    return found[0][0] if found else None


def start_end(printer):
    """(start, end) text from the printer's tree, each None when absent."""
    return printer_string(printer, START_G_CODE), printer_string(printer, END_G_CODE)


def retraction(printer):
    """(retract, unretract) text from the printer's tree, each ending in a
    newline, or None when the printer has neither: travels then don't
    retract. An empty String counts as absent. Only one of the two stops the
    export, since every retract must be undone."""
    pair = [printer_string(printer, name) for name in (RETRACT_G_CODE, UNRETRACT_G_CODE)]
    pair = [text if text and text.strip() else None for text in pair]
    if pair == [None, None]:
        return None
    if None in pair:
        have, missing = ((RETRACT_G_CODE, UNRETRACT_G_CODE) if pair[1] is None
                         else (UNRETRACT_G_CODE, RETRACT_G_CODE))
        raise ValueError(f"The printer '{printer.name}' has {have} but no {missing}: wire both to "
                         f"'{EXPORT_SETTINGS}', or neither for no retraction")
    return tuple(text if text.endswith('\n') else text + '\n' for text in pair)
