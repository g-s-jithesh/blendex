import argparse
import json
import os
import sys
import tempfile

import maya_safety

# A hostile downloaded scene is auto-converted on scroll, so none of its embedded code may run.
BLOCKED_AUDIT_EVENTS = frozenset({
    "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile",
    "subprocess.Popen", "pty.spawn", "winreg.CreateKey", "winreg.SetValue",
})
_GUARD = {"armed": False, "writes": ()}

QUIET_ENV = {
    "MAYA_SKIP_USERSETUP_PY": "1",
    "MAYA_DISABLE_CER": "1",
    "MAYA_DISABLE_CLIC_IPM": "1",
    "MAYA_DEBUG_NO_SAVE_ON_CRASH": "1",
}
# Scenes saved with these installed carry `requires` lines that load them on open (bifrostGraph ~75 s,
# mtoa/mayaUsd/xgen 5-9 s each) and none of them is needed to export polygon meshes.
SKIPPED_PLUGIN_DIRS = ("arnold", "mtoa", "bifrost", "mayausd", "lookdevx", "substance", "xgen", "mayaflow", "flowretopology")
FBX_SETTINGS = (
    "FBXResetExport",
    "FBXExportInAscii -v false",
    "FBXExportAnimationOnly -v false",
    "FBXExportBakeComplexAnimation -v false",
    "FBXExportCameras -v false",
    "FBXExportLights -v false",
    "FBXExportConstraints -v false",
    "FBXExportSkins -v false",
    "FBXExportShapes -v false",
    "FBXExportInputConnections -v false",
    "FBXExportEmbeddedTextures -v false",
    "FBXExportSmoothMesh -v false",
    "FBXExportSmoothingGroups -v true",
    "FBXExportReferencedAssetsContent -v true",
)


def wide_argv() -> list[str]:
    if os.name != "nt" or len(sys.argv) < 2:
        return sys.argv[1:]
    import ctypes
    from ctypes import wintypes

    # mayapy builds sys.argv from the ANSI command line, so non-ASCII paths arrive mangled.
    kernel32, shell32 = ctypes.windll.kernel32, ctypes.windll.shell32
    kernel32.GetCommandLineW.restype = wintypes.LPCWSTR
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    n = ctypes.c_int()
    wide = shell32.CommandLineToArgvW(kernel32.GetCommandLineW(), ctypes.byref(n))
    return [wide[i] for i in range(n.value - len(sys.argv) + 1, n.value)]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="maya_export_fbx")
    p.add_argument("--input", required=True, type=os.path.abspath)
    p.add_argument("--out-fbx", required=True, type=os.path.abspath)
    p.add_argument("--out-meta", required=True, type=os.path.abspath)
    return p.parse_args(wide_argv())


def check_paths(args: argparse.Namespace) -> None:
    if os.path.splitext(args.input)[1].lower() not in (".ma", ".mb"):
        raise ValueError(f"unsupported input extension: {os.path.splitext(args.input)[1] or '(none)'}")
    if not os.path.isfile(args.input):
        raise FileNotFoundError(args.input)
    if os.name == "nt":
        for path in (args.input, args.out_fbx):
            try:
                path.encode("mbcs")
            except UnicodeEncodeError:
                raise ValueError(f"Maya cannot open paths outside the system code page: {path}") from None


def audit_hook(event: str, args: tuple) -> None:
    if not _GUARD["armed"]:
        return
    if event in BLOCKED_AUDIT_EVENTS:
        raise PermissionError(f"Blendex blocked a scene process/registry call: {event}")
    if event == "open":
        mode = args[1] if len(args) > 1 else ""
        target = args[0]
        if mode and isinstance(target, (str, bytes, os.PathLike)) and any(c in str(mode) for c in "wax+"):
            norm = os.path.normcase(os.path.abspath(os.fspath(target)))
            if not any(norm.startswith(p) for p in _GUARD["writes"]):
                raise PermissionError(f"Blendex blocked a scene file write outside output paths: {target}")


def arm_audit_guard(args: argparse.Namespace) -> None:
    writes = [os.path.dirname(args.out_fbx), os.path.dirname(args.out_meta),
              os.environ.get("MAYA_APP_DIR", ""), tempfile.gettempdir(), sys.prefix, sys.base_prefix]
    _GUARD["writes"] = tuple(os.path.normcase(os.path.abspath(p)) for p in writes if p)
    _GUARD["armed"] = True


def open_scene(cmds, path: str) -> str | None:
    os.environ["MAYA_PLUG_IN_PATH"] = os.pathsep.join(
        d for d in os.environ.get("MAYA_PLUG_IN_PATH", "").split(os.pathsep)
        if not any(k in d.lower() for k in SKIPPED_PLUGIN_DIRS)
    )
    try:
        cmds.file(
            path, open=True, force=True, ignoreVersion=True, prompt=False,
            executeScriptNodes=False, loadReferenceDepth="none",
        )
    except RuntimeError as e:
        return str(e).strip()
    return None


def is_visible(om, path: str) -> bool:
    sel = om.MSelectionList()
    sel.add(path)
    return sel.getDagPath(0).isVisible()


def run(args: argparse.Namespace, meta: dict) -> None:
    import maya.api.OpenMaya as om
    from maya import cmds, mel

    meta["maya_version"] = cmds.about(version=True)
    arm_audit_guard(args)
    with maya_safety.untrusted_scene(args.input):
        open_error = open_scene(cmds, args.input)
    # isVisible also honours display layers and draw overrides, which `ls -visible` ignores.
    all_shapes = cmds.ls(type="mesh", noIntermediate=True, long=True, allPaths=True) or []
    shapes = [s for s in all_shapes if is_visible(om, s)]
    if not shapes:
        raise RuntimeError("scene has no visible meshes" + (f" ({open_error})" if open_error else ""))
    transforms = sorted({cmds.listRelatives(s, parent=True, fullPath=True)[0] for s in shapes})
    meta["poly_count"] = sum(cmds.polyEvaluate(s, triangle=True) for s in shapes)

    cmds.loadPlugin("fbxmaya", quiet=True)
    for line in FBX_SETTINGS:
        mel.eval(line)
    cmds.select(transforms, replace=True)
    cmds.file(args.out_fbx, exportSelected=True, type="FBX export", force=True, preserveReferences=False)
    if not os.path.isfile(args.out_fbx):
        raise RuntimeError("FBX export produced no file")
    meta["ok"] = True


def main() -> int:
    args = parse_args()
    meta = {"ok": False, "maya_version": None, "poly_count": None, "error": None}
    for k, v in QUIET_ENV.items():
        os.environ.setdefault(k, v)
    sys.addaudithook(audit_hook)
    standalone = None
    try:
        check_paths(args)
        import maya.standalone

        maya.standalone.initialize(name="python")
        standalone = maya.standalone
        run(args, meta)
    except Exception as e:  # noqa: BLE001
        meta["ok"] = False
        meta["error"] = f"{type(e).__name__}: {e}"
    with open(args.out_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    if standalone is not None:
        standalone.uninitialize()
    return 0 if meta["ok"] else 1


sys.exit(main())
