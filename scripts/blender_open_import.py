import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dam_bridge import import_file

for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)
import_file(os.path.abspath(sys.argv[sys.argv.index("--") + 1]))
