import bpy
import time
import os
from mathutils import Vector
import numpy as np


from bpy.props import (StringProperty,
                       BoolProperty,
                       PointerProperty,
                       FloatProperty,
                       IntProperty,
                       FloatVectorProperty
                       )
from bpy.types import (Panel,
                       Menu,
                       Operator,
                       PropertyGroup,
                       )
                       
                       
from .utils import *
from . import overlay


def _overlay_cavity_update(self, context):
    overlay.apply_cavity(self.overlay_cavity)



class gcode_settings(bpy.types.PropertyGroup): 
    
    flow_map: StringProperty(
        name="Flow Multiplier weightmap - grayscale of vertex color gets mapped to min and max range",
        default="Flow"
        )
    speed_map: StringProperty(
        name="Speed Multiplier weightmap - grayscale of vertex color gets mapped to min and max range",
        default="Speed"
        )

    fan_map: StringProperty(
        name="Fan Speed weightmap - grayscale of vertex color gets mapped to min and max range",
        default="Fan"
        )
        
         
    T0: StringProperty(
        name="Custom G-code per color",
        default="T0"
        )
    T1: StringProperty(
        name="Custom G-code per color",
        default="T1"
        )
        
    start: StringProperty(
        name="Start G-code to append to every exported G-code file",
        default="Start"
        )
    end: StringProperty(
        name="End G-code to append to every exported G-code file",
        default="End"
        )

    split_layers: BoolProperty(
        name="Split into Layers",
        description="Save every layer as single Object in Collection",
        default = False
        )

    subdivide: BoolProperty(
        name="Subdivide",
        description="Subdivide Segments that are bigger then value, needed to increase resolution when modifyng the mesh",
        default = False
        )
        
        
    tool_color: BoolProperty(
        name="Toolchange based on color:",
        description="Will insert textblock associated with that color. Color value is taken from 'Tool' Vertex Color map",
        default = False
        )

    max_segment_size: FloatProperty(
        name = "",
        description = "Only Segments bigger then this value get subdivided, too small values here can cause performance issues",
        default = 1,
        min = 0.1,
        max = 999.0
        )

    min_flow: FloatProperty(
        name = "",
        description = "Factor the extrusion value is multiplied with based on the underlying vertex color of that segment",
        default = 0.4
        )
    max_flow: FloatProperty(
        name = "",
        description = "Factor the extrusion value is multiplied with based on the underlying vertex color of that segment",
        default = 1.
        )
        
    min_speed: FloatProperty(
        name = "",
        description = "Factor the extrusion speed value is multiplied with based on the underlying vertex color of that segment",
        default = 0.2
        )
    max_speed: FloatProperty(
        name = "",
        description = "Factor the extrusion speed value is multiplied with based on the underlying vertex color of that segment",
        default = 1.
        )

    min_fan: FloatProperty(
        name = "",
        description = "Fan speed (0-1, mapped to M106 S0-S255) at the low end of the underlying vertex color of that segment",
        default = 0.,
        min = 0.,
        max = 1.
        )
    max_fan: FloatProperty(
        name = "",
        description = "Fan speed (0-1, mapped to M106 S0-S255) at the high end of the underlying vertex color of that segment",
        default = 1.,
        min = 0.,
        max = 1.
        )

    overlay_attribute: StringProperty(
        name="",
        description="Attribute currently previewed in the viewport, empty when the overlay is off",
        default=""
        )

    overlay_object: StringProperty(
        name="Overlay Object",
        description="Object carrying the overlay; it follows the active object",
        default="",
        )
    overlay_cavity: BoolProperty(
        name="Cavity",
        description="Add edge definition - flat lighting is required for the gradient to stay readable, "
                    "but it removes form cues",
        default=True,
        update=_overlay_cavity_update
        )

    nozzle_diameter: FloatProperty(
        name = "",
        description = "Nozzle diameter - used to calculate the extrusion width",
        default = 0.4
        )
                    
    travel_speed: IntProperty(
        name = "",
        description = "In mm/s. Exported G-code will have mm/min though",
        default = 60
        )
        
    extrusion_speed: IntProperty(
        name = "",
        description = "In mm/s. Exported G-code will have mm/min though",
        default = 30
        )
        

    color_T0: FloatVectorProperty(
        name="Color",
        description="When ever this color is reached on export, add custom G-code in T0 textblock to export file",
        subtype='COLOR', size=3, min=0, max=1, precision=3, step=0.1,
        default=(1.0, 1.0, 1.0)
    )
    
    color_T1: FloatVectorProperty(
        name="Color",
        description="When ever this color is reached on export, add custom G-code in T0 textblock to export file",
        subtype='COLOR', size=3, min=0, max=1, precision=3, step=0.1,
        default=(0.0, 0.0, 0.0)
    )





