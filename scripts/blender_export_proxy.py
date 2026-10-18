import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Vector

MIN_TRIS_TO_DECIMATE = 1000


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="blender_export_proxy")
    p.add_argument("--input", required=True, type=os.path.abspath)
    p.add_argument("--out-png", required=True, type=os.path.abspath)
    p.add_argument("--out-meta", required=True, type=os.path.abspath)
    p.add_argument("--out-glb", type=os.path.abspath)
    p.add_argument("--decimate", type=float, default=0.5)
    p.add_argument("--thumb-size", type=int, default=512)
    return p.parse_args(argv)


def load_input(path: str) -> None:
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".blend", ".obj", ".fbx"):
        raise ValueError(f"unsupported input extension: {ext or '(none)'}")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    if ext == ".blend":
        # use_scripts=False: never run Python drivers/scripts embedded in an untrusted library file.
        bpy.ops.wm.open_mainfile(filepath=path, load_ui=False, use_scripts=False)
        if os.path.normcase(os.path.abspath(bpy.data.filepath)) != os.path.normcase(path):
            raise RuntimeError("input .blend failed to load (corrupt or saved by a newer Blender)")
        return
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    else:
        bpy.ops.import_scene.fbx(filepath=path)


def tri_count(obj: bpy.types.Object, depsgraph: bpy.types.Depsgraph) -> int:
    ev = obj.evaluated_get(depsgraph)
    mesh = ev.to_mesh()
    try:
        mesh.calc_loop_triangles()
        return len(mesh.loop_triangles)
    finally:
        ev.to_mesh_clear()


def world_bounds(objs: list, depsgraph: bpy.types.Depsgraph) -> tuple[Vector, Vector]:
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.evaluated_get(depsgraph).bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def frame_camera(scene: bpy.types.Scene, lo: Vector, hi: Vector) -> None:
    cam_data = bpy.data.cameras.new("dam_thumb_cam")
    cam = bpy.data.objects.new("dam_thumb_cam", cam_data)
    scene.collection.objects.link(cam)
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1e-3)
    distance = radius / math.sin(cam_data.angle / 2) * 1.05
    cam.location = center + Vector((1.0, -1.0, 0.75)).normalized() * distance
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_data.clip_start = distance * 0.001
    cam_data.clip_end = distance + radius * 2
    scene.camera = cam


def render_thumbnail(scene: bpy.types.Scene, size: int, path: str) -> None:
    r = scene.render
    r.engine = "BLENDER_WORKBENCH"
    r.film_transparent = True
    r.resolution_x = r.resolution_y = size
    r.resolution_percentage = 100
    r.pixel_aspect_x = r.pixel_aspect_y = 1
    r.use_border = r.use_stamp = r.use_compositing = r.use_sequencer = False
    if bpy.app.version >= (5, 0, 0):
        # 5.0 split file_format by media_type; a file set to video output rejects "PNG" otherwise.
        r.image_settings.media_type = "IMAGE"
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGBA"
    r.image_settings.color_depth = "8"
    # Without this Blender appends ".png" and the backend's temp path would not exist.
    r.use_file_extension = False
    r.filepath = path
    bpy.ops.render.render(write_still=True)
    if not os.path.isfile(path):
        raise RuntimeError("render produced no thumbnail")


def export_glb(meshes: list, path: str) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    # The glTF exporter forces a .glb suffix on some versions, so export to one and rename.
    target = path if path.lower().endswith(".glb") else path + ".glb"
    bpy.ops.export_scene.gltf(
        filepath=target,
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_yup=True,
        export_cameras=False,
        export_lights=False,
        export_draco_mesh_compression_enable=True,
    )
    if target != path:
        os.replace(target, path)


def run(args: argparse.Namespace, meta: dict) -> None:
    load_input(args.input)
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    meshes = [o for o in scene.objects if o.type == "MESH" and o.visible_get(view_layer=view_layer)]
    if not meshes:
        raise RuntimeError("scene has no visible mesh objects")

    depsgraph = bpy.context.evaluated_depsgraph_get()
    per_obj = {o.name: tri_count(o, depsgraph) for o in meshes}
    meta["poly_count"] = sum(per_obj.values())
    lo, hi = world_bounds(meshes, depsgraph)
    meta["bounds"] = {"min": list(lo), "max": list(hi)}

    frame_camera(scene, lo, hi)
    render_thumbnail(scene, args.thumb_size, args.out_png)

    if args.out_glb:
        if 0 < args.decimate < 1:
            for o in meshes:
                if per_obj[o.name] >= MIN_TRIS_TO_DECIMATE:
                    mod = o.modifiers.new("dam_decimate", "DECIMATE")
                    mod.decimate_type = "COLLAPSE"
                    mod.ratio = args.decimate
            view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        meta["proxy_poly_count"] = sum(tri_count(o, depsgraph) for o in meshes)
        export_glb(meshes, args.out_glb)
    meta["ok"] = True


def main() -> int:
    args = parse_args()
    meta = {
        "ok": False,
        "blender_version": ".".join(map(str, bpy.app.version)),
        "poly_count": None,
        "proxy_poly_count": None,
        "bounds": None,
        "thumb_size": args.thumb_size,
        "error": None,
    }
    try:
        run(args, meta)
    except Exception as e:  # noqa: BLE001
        meta["ok"] = False
        meta["error"] = f"{type(e).__name__}: {e}"
    with open(args.out_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return 0 if meta["ok"] else 1


sys.exit(main())
