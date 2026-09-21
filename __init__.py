bl_info = {
    "name": "NozzleBoss (Claywoven Fork)",
    "description": "G-code Importer/Editor/Re-Exporter",
    "author": "Jasper - Claywoven (based on Heinz Löpmeier's nozzleboss)",
    "version": (0, 0, 4),
    "blender": (5, 2, 0),   # the GN viewport overlay needs 5.2+
    "location": "3D View > nozzleboss",
    "category": "Import-Export"
}



if "bpy" in locals():
    import importlib
    importlib.reload(utils)
    importlib.reload(colormaps)
    importlib.reload(overlay)
    importlib.reload(nozzleboss)


else:
    from . import utils
    from . import colormaps
    from . import overlay
    from . import nozzleboss
    
import bpy
from bpy.props import PointerProperty
    
    
    
    


def register():
    bpy.utils.register_class(nozzleboss.NOZZLEBOSS_PT_Panel)
    bpy.utils.register_class(nozzleboss.gcode_settings)
    bpy.utils.register_class(nozzleboss.WM_OT_gcode_export)
    overlay.register()
    bpy.types.Scene.nozzleboss = bpy.props.PointerProperty(type= nozzleboss.gcode_settings)
 



def unregister():
    overlay.unregister()
    bpy.utils.unregister_class(nozzleboss.NOZZLEBOSS_PT_Panel)
    bpy.utils.unregister_class(nozzleboss.gcode_settings)
    bpy.utils.unregister_class(nozzleboss.WM_OT_gcode_export)
    del bpy.types.Scene.nozzleboss



if __name__ == "__main__":
    register()