class NOZZLEBOSS_PT_Panel(bpy.types.Panel):
 
    bl_label = "NozzleBoss (Claywoven fork)"
    bl_idname = 'NOZZLEBOSS_PT_Panel' 
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "nozzleboss" 
    
    
#   @classmethod
#   def poll(cls, context):
#       try: return context.object.type in ('MESH')
#        except: return False


    # Width of the label column; the min/max button takes the rest.
    label_width = 1 / 3

    def draw_report_header(self, col):
        split = col.split(factor=self.label_width)
        split.label(text=" ")
        header = split.row()
        for text in ("min:", "max:"):
            sub = header.row()
            sub.alignment = 'CENTER'
            sub.label(text=text)

    def draw_report_row(self, col, context, mesh, attr_name, label):
        """A right-aligned label and one button reporting the attribute's
        min - max, which toggles its viewport overlay. Problems (missing
        attribute or mask) show as a red button instead."""
        split = col.split(factor=self.label_width)
        name = split.row()
        name.alignment = 'RIGHT'
        name.label(text=label)

        value = split.row(align=True)
        minmax, problem = overlay.attribute_status(mesh, attr_name)
        if problem is not None:
            value.alert = True
            value.operator("nozzleboss.toggle_overlay", text=problem).attribute = attr_name
            return

        if overlay.is_integer_attribute(mesh, attr_name):
            text = f"{round(minmax[0])}  -  {round(minmax[1])}"
        else:
            text = f"{minmax[0]:.3f}  -  {minmax[1]:.3f}"
        button = value.operator(
            "nozzleboss.toggle_overlay",
            text=text,
            depress=overlay.is_showing(context, context.object, attr_name),
        )
        button.attribute = attr_name

    def draw(self, context): 
        layout = self.layout 

        scn = context.scene
        nozzleboss = scn.nozzleboss 
        obj = context.object
        

        # Import UI removed — plugin now focuses on export only

        
        col = layout.box().column(align=True)
        col.label(text="Export settings:")
        col.prop(nozzleboss, "nozzle_diameter", text='Nozzle Size')
        col.prop(nozzleboss, "travel_speed", text='Travel Speed')
        col.prop(nozzleboss, "extrusion_speed", text='Extrusion Speed')

        col.separator(factor=1.5)

        mesh = overlay.evaluated_mesh(obj)

        self.draw_report_header(col)
        for attr_name, label, _bounds, _integer in overlay.ATTRIBUTES:
            self.draw_report_row(col, context, mesh, attr_name, label)

        # Masked values only exist when the node tree makes them, so the
        # section appears only then.
        masked = overlay.masked_attributes(mesh) if mesh else []
        if masked:
            col.separator(factor=1.5)
            self.draw_report_header(col)
            for attr_name, base in masked:
                self.draw_report_row(col, context, mesh, attr_name, base + ":")

        if nozzleboss.overlay_attribute:
            col.separator(factor=1.5)
            overlay.draw_overlay_settings(col, context)

        col.separator(factor=2)
        row=col.row(align=True) 
        #row.label(text='Start G-code:')
        row.scale_y= 1.5# only works on operators and not on labels i think
        row.operator("wm.gcode_export")
        

        
        
    

        # import functionality removed
    
    
