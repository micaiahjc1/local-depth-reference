bl_info = {
    'name': 'Local Depth Reference',
    'author': 'Micaiah / Codex',
    'version': (0, 3, 0),
    'blender': (4, 2, 0),
    'location': '3D View > Sidebar > Local Depth',
    'description': 'Generate local depth for manual Blender alignment and export AE depth maps without changing scene timing or rigs',
    'category': '3D View',
}

# Blender reloads the package entry point on an in-session update, but Python
# keeps its child modules cached. Reload dependencies before rebuilding RNA
# classes so an updated version cannot retain the previous sidebar/operators.
if 'bpy' in locals():
    import importlib
    import sys
    for _name in ('shot_paths', 'runtime', 'geometry', 'operators', 'ui'):
        _module = sys.modules.get(__name__ + '.' + _name)
        if _module is not None:
            importlib.reload(_module)

import bpy
from . import operators, ui

_CLASSES = (ui.LDR_Settings, ui.LDR_Preferences) + operators.CLASSES + (ui.LDR_PT_reference,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.local_depth_reference = bpy.props.PointerProperty(type=ui.LDR_Settings)
    operators.start_status_monitor()


def unregister():
    operators.shutdown(bpy.context)
    if hasattr(bpy.types.Scene, 'local_depth_reference'):
        del bpy.types.Scene.local_depth_reference
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
