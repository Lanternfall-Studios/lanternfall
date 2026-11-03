import bpy, os
bpy.ops.wm.open_mainfile(filepath=os.path.join(os.path.dirname(os.path.abspath(__file__)), "mira.blend"))
bpy.ops.object.select_all(action='SELECT')
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mira.glb")
bpy.ops.export_scene.gltf(filepath=out, export_format='GLB')
print("GLB_OK", out, os.path.getsize(out))