def export_gcode(context, operator=None):
  
      #auto create textblocks to, need if you model from scratch (if you import existing they get created in parser)
    if not bpy.data.texts.get('T0'):
        bpy.data.texts.new('T0')
        bpy.data.texts['T0'].write('T0; switch to extruder T0 (any G-code macro can be passed here)\n')
    if not bpy.data.texts.get('T1'):
        bpy.data.texts.new('T1')
        bpy.data.texts['T1'].write('T1; switch to extruder T1 (any G-code macro can be passed here)\n')
    if not bpy.data.texts.get('Start'):
        bpy.data.texts.new('Start')
        bpy.data.texts['Start'].write(';nozzleboss\n')
        bpy.data.texts['Start'].write('G28 ;homing\n')
        bpy.data.texts['Start'].write('M104 S180 ;set hotend temp\n')
        bpy.data.texts['Start'].write('M190 S50 ;wait for bed temp\n')
        bpy.data.texts['Start'].write('M109 S200 ;wait for hotendtemp\n')
        bpy.data.texts['Start'].write('M83; relative extrusion mode (REQUIRED)\n')

    if not bpy.data.texts.get('End'):
        bpy.data.texts.new('End')
        bpy.data.texts['End'].write('G10 ;retract\n')
        bpy.data.texts['End'].write('M104 S0 ;deactivate hotend\n')
        bpy.data.texts['End'].write('M140 S0 ;deactivate bed\n')
        bpy.data.texts['End'].write('G28 ;homing\n')
        bpy.data.texts['End'].write('M84 ;turn off motors\n')


    scene = context.scene
    nozzleboss = scene.nozzleboss
    then=time.time()
    filename = bpy.path.basename(bpy.data.filepath)
    filename = os.path.splitext(filename)[0] #strip .blend extension


    output_path = bpy.path.abspath("//") + bpy.path.basename(filename) + ".gcode"
    try:
        gcode_txt = open(output_path, "w")
    except PermissionError:
        if operator:
            operator.report({'ERROR'}, f"Could not write file — is it open in another program? {output_path}")
        return {'CANCELLED'}
    except OSError as e:
        if operator:
            operator.report({'ERROR'}, f"File write failed: {e}")
        return {'CANCELLED'}



    _txt = []
    start_code = read_textblock('Start')+'\n'#'G28\nM140 S50\nM109 S190\nM83\nG1 F600\n;RGB,-1,-1,-1\nM163 S0 P0\nM163 S1 P0\nM163 S2 P1\nM163 S3 P1\nM163 S4 P1\nM164 S0\nT0\n'
    _txt.append(start_code)
    nozzle_diameter = nozzleboss.nozzle_diameter
    extrusion_speed = nozzleboss.extrusion_speed
    travel_speed = nozzleboss.travel_speed




    obj = bpy.context.active_object
    verts = read_verts(obj.data)
    edges = read_edges(obj.data)
    #initial values
    P2=(0,0,0)
    prev_F=-1
    prev_tool_color = -1
    prev_fan_speed = -1

    #create vertex colors maps, most cases importer could already do that, but in case you have handdrawn/beveled extrusion path
    if not obj.data.color_attributes.get('Flow'):
      obj.data.color_attributes.new(name='Flow', type='FLOAT_COLOR', domain='CORNER')
    if not obj.data.color_attributes.get('Speed'):
      obj.data.color_attributes.new(name='Speed', type='FLOAT_COLOR', domain='CORNER')
    if not obj.data.color_attributes.get('Tool'):
      obj.data.color_attributes.new(name='Tool', type='FLOAT_COLOR', domain='CORNER')
    if not obj.data.color_attributes.get('Fan'):
      obj.data.color_attributes.new(name='Fan', type='FLOAT_COLOR', domain='CORNER')
        
        
    edge_to_loops = build_edge_to_loops(obj.data)
    

    islands = find_islands(edges)
    sorted_islands = sort_Z(islands, verts)
                                  
                                  
    ##islands of extrusions vert indices

    max_z_so_far = 0.0
    hop_clearance = 10.0  # mm above highest point printed so far, adjust to taste

    for island in sorted_islands:
        e_edges = island[:int(len(island)/2)]
        h_edges = island[int(len(island)/2):]

        travel_dist = (Vector(verts[island[0]])-Vector(P2)).length

        # speed for the very first extruded segment of this island
        first_speed_raw = sample_corner_value(obj.data, 'Speed', edge_to_loops, e_edges[0], e_edges[1], use_vertex=e_edges[0])
        first_speed_weight = remap(first_speed_raw, nozzleboss.min_speed, nozzleboss.max_speed)
        restored_F = extrusion_speed * first_speed_weight  # mm/s

        if travel_dist > 1:
            next_pos = verts[island[0]]
            hop_z    = round(max_z_so_far + hop_clearance, 4)
            next_x   = round(float(next_pos[0]), 4)
            next_y   = round(float(next_pos[1]), 4)
            next_z   = round(float(next_pos[2]), 4)

            _txt.append('G10 \n')
            _txt.append(f'G1 F{travel_speed*60}\n')
            _txt.append(f'G1 Z{hop_z}\n')
            _txt.append(f'G1 X{next_x} Y{next_y}\n')
            _txt.append(f'G1 Z{next_z}\n')
            _txt.append(f'G1 F{int(restored_F*60)}\n')  # restore with correct multiplier
            _txt.append('G11 \n')

        else:
            _txt.append(travel(P2, verts[island[0]], travel_speed*60, restored_F*60))

        prev_F = restored_F  # keep tracker in sync with what was actually written
        
        island_zs = [verts[v][2] for v in e_edges]
        max_z_so_far = max(max_z_so_far, max(island_zs))
            
        
        #extrusion and height edges per island, parallel arrays
        e_edges = island[:int(len(island)/2)] #first half extrusion v_idx, real nozzle path
        h_edges = island[int(len(island)/2):] #second half of island corresponding height v_idx
        #print(h_edges)
        

            
            
        #extrude between all poitns in island
        for i in range(len(e_edges)): 
            if i == len(e_edges)-1:#break on last segment, if you want to close segment, 'rip' the last vert
              break
              

            #coord for seg length and height
            P1 = verts[e_edges[i]]
            P2 = verts[e_edges[i+1]]
            P3 = verts[h_edges[i]] 



    #calcE
            dist = np.linalg.norm(P2-P1)
            height=np.linalg.norm(P3-P1)

            width=nozzle_diameter*1.5
            multiplier = sample_corner_value(obj.data, 'Flow', edge_to_loops, e_edges[i], e_edges[i+1], use_vertex=e_edges[i])
            multiplier = remap(multiplier, nozzleboss.min_flow, nozzleboss.max_flow)
            E_volume=dist*height*width*multiplier
            E=E_volume/2.405281875  ##E axis in mm not mm³, 2.405 is 1mm of 1.75mm filament (r*(PI*r), 0.875*PI*0.875


    #calcF

            speed_raw = sample_corner_value(obj.data, 'Speed', edge_to_loops, e_edges[i], e_edges[i+1], use_vertex=e_edges[i])
            speed_weight = remap(speed_raw, nozzleboss.min_speed, nozzleboss.max_speed)
            F = extrusion_speed*speed_weight 

            #check if tool color changed and append corresponding textblock
            tool_color = sample_corner_value(obj.data, 'Tool', edge_to_loops, e_edges[i], e_edges[i+1], use_vertex=e_edges[i])
            if tool_color != prev_tool_color:

                if tool_color < 0.5:
                    _txt.append(read_textblock('T1'))
                else:
                    _txt.append(read_textblock('T0'))  

                prev_tool_color = tool_color   

            #check if fan speed changed and append M106
            fan_raw = sample_corner_value(obj.data, 'Fan', edge_to_loops, e_edges[i], e_edges[i+1], use_vertex=e_edges[i])
            fan_weight = remap(fan_raw, nozzleboss.min_fan, nozzleboss.max_fan)
            fan_speed = int(round(fan_weight*255))
            if fan_speed != prev_fan_speed:
                _txt.append(f'M106 S{fan_speed}\n')
                prev_fan_speed = fan_speed

            gcode_line, prev_F = extrude(P1, P2, E, F, prev_F)
            _txt.append(gcode_line)


    #print(_txt)
    end_code = '\n'+read_textblock('End')
    
    
    _txt.append(end_code)
    gcode_txt.write(''.join(_txt))
    gcode_txt.close()

    
    print("took in seconds: ", time.time()-then)
    if operator:
        operator.report({'INFO'}, f"G-code exported to {output_path}")
    return {'FINISHED'}    
    


