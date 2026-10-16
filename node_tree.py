"""One walker for an object's Geometry Nodes trees, shared by the printer
lookup and the G-code strings, so both follow the same rules:

- only enabled NODES modifiers count, never the overlay's display modifier;
- group nodes that are muted, or whose Geometry output goes nowhere, are
  skipped;
- each group node *instance* is visited, not each tree, so a group used twice
  is read twice and a value fed from a Group Input is traced up through the
  parent group node, and at the top to the modifier's input.

The trees are read, never evaluated, so only literal values are found: a
String or Object node, or a socket's own value. Links are followed forward
only through reroutes and out of groups (reaches_output).
"""

from collections import namedtuple

from .attributes import OVERLAY_MODIFIER


# Blender forbids recursive groups; this only guards against a bad file.
MAX_DEPTH = 64

STRING_SOURCES = {"FunctionNodeInputString": "string"}
OBJECT_SOURCES = {"GeometryNodeInputObject": "object"}


# One visit of a tree: path is the group nodes from the modifier's tree down
# to this one (empty at the top).
Scope = namedtuple("Scope", "tree modifier path")


def live_modifiers(obj):
    """The object's enabled NODES modifiers, without the overlay's."""
    return [mod for mod in obj.modifiers
            if mod.type == 'NODES' and mod.node_group is not None and mod.show_viewport
            and mod.name != OVERLAY_MODIFIER]


def is_live_group(node):
    """A group node that can contribute: not muted, and if it outputs
    geometry, that geometry is used."""
    if node.mute or getattr(node, "node_tree", None) is None:
        return False
    geometry = [s for s in node.outputs if s.type == 'GEOMETRY']
    return not geometry or any(s.is_linked for s in geometry)


def iter_scopes(obj):
    """A Scope for each live modifier's tree and every live group instance
    nested in it, depth first."""
    for mod in live_modifiers(obj):
        stack = [Scope(mod.node_group, mod, ())]
        while stack:
            scope = stack.pop()
            yield scope
            if len(scope.path) >= MAX_DEPTH:
                continue
            for node in list(scope.tree.nodes)[::-1]:
                if node.bl_idname == "GeometryNodeGroup" and is_live_group(node):
                    stack.append(Scope(node.node_tree, mod, scope.path + (node,)))


def describe(scope, node=None):
    """'Main' > 'Add G-code.001' / 'Combine Bundle' - where a node sits."""
    trail = " > ".join([f"'{scope.modifier.node_group.name}'"]
                       + [f"'{group.name}'" for group in scope.path])
    return trail + (f" / '{node.name}'" if node is not None else "")


def _parent_scope(scope):
    path = scope.path[:-1]
    tree = path[-1].node_tree if path else scope.modifier.node_group
    return Scope(tree, scope.modifier, path)


def reaches_output(socket, scope, name):
    """True when the output socket is wired to the modifier tree's Group
    Output socket called name: through reroutes, and out of each group
    through its Group Output to the group node's matching output."""
    stack = [(socket, scope)]
    while stack:
        socket, scope = stack.pop()
        for link in socket.links:
            if link.is_muted or not link.is_valid:
                continue
            node = link.to_node
            if node.bl_idname == "NodeReroute":
                stack.append((node.outputs[0], scope))
            elif node.bl_idname == "NodeGroupOutput" and node.is_active_output:
                if not scope.path:
                    if link.to_socket.name == name:
                        return True
                    continue
                identifier = link.to_socket.identifier
                out = next((s for s in scope.path[-1].outputs if s.identifier == identifier), None)
                if out is not None:
                    stack.append((out, _parent_scope(scope)))
    return False


def resolve(socket, scope, sources, where):
    """The literal value feeding socket: an allowed source node (through
    reroutes), the socket's own value, or - through a Group Input - the value
    on the parent group node, or at the top the modifier's input."""
    while True:
        links = [link for link in socket.links if not link.is_muted]
        if not links:
            return socket.default_value
        link = links[0]
        node = link.from_node
        if node.bl_idname == "NodeReroute":
            socket = node.inputs[0]
        elif node.bl_idname in sources:
            return getattr(node, sources[node.bl_idname])
        elif node.bl_idname == "NodeGroupInput":
            identifier = link.from_socket.identifier
            if not scope.path:
                item = getattr(scope.modifier.properties.inputs, identifier, None)
                if item is None:
                    raise ValueError(f"{where}: can't read modifier input '{link.from_socket.name}'")
                return item.value
            group = scope.path[-1]
            socket = next((s for s in group.inputs if s.identifier == identifier), None)
            if socket is None:
                raise ValueError(f"{where}: can't read input '{link.from_socket.name}' "
                                 f"of '{group.name}'")
            scope = _parent_scope(scope)
        else:
            kind = "a String node" if sources is STRING_SOURCES else "an Object node"
            raise ValueError(f"{where}: must come from {kind}, "
                             f"not '{node.name}' ({node.bl_label})")
