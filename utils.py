import bpy
import numpy as np
import bmesh

def read_verts(mesh):
    mverts_co = np.zeros((len(mesh.vertices) * 3), dtype=float)
    mesh.vertices.foreach_get("co", mverts_co)
    return np.reshape(mverts_co, (len(mesh.vertices), 3))


def read_edges(mesh): #return np.array
    fastedges = np.zeros((len(mesh.edges)*2), dtype=int) # [0.0, 0.0] * len(mesh.edges)
    mesh.edges.foreach_get("vertices", fastedges)
    return np.reshape(fastedges, (len(mesh.edges), 2))


def read_textblock(name):
    txt = bpy.data.texts[name]
    textblock=[]
    for l in txt.lines:
        textblock.append(l.body+'\n')   
            
    textblock="".join(textblock) 
    return textblock


def travel(coords, next, travel_speed, extrusion_speed):
    x,y,z = coords[0], coords[1], coords[2]
    next_x, next_y, next_z = next[0], next[1], next[2]
    gcode_cmd=''

    gcode_cmd+='G1 F'+str(travel_speed)+'\n'
        
    gcode_cmd+='G1 '
    
    #only changed coords
    if next_x!=x:
        gcode_cmd+='X'+str(round(next_x,4))+' '
    if next_y!=y:
        gcode_cmd+='Y'+str(round(next_y,4))+' '
    if next_z!=z:
        gcode_cmd+='Z'+str(round(next_z,4))
#    gcode_cmd+='G11 \n'
#        

    gcode_cmd+='\nG1 '+'F'+str(extrusion_speed)+'\n'  
    
    return gcode_cmd



#only print changed axis
def extrude(coords, next, E, F, prev_F):
    gcode_cmd='G1 '
    x,y,z = coords[0], coords[1], coords[2]
    next_x, next_y, next_z = next[0], next[1], next[2]
    
    if x!=next_x:
        gcode_cmd+='X'+str(round(next_x,4))+' '
    if y!=next_y:
        gcode_cmd+='Y'+str(round(next_y,4))+' '
    if z!=next_z: 
        gcode_cmd+='Z'+str(round(next_z,4))+' '
    
    gcode_cmd+= 'E'+str(round(E,4))
    
        #cweighted speed, put in extrude function later
    if F!=prev_F:          
            gcode_cmd+=(' F'+str(int(F*60)))
            prev_F=F
    gcode_cmd += '\n'
    #return string of changed coords
    return gcode_cmd, prev_F
    
#find loose parts/islands in list np.array of edges this is used to find individual toolpaths, which are then sorted by Z height for printing order
def find_islands(edges):
    
    lparts=[]
    paths={v_idx:set() for v_idx in edges.ravel()} 

    for e in edges:
        paths[e[0]].add(e[1])
        paths[e[1]].add(e[0])
        
    while True:
        try:
            i=next(iter(paths.keys()))
        except StopIteration:
            break
        lpart={i}
        cur={i}
        while True:
            eligible={sc for sc in cur if sc in paths}
            if not eligible:
                break
            cur={ve for sc in eligible for ve in paths[sc]}
            lpart.update(cur)
            for key in eligible: paths.pop(key)
        lparts.append(sorted(list(lpart))) #make index order of unordered set chronological, works in my case
    
    #return np.array(lparts)
    return np.array(lparts, dtype=object) 

def sort_Z(islands, verts):  #islands = vert_idx,  verts = verts_co
    sorted_verts=[]
    for island in islands: #island is list of vert_indices
        # mean z
        listz = [verts[v_idx][2] for v_idx in island[:int(len(island)/2)]] #take z coords of first half of islands (where extrusion path lies, other half of beveled path is only for meshing the path und getting height edges)
        meanz = np.mean(listz)
        # store island and meanz (and original idx to get sculpt colors of sorted.)
        sorted_verts.append((island, meanz)) #store vert_indices(island) with Z average


    sorted_islands = [data[0] for data in sorted(sorted_verts, key=lambda height: height[1])] #sort islands by Z average
    
    return sorted_islands #used z coords of upper half but return islands as whole beveled mesh and slice into extrusion and height edges in 
    
    
    
def build_edge_to_loops(mesh):
    """Map an undirected edge {v1, v2} -> {v1: loop_idx_at_v1, v2: loop_idx_at_v2}
    within whichever single polygon owns that edge. Unlike a directed-edge dict,
    this doesn't depend on the face's winding direction matching the direction
    you query in - you look up the loop for the specific vertex you want,
    explicitly, every time."""
    edge_to_loops = {}
    for poly in mesh.polygons:
        verts = list(poly.vertices)
        loops = list(poly.loop_indices)
        n = len(verts)
        for i in range(n):
            v_curr, v_next = verts[i], verts[(i + 1) % n]
            loop_curr, loop_next = loops[i], loops[(i + 1) % n]
            key = frozenset((v_curr, v_next))
            edge_to_loops[key] = {v_curr: loop_curr, v_next: loop_next}
    return edge_to_loops


def linear_to_srgb(c):
    """Convert a linear color-attribute value back to perceptual (sRGB) space,
    so a value that was picked/painted as 'visual 0.5' reads back as 0.5
    instead of the linear-encoded ~0.214 that Blender stores internally."""
    c = max(0.0, min(1.0, c))
    if c <= 0.0031308:
        return c * 12.92
    return 1.055 * (c ** (1 / 2.4)) - 0.055


def sample_corner_value(mesh, vcol_name, edge_to_loops, v_from, v_to, use_vertex=None):
    """Luma (0-1) of the face-corner color for the segment v_from->v_to.
    use_vertex picks which endpoint's own corner to sample - defaults to
    v_from (start of the move/segment). Returns None if the edge isn't in
    the map, or if the requested vertex has no corner in that face (e.g.
    querying the far end when only one direction has a triangle - shouldn't
    happen on the quad ribbon topology this add-on generates)."""
    loops = edge_to_loops.get(frozenset((v_from, v_to)))
    if loops is None:
        return None
    target = use_vertex if use_vertex is not None else v_from
    loop_idx = loops.get(target)
    if loop_idx is None:
        return None

    col = mesh.color_attributes[vcol_name].data[loop_idx].color[:3]
    luma = col[0]*0.299 + col[1]*0.587 + col[2]*0.114
    return linear_to_srgb(luma)



def remap(weight, min, max):
    remapped_speed = np.interp(weight,[0,1],[min,max])
    return remapped_speed