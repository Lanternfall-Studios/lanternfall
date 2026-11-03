"""
Lanternfall developing SDK — asset build: Elder Mira (lantern keeper)
Iteration loop: run headless, renders 4 ortho views to renders/, compare
against concept/mira-contact-sheet.webp, adjust PARAMS, re-run.

Usage: blender -b --python build_mira.py
Character faces -Y. Units are meters. Total height ~1.62.
"""
import bpy, math, os
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
RENDERS = os.path.join(HERE, "renders")
os.makedirs(RENDERS, exist_ok=True)

# ---------------------------------------------------------------- params
P = dict(
    dress_top_r=0.16, dress_bot_r=0.35, dress_center_z=0.60, dress_depth=1.16,
    head_r=0.165, head_z=1.47,
    hair_r=0.168, hair_dy=0.036, hair_dz=0.038,
    bun_r=0.076, bun_y=0.128, bun_z=1.612,
    shoulder_x=0.185, shoulder_z=1.235,
    wrist_x=0.25, wrist_z=0.845,
    lantern_x=-0.30, lantern_top_z=0.80,
)

# ---------------------------------------------------------------- reset FIRST
# (read_factory_settings invalidates material references created before it)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

# ---------------------------------------------------------------- palette
def make_mat(name, rgb, emission=None, strength=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = 1.0
    if emission is not None:
        bsdf.inputs["Emission Color"].default_value = (*emission, 1.0)
        bsdf.inputs["Emission Strength"].default_value = strength
    return m

CREAM  = make_mat("cream",  (0.84, 0.78, 0.64))
RUST   = make_mat("rust",   (0.58, 0.21, 0.10))
SKIN   = make_mat("skin",   (0.85, 0.62, 0.48))
HAIR   = make_mat("hair",   (0.45, 0.45, 0.48))
DARK   = make_mat("dark",   (0.16, 0.12, 0.10))
GOLD   = make_mat("gold",   (0.55, 0.38, 0.12))
SAGE   = make_mat("sage",   (0.45, 0.55, 0.38))
GLOW   = make_mat("glow",   (1.00, 0.80, 0.45), emission=(1.0, 0.62, 0.22), strength=2.5)

# ---------------------------------------------------------------- helpers
def flat(obj):
    for poly in obj.data.polygons:
        poly.use_smooth = False
    return obj

def apply_mat(obj, material):
    if material:
        obj.data.materials.append(material)
    return obj

def cube(name, size, loc, mat, scale=(1, 1, 1)):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    o = bpy.context.object; o.name = name; o.scale = scale
    return apply_mat(flat(o), mat)

def sphere(name, r, loc, mat, scale=(1, 1, 1), seg=12, rings=8):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=seg, ring_count=rings,
                                         radius=r, location=loc)
    o = bpy.context.object; o.name = name; o.scale = scale
    return apply_mat(flat(o), mat)

def cylinder(name, r_top, r_bot, depth, loc, mat, verts=14):
    bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=r_bot, radius2=r_top,
                                    depth=depth, location=loc)
    o = bpy.context.object; o.name = name
    return apply_mat(flat(o), mat)

def cone(name, r_bot, r_top, depth, loc, mat, verts=14):
    return cylinder(name, r_top, r_bot, depth, loc, mat, verts)

def limb(name, p1, p2, r, mat):
    p1, p2 = Vector(p1), Vector(p2)
    d = p2 - p1
    bpy.ops.mesh.primitive_cylinder_add(vertices=10, radius=r, depth=d.length,
                                        location=(p1 + p2) / 2)
    o = bpy.context.object; o.name = name
    o.rotation_mode = 'QUATERNION'
    o.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(d.normalized())
    return apply_mat(flat(o), mat)

