"""What the panel shows, kept between redraws. The N-panel redraws on every
mouse-over, and working out its values reads whole dense meshes and walks
node trees, so each value is computed once and kept until the scene changes.

Keys and values are plain names, numbers and strings, never Blender data
(a reference can go stale after undo). The handlers only clear the cache;
nothing is computed in them (the overlay rule against fitting ranges from a
depsgraph handler). The export never reads from here."""

import bpy
from bpy.app.handlers import persistent

_cache = {}


def get(key, compute):
    """The cached value for key, calling compute() on the first request."""
    try:
        return _cache[key]
    except KeyError:
        value = _cache[key] = compute()
        return value


def clear():
    _cache.clear()


@persistent
def _clear(*_args):
    _cache.clear()


# Every edit, node change, selection and visibility change goes through
# depsgraph_update_post; the others cover changes that don't.
_HANDLERS = ("depsgraph_update_post", "frame_change_post", "undo_post", "redo_post", "load_post")


def register():
    for name in _HANDLERS:
        getattr(bpy.app.handlers, name).append(_clear)


def unregister():
    for name in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if _clear in handlers:
            handlers.remove(_clear)
    _cache.clear()
