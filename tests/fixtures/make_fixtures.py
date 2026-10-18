import os
import sys

import bpy

out = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else os.path.join(os.path.dirname(__file__), "out")
os.makedirs(out, exist_ok=True)
prefs = bpy.context.preferences.filepaths


def save(name: str, preview: str = "AUTO", **kw) -> None:
    prefs.file_preview_type = preview
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, name), copy=True, **({"compress": False} | kw))


def remove(*types: str) -> None:
    for o in [o for o in bpy.data.objects if o.type in types]:
        bpy.data.objects.remove(o)


save("camera.blend")
remove("CAMERA", "LIGHT")
save("preview.blend")
save("preview_zstd.blend", compress=True)

remove("MESH")
bpy.ops.mesh.primitive_monkey_add()
bpy.ops.object.modifier_add(type="SUBSURF")
bpy.context.object.modifiers[-1].levels = 2
save("nopreview.blend", "NONE")
bpy.ops.wm.obj_export(filepath=os.path.join(out, "suzanne.obj"), export_selected_objects=True,
                      export_materials=False)
bpy.ops.export_scene.fbx(filepath=os.path.join(out, "suzanne.fbx"), use_selection=True)
for mode in ("EDIT", "SCULPT"):
    bpy.ops.object.mode_set(mode=mode)
    save(f"{mode.lower()}_mode.blend")
    bpy.ops.object.mode_set(mode="OBJECT")

remove("MESH")
save("empty_scene.blend")
prefs.file_preview_type = "AUTO"

with open(os.path.join(out, "preview.blend"), "rb") as f:
    head = f.read(4096)
with open(os.path.join(out, "truncated.blend"), "wb") as f:
    f.write(head)
open(os.path.join(out, "empty.blend"), "wb").close()