def torus(name, R, tube, loc, mat, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_torus_add(major_radius=R, minor_radius=tube,
                                     major_segments=16, minor_segments=6,
                                     location=loc, rotation=rot)
    o = bpy.context.object; o.name = name
    return apply_mat(flat(o), mat)

# ---------------------------------------------------------------- build
# Dress — long cream A-line gown
cylinder("dress", P["dress_top_r"], P["dress_bot_r"], P["dress_depth"],
         (0, 0, P["dress_center_z"]), CREAM, verts=16)

# Shawl — three pieces: shoulder wrap + front point + back point.
# (A single cone of revolution dives inside the dress and reads as a slab;
#  the sheet's shawl is a wrap with triangular drapes front and back.)
cone("shawl_wrap", 0.29, 0.14, 0.27, (0, 0, 1.235), RUST, verts=16)
# sage trim band on the wrap's lower edge (the sheet's green wavy trim) — flush
cone("shawl_trim", 0.290, 0.285, 0.016, (0, 0, 1.104), SAGE, verts=16)

def drape(name, r_top, r_bot, depth, loc, y_scale=0.5, rot_z=0.0, rot_x=0.0):
    o = cone(name, r_bot, r_top, depth, loc, RUST, verts=14)
    o.scale = (0.95, y_scale, 1.0)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    o.rotation_euler = (rot_x, 0, rot_z)
    return o

# front drape angled — the sheet's wrap crosses the torso diagonally
drape("shawl_front", 0.20, 0.03, 0.33, (0.03, -0.20, 1.03),
      y_scale=0.42, rot_z=math.radians(-22), rot_x=math.radians(6))
drape("shawl_back", 0.22, 0.03, 0.40, (0, 0.185, 0.99))

# Neck + head
cylinder("neck", 0.05, 0.055, 0.10, (0, 0, 1.345), SKIN, verts=10)
sphere("head", P["head_r"], (0, 0, P["head_z"]), SKIN, scale=(0.94, 0.97, 1.04))

# Hair cap (offset up/back so the face stays clear) + bun
sphere("hair", P["hair_r"], (0, P["hair_dy"], P["head_z"] + P["hair_dz"]),
       HAIR, scale=(0.96, 0.99, 1.02))
sphere("bun", P["bun_r"], (0, P["bun_y"], P["bun_z"]), HAIR)

# Eyes + glasses
for sx in (-1, 1):
    sphere("eye", 0.0095, (sx * 0.058, -0.155, P["head_z"] + 0.012), DARK, seg=8, rings=6)
    torus("lens", 0.038, 0.005, (sx * 0.058, -0.162, P["head_z"] + 0.012),
          DARK, rot=(math.radians(90), 0, 0))
cube("bridge", 0.008, (0, -0.162, P["head_z"] + 0.014), DARK, scale=(3.2, 0.8, 0.8))

# Arms (cream sleeves) + hands
for sx in (-1, 1):
    limb("sleeve", (sx * P["shoulder_x"], 0, P["shoulder_z"]),
         (sx * P["wrist_x"], -0.01, P["wrist_z"]), 0.052, CREAM)
    sphere("hand", 0.046, (sx * P["wrist_x"], -0.015, P["wrist_z"] - 0.05),
           SKIN, scale=(0.85, 0.85, 1.15), seg=10, rings=7)

# Lantern in her right hand (x negative side, per contact sheet front view)
lx, ltop = P["lantern_x"], P["lantern_top_z"]
cylinder("lant_handle_post", 0.007, 0.007, 0.08, (lx, -0.015, ltop - 0.02), GOLD, verts=8)
cone("lant_cap", 0.034, 0.070, 0.05, (lx, -0.015, ltop - 0.085), GOLD, verts=6)
cylinder("lant_glass", 0.050, 0.057, 0.105, (lx, -0.015, ltop - 0.16), GLOW, verts=6)
cylinder("lant_base", 0.058, 0.050, 0.02, (lx, -0.015, ltop - 0.222), GOLD, verts=6)

# ---------------------------------------------------------------- light/world
world = bpy.data.worlds.new("World")
scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.96, 0.95, 0.93, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.75

bpy.ops.object.light_add(type='SUN', location=(4, -4, 7))
sun = bpy.context.object
sun.data.energy = 2.2
sun.rotation_euler = (math.radians(48), math.radians(8), math.radians(38))
bpy.ops.object.light_add(type='SUN', location=(-5, 3, 4))
fill = bpy.context.object
fill.data.energy = 0.9
fill.rotation_euler = (math.radians(65), math.radians(-15), math.radians(-105))

# ---------------------------------------------------------------- cameras + render
scene.render.engine = 'CYCLES'
scene.cycles.samples = 24
scene.cycles.device = 'CPU'
scene.render.resolution_x = 560
scene.render.resolution_y = 560

TARGET = Vector((0, 0, 0.82))
VIEWS = {
    "front": Vector((0, -3.4, 0.86)),
    "side":  Vector((3.4, 0, 0.86)),
    "back":  Vector((0, 3.4, 0.86)),
    "top":   Vector((0, -0.55, 3.6)),
}
bpy.ops.object.camera_add()
cam = bpy.context.object
cam.data.type = 'ORTHO'
cam.data.ortho_scale = 2.05
scene.camera = cam

for name, pos in VIEWS.items():
    cam.location = pos
    direction = TARGET - pos
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    scene.render.filepath = os.path.join(RENDERS, f"mira_{name}.png")
    bpy.ops.render.render(write_still=True)
    print("RENDERED", name)

bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "mira.blend"))
print("MIRA_BUILD_OK")
