import os
import shutil

import bpy

addons = bpy.utils.user_resource("SCRIPTS", path="addons", create=True)
shutil.copy2(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dam_bridge.py"),
             os.path.join(addons, "dam_bridge.py"))
bpy.utils.refresh_script_paths()
bpy.ops.preferences.addon_refresh()
bpy.ops.preferences.addon_enable(module="dam_bridge")
bpy.ops.wm.save_userpref()
