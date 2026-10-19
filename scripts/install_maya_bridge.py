import os
import shutil
from pathlib import Path

os.environ["MAYA_SKIP_USERSETUP_PY"] = "1"
import maya.standalone

maya.standalone.initialize()
from maya import cmds

AUTOLOAD = 'evalDeferred("autoLoadPlugin(\\"\\", \\"maya_bridge.py\\", \\"maya_bridge\\")");\n'

prefs = Path(cmds.internalVar(userPrefDir=True))
if not (prefs / "userPrefs.mel").is_file():
    raise SystemExit(f"Error: start Maya {cmds.about(version=True)} once so it creates its preferences, then install again")
plugins = prefs.parent / "plug-ins"
plugins.mkdir(parents=True, exist_ok=True)
shutil.copy2(Path(__file__).resolve().with_name("maya_bridge.py"), plugins / "maya_bridge.py")
# Batch Maya neither reads nor saves plug-in prefs, so the autoload entry is appended to the file the GUI reads.
plugin_prefs = prefs / "pluginPrefs.mel"
text = plugin_prefs.read_text(encoding="utf-8") if plugin_prefs.is_file() else ""
if '\\"maya_bridge.py\\"' not in text:
    plugin_prefs.write_text(text + ("\n" if text and not text.endswith("\n") else "") + AUTOLOAD, encoding="utf-8")
print(plugins / "maya_bridge.py")