# Import operator removed
    
    
    
class WM_OT_gcode_export(Operator):
    bl_idname = "wm.gcode_export"  
    bl_label = "Export to G-code"
    bl_description = "Export active Object to G-code. Result can be found in folder of current .blend file"
    
    
    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.select_get() and obj.type in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'}

    def execute(self, context):
        obj = context.active_object

        if context.mode != 'OBJECT':
            self.report({'ERROR'}, "Switch to Object Mode before exporting")
            return {'CANCELLED'}

        # Duplicate
        bpy.ops.object.duplicate(linked=False)
        new_obj = context.active_object

        # Hide Solidify, and the overlay display modifier so preview colours
        # are never baked into the exported mesh
        for mod in new_obj.modifiers:
            if mod.type == 'SOLIDIFY' or mod.name == overlay.MODIFIER:
                mod.show_viewport = False

        # Apply modifiers
        bpy.ops.object.convert(target='MESH')

        # Apply transforms
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # Export
        try:
            result = export_gcode(context, self)
        except Exception as e:
            bpy.data.objects.remove(new_obj, do_unlink=True)
            self.report({'ERROR'}, f"Export failed: {e}")
            return {'CANCELLED'}

        # Clean up duplicate regardless of result
        bpy.data.objects.remove(new_obj, do_unlink=True)

        if result == {'CANCELLED'}:
            return {'CANCELLED'}

        return {'FINISHED'}
    
    
    



def register():
    bpy.utils.register_class(NOZZLEBOSS_PT_Panel)
    bpy.utils.register_class(gcode_settings)
    bpy.utils.register_class(WM_OT_gcode_export)
    overlay.register()
    bpy.types.Scene.nozzleboss = bpy.props.PointerProperty(type= gcode_settings)




def unregister():
    overlay.unregister()
    bpy.utils.unregister_class(NOZZLEBOSS_PT_Panel)
    bpy.utils.unregister_class(gcode_settings)
    bpy.utils.unregister_class(WM_OT_gcode_export)



if __name__ == "__main__":
    register()