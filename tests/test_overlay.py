"""The overlay's restore state survives saving and reopening (review 1.9):
the previous viewport shading (on the screen) and active colour attribute
(on the mesh) are kept in the file, and a load handler tidies up an overlay
that is no longer live. Uses a copy of Design/dev_assets/dev_assets.blend,
which has a window and a 3D viewport even headless.

Skipped when Design/ isn't there (the public repo). Run: tests/run.sh
"""
import sys, os, importlib, shutil, tempfile
import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.basename(REPO)
SOURCE = os.path.join(REPO, "Design", "dev_assets", "dev_assets.blend")


def main():
    if not os.path.exists(SOURCE):
        print("SKIP: no", SOURCE)
        return
    here = tempfile.mkdtemp(prefix="nb_overlay_test_")

    # Registered before opening, so the load handler runs as it would for a user.
    sys.path.insert(0, os.path.dirname(REPO))
    nb = importlib.import_module(PKG)
    nb.register()
    names = nb.attributes

    def reopen(path):
        bpy.ops.wm.open_mainfile(filepath=path)

    def save(name):
        path = os.path.join(here, name)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return path

    def viewport():
        return next(nb.overlay._view3d_spaces()).shading

    def shading():
        s = viewport()
        return (s.type, s.color_type, s.light, s.show_cavity)

    def gcode():
        return bpy.data.objects["G-code.001"]

    def settings():
        return bpy.context.scene.nozzleboss

    def no_overlay_left():
        obj = gcode()
        assert obj.modifiers.get(names.OVERLAY_MODIFIER) is None, "modifier left"
        assert obj.data.color_attributes.get(names.OVERLAY_ATTR) is None, "attribute left"
        assert obj.data.attributes.active_color_name != names.OVERLAY_ATTR
        assert names.OVERLAY_PREVIOUS_COLOUR not in obj.data, "mesh stash left"
        assert not any(names.OVERLAY_PREVIOUS_SHADING in s for s in bpy.data.screens), "screen stash left"
        assert not settings().overlay_attribute and not settings().overlay_object

    def turn_on(attr):
        bpy.context.view_layer.objects.active = gcode()
        assert bpy.ops.nozzleboss.toggle_overlay(attribute=attr) == {'FINISHED'}
        assert nb.overlay.overlay_is_live(gcode()) and shading()[1:3] == ('VERTEX', 'FLAT')

    def turn_off():
        if settings().overlay_attribute:
            assert bpy.ops.nozzleboss.overlay_off() == {'FINISHED'}

    # 1. A file saved with the overlay on and no stash (before this fix):
    #    turning it off gives Blender's default shading, not flat. Built
    #    here, since dev_assets.blend is saved in whatever state it was left.
    shutil.copy(SOURCE, os.path.join(here, "source.blend"))
    reopen(os.path.join(here, "source.blend"))
    s = viewport()
    s.type, s.color_type, s.light, s.show_cavity = 'SOLID', 'OBJECT', 'STUDIO', True
    turn_on("flow_value")
    del gcode().data[names.OVERLAY_PREVIOUS_COLOUR]
    for screen in bpy.data.screens:
        if names.OVERLAY_PREVIOUS_SHADING in screen:
            del screen[names.OVERLAY_PREVIOUS_SHADING]
    reopen(save("old.blend"))
    assert settings().overlay_attribute == "flow_value", "the overlay reopens on"
    print("1. after load:", shading(), repr(settings().overlay_attribute),
          gcode().data.attributes.active_color_name)
    turn_off()
    assert shading() == ('SOLID', 'MATERIAL', 'STUDIO', False), shading()
    no_overlay_left()

    # 2. round trip: own shading and paint layer, overlay on, save, reopen,
    #    overlay still on, turn off -> both back.
    s = viewport()
    s.type, s.color_type, s.light, s.show_cavity = 'SOLID', 'OBJECT', 'STUDIO', True
    mesh = gcode().data
    mesh.color_attributes.new("Paint", 'BYTE_COLOR', 'CORNER')
    mesh.attributes.active_color_name = "Paint"
    turn_on("flow_value")
    nb.draw_cache.get(("sentinel",), lambda: 1)
    reopen(save("roundtrip.blend"))
    assert ("sentinel",) not in nb.draw_cache._cache, "file load clears the panel's cache"
    assert settings().overlay_attribute == "flow_value", "a live overlay reopens on"
    assert nb.overlay.overlay_is_live(gcode())
    assert shading()[1:3] == ('VERTEX', 'FLAT')
    turn_off()
    assert shading() == ('SOLID', 'OBJECT', 'STUDIO', True), shading()
    assert gcode().data.attributes.active_color_name == "Paint"
    no_overlay_left()
    print("2. round trip restored", shading(), "Paint")

    # 3. saved with the settings on but the modifier deleted: the load
    #    handler clears the settings and restores the shading and paint layer.
    turn_on("speed_value")
    gcode().modifiers.remove(gcode().modifiers[names.OVERLAY_MODIFIER])
    reopen(save("stale.blend"))
    no_overlay_left()
    assert shading() == ('SOLID', 'OBJECT', 'STUDIO', True), shading()
    assert gcode().data.attributes.active_color_name == "Paint"
    print("3. stale settings cleared on load")

    # 4. undo and redo: shading is UI state that undo doesn't restore, so
    #    the handler brings it in line with the overlay undo brought back.
    #    The UI pushes the step after an UNDO operator; scripts don't, so
    #    it's pushed here by hand.
    for op in (nb.overlay.NOZZLEBOSS_OT_toggle_overlay, nb.overlay.NOZZLEBOSS_OT_overlay_off):
        assert 'UNDO' in op.bl_options, op
    own = ('SOLID', 'OBJECT', 'STUDIO', True)

    def step(name):
        bpy.ops.ed.undo_push(message=name)

    def overlay_state():
        return (settings().overlay_attribute, shading(), gcode().data.attributes.active_color_name)

    off = ("", own, "Paint")
    step("before")
    turn_on("flow_value"); step("overlay on")
    on = overlay_state()
    assert on[0] == "flow_value" and on[2] == names.OVERLAY_ATTR, on
    bpy.ops.ed.undo()
    assert overlay_state() == off, "undo turns the overlay off and restores the shading"
    no_overlay_left()
    bpy.ops.ed.redo()
    assert overlay_state() == on and nb.overlay.overlay_is_live(gcode()), "redo brings it back"
    turn_off(); step("overlay off")
    bpy.ops.ed.undo()
    assert overlay_state() == on, "undoing Turn Off brings the overlay and its shading back"
    bpy.ops.ed.redo()
    assert overlay_state() == off
    no_overlay_left()

    # an unrelated undo with the overlay on keeps the user's viewport change
    turn_on("flow_value"); step("overlay on")
    gcode().location.x += 1; step("move")
    viewport().light = 'STUDIO'
    bpy.ops.ed.undo()
    assert settings().overlay_attribute == "flow_value" and viewport().light == 'STUDIO', overlay_state()
    turn_off()
    assert shading() == own, shading()
    print("4. undo and redo keep the shading in line")

    # 5. unregister with the overlay on leaves nothing behind
    turn_on("fan_value")
    nb.unregister()
    obj = gcode()
    assert obj.modifiers.get(names.OVERLAY_MODIFIER) is None
    assert obj.data.attributes.active_color_name == "Paint"
    assert names.OVERLAY_PREVIOUS_COLOUR not in obj.data
    assert not any(names.OVERLAY_PREVIOUS_SHADING in s for s in bpy.data.screens)
    assert shading() == ('SOLID', 'OBJECT', 'STUDIO', True), shading()
    assert nb.overlay._repair_after_load not in bpy.app.handlers.load_post
    assert nb.overlay._sync_after_undo not in bpy.app.handlers.undo_post
    assert nb.overlay._sync_after_undo not in bpy.app.handlers.redo_post
    print("5. unregister cleaned up")
    print("ALL OK")


main()
