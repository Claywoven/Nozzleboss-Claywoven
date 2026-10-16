"""G-code text from move arrays. Plain Python and numpy only - no bpy or
mathutils - so this can be tested without Blender."""

import numpy as np

# Height the nozzle lifts above the highest point printed so far on long travels (mm).
HOP_CLEARANCE = 10.0
# Travels shorter than this (mm) skip the retract and Z-hop.
HOP_MIN_TRAVEL = 1.0
# Filament cross-section in mm², 1 mm of 1.75 mm filament (0.875² * pi).
FILAMENT_AREA = 2.405281875

# The move lines need absolute positioning and relative extrusion. They are
# set after the start G-code, whatever it (or a macro it calls) left, and
# again after every snippet, which may change them.
MODES = 'G90 ; absolute positioning (nozzleboss moves)\nM83 ; relative extrusion (nozzleboss moves)\n'
MODES_AFTER_SNIPPET = 'G90\nM83\n'

def feed_rate(speed):
    """G-code F (mm/min) for a speed in mm/s, rounded: a speed stored as
    0.19999 mm/s is F12, not F11."""
    return int(round(speed * 60))


def travel(coords, next, travel_speed, extrusion_speed):
    x,y,z = coords[0], coords[1], coords[2]
    next_x, next_y, next_z = next[0], next[1], next[2]
    if (next_x, next_y, next_z) == (x, y, z):
        # Already there: no bare "G1" line, just the extrusion speed.
        return 'G1 F'+str(extrusion_speed)+'\n'
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
            gcode_cmd+=(' F'+str(feed_rate(F)))
            prev_F=F
    gcode_cmd += '\n'
    #return string of changed coords
    return gcode_cmd, prev_F

class _SnippetFloat(float):
    """A bare {name} is rounded to 4 decimals like the move lines; a format
    spec such as {name:.2f} is used as given."""
    def __format__(self, spec):
        return float.__format__(self, spec) if spec else str(round(float(self), 4))


def format_snippet(where, template, values):
    """Fill the placeholders in a G-code snippet from values ({placeholder:
    the face's value}), using the same format syntax as the Format String
    node. A bare float placeholder is rounded to 4 decimals."""
    values = {name: _SnippetFloat(value) if isinstance(value, (float, np.floating)) else value
              for name, value in values.items()}
    try:
        snippet = template.format_map(values)
    except KeyError as e:
        available = ", ".join(f"{{{name}}}" for name in sorted(values)) or "none (the stage has no values)"
        raise ValueError(f"{where}:\nIt uses {{{e.args[0]}}}; available: {available}.")
    except (ValueError, IndexError) as e:
        raise ValueError(f"{where}:\nA placeholder doesn't fit its value: {e}.")
    return snippet if snippet.endswith('\n') else snippet + '\n'

def travel_to(txt, from_co, to_co, max_z_so_far, F, travel_speed, retraction=None):
    """Travel at travel_speed (mm/s) to the start of a run, then set the
    extrusion speed F (mm/s). Long travels hop above everything printed so
    far, between retraction's (retract, unretract) text when given. from_co
    None means the nozzle position is unknown (the first travel after the
    start G-code): that always takes the long form, which states X, Y and Z."""
    travel_F = feed_rate(travel_speed)
    if from_co is None:
        travel_dist = np.inf
    else:
        travel_dist = np.linalg.norm(np.asarray(to_co, dtype=float) - np.asarray(from_co, dtype=float))
    if travel_dist > HOP_MIN_TRAVEL:
        hop_z  = round(max_z_so_far + HOP_CLEARANCE, 4)
        next_x = round(float(to_co[0]), 4)
        next_y = round(float(to_co[1]), 4)
        next_z = round(float(to_co[2]), 4)

        if retraction is not None:
            txt.append(retraction[0])
        txt.append(f'G1 F{travel_F}\n')
        txt.append(f'G1 Z{hop_z}\n')
        txt.append(f'G1 X{next_x} Y{next_y}\n')
        txt.append(f'G1 Z{next_z}\n')
        # The unretract may set its own F, so the extrusion speed comes after it.
        if retraction is not None:
            txt.append(retraction[1])
        txt.append(f'G1 F{feed_rate(F)}\n')
    else:
        txt.append(travel(from_co, to_co, travel_F, feed_rate(F)))

def write_path_moves(txt, runs, travel_speed, retraction=None):
    """The moves of each mesh_read.read_object_moves() result in turn. Each object
    starts with a travel, which hops above everything printed so far.
    travel_speed is in mm/s; retraction is the (retract, unretract) text
    around long travels, or None for none. MODES comes first, and again
    after snippets."""
    txt.append(MODES)
    prev_co = None  # unknown until the first travel
    prev_F = -1
    prev_fan = -1
    max_z_so_far = 0.0
    for run in runs:
        txt.append(f'; object: {run["name"]}\n')
        # Plain Python lists: indexing a numpy array one element at a time is
        # several times slower than the rest of the loop put together.
        P1, P2, E, speed, new_run, zero = (
            np.asarray(run[key]).tolist() for key in ("P1", "P2", "E", "speed", "new_run", "zero"))
        # fan None: the run leaves the fan to the start code and snippets.
        fan = None if run["fan"] is None else np.asarray(run["fan"]).tolist()
        snippets = run["snippets"]
        for k in range(len(P1)):
            if new_run[k]:
                travel_to(txt, prev_co, P1[k], max_z_so_far, speed[k], travel_speed, retraction)
                prev_F = speed[k]
            if fan is not None and fan[k] != prev_fan:
                txt.append(f'M106 S{fan[k]}\n')
                prev_fan = fan[k]
            if k in snippets:
                txt.extend(snippets[k])
                txt.append(MODES_AFTER_SNIPPET)
                prev_F = -1  # a snippet may change the feed rate; restore it on this move
            if zero[k]:
                continue
            gcode_line, prev_F = extrude(P1[k], P2[k], E[k], speed[k], prev_F)
            txt.append(gcode_line)
            prev_co = P2[k]
            max_z_so_far = max(max_z_so_far, P1[k][2], P2[k][2])
