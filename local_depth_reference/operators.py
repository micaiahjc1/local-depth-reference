"""Import, camera alignment, and nonblocking external-worker orchestration."""
import json
from pathlib import Path

import bpy
from bpy.app.handlers import persistent
from bpy.props import IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from . import geometry

_ACTIVE = set()
_FINISHED = {'complete', 'completed', 'success', 'failed', 'error', 'cancelled', 'canceled'}
_SELECTION = {'needs_subject', 'needs_selection', 'awaiting_subject', 'awaiting_selection', 'needs_subject_selection'}
_STATUS_MONITOR_ENABLED = False
_STATUS_SEEN = set()


def _settings(context):
    return context.scene.local_depth_reference


def _runtime_config(context):
    entry = context.preferences.addons.get(__package__)
    return bpy.path.abspath(entry.preferences.runtime_config) if entry and entry.preferences.runtime_config else ''


def _binding(scene):
    value = scene.get('ldr_shot_binding')
    if isinstance(value, str):
        value = json.loads(value)
    if value is None:
        value = getattr(scene, 'blenderae_props', None)
    if value is None:
        value = scene.get('blenderae_props')
    if value is None:
        return None
    result = {}
    for key in ('manifest_path', 'shot_id', 'project_id', 'shot_revision', 'shot_label'):
        item = getattr(value, key, None)
        if item is None and hasattr(value, 'get'):
            item = value.get(key)
        if isinstance(item, (str, int, float, bool)) and item != '':
            result[key] = item
    return result or None


def _selected_import(context):
    collection = _settings(context).import_collection
    if collection not in context.scene.collection.children_recursive:
        raise ValueError('Choose a depth import in the current scene.')
    geometry.get_import_objects(collection)
    return collection


def _camera(context):
    camera = _settings(context).main_camera
    linked = getattr(context.scene, 'blenderae_props', None) or context.scene.get('blenderae_props')
    if not camera and linked is not None:
        for key in ('prep_camera', 'prepared_camera'):
            value = getattr(linked, key, None)
            if value is None and hasattr(linked, 'get'):
                value = linked.get(key)
            if isinstance(value, bpy.types.Object) and value.type == 'CAMERA':
                camera = value
                break
    camera = camera or context.scene.camera
    geometry._valid_camera(camera, context.scene)
    return camera


def _apply_progress(settings, progress):
    state = str(progress.get('status', progress.get('state', 'running'))).lower()
    settings.job_state = state
    settings.job_kind = str(progress.get('job_kind', settings.job_kind))
    settings.job_status = str(progress.get('message') or state.replace('_', ' ').title())
    settings.job_stage = str(progress.get('stage') or '')
    amount = progress.get('progress', progress.get('fraction', 0))
    if isinstance(amount, dict):
        amount = amount.get('fraction', 0)
    try:
        amount = float(amount)
        settings.job_progress = min(max(amount/100 if amount > 1 else amount, 0), 1)
    except (TypeError, ValueError):
        pass
    settings.needs_subject = state in _SELECTION or bool(progress.get('needs_subject_selection'))
    selection = progress.get('subject_selection') or {}
    preview = progress.get('preview_path') or progress.get('selection_preview') or progress.get('preview') or selection.get('preview')
    if isinstance(preview, str):
        path = Path(preview)
        settings.preview_path = str(path if path.is_absolute() else Path(settings.last_run_dir)/path)
    manifest = progress.get('ae_manifest_path') or progress.get('manifest_path') or progress.get('manifest')
    output_property = 'ae_manifest_path' if settings.job_kind == 'ae_export' else 'manifest_path'
    if isinstance(manifest, str):
        path = Path(manifest)
        if state in {'complete', 'completed', 'success'}:
            setattr(settings, output_property, str(path if path.is_absolute() else Path(settings.last_run_dir)/path))
    elif state in {'complete', 'completed', 'success'}:
        path = Path(settings.last_run_dir)/'manifest.json'
        if path.is_file():
            setattr(settings, output_property, str(path))
    if state in {'complete', 'completed', 'success'}:
        settings.job_progress = 1
    return state


def _refresh_progress(settings, progress):
    state = _apply_progress(settings, progress)
    settings.is_running = bool(progress.get('alive')) and state not in _FINISHED and not settings.needs_subject
    return state


def _redraw_status():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _poll_status():
    """Recover recorded jobs even when no modal operator survived a reload."""
    if not _STATUS_MONITOR_ENABLED:
        return None
    if not hasattr(bpy.data, 'scenes'):
        # Add-on enable runs inside Blender's restricted registration context.
        # The registered timer performs the first scan once data is accessible.
        return 1.0
    from . import runtime
    changed = False
    present = set()
    for scene in tuple(bpy.data.scenes):
        try:
            settings = getattr(scene, 'local_depth_reference', None)
            if settings is None or not settings.last_run_dir:
                continue
            key = (scene.as_pointer(), settings.last_run_dir)
            present.add(key)
            if key in _STATUS_SEEN and not settings.is_running and settings.job_state in _FINISHED:
                continue
            progress = runtime.read_progress(bpy.path.abspath(settings.last_run_dir))
            _refresh_progress(settings, progress)
            _STATUS_SEEN.add(key)
            changed = True
        except (OSError, ValueError, TypeError, AttributeError, ReferenceError):
            # An atomic replacement, unavailable drive, or scene removal can be
            # transient. Retain the last known state and retry on the next tick.
            continue
    _STATUS_SEEN.intersection_update(present)
    if changed:
        _redraw_status()
    return 1.0


@persistent
def _status_load_post(_unused):
    _STATUS_SEEN.clear()


def start_status_monitor():
    """Attach the UI to existing workers without starting or cancelling them."""
    global _STATUS_MONITOR_ENABLED
    _STATUS_MONITOR_ENABLED = True
    _STATUS_SEEN.clear()
    if _status_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_status_load_post)
    _poll_status()
    if not bpy.app.timers.is_registered(_poll_status):
        bpy.app.timers.register(_poll_status, first_interval=1.0, persistent=True)


class _JobModal:
    _timer = None
    _scene = None
    _detached = False

    def _start(self, context, run_dir, job_kind='depth'):
        self._detached = False
        self._scene = context.scene
        settings = self._scene.local_depth_reference
        settings.last_run_dir = str(run_dir)
        settings.job_kind = job_kind
        settings.is_running = True
        settings.needs_subject = False
        settings.job_status = 'Starting worker'
        settings.job_state = 'running'
        settings.job_stage = ''
        settings.job_progress = 0
        if bpy.app.background or context.window is None:
            return {'FINISHED'}
        self._timer = context.window_manager.event_timer_add(.5, window=context.window)
        context.window_manager.modal_handler_add(self)
        _ACTIVE.add(self)
        return {'RUNNING_MODAL'}

    def _finish(self, context, clear_running=True):
        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except (ReferenceError, RuntimeError):
                pass
            self._timer = None
        if clear_running and self._scene is not None:
            try:
                self._scene.local_depth_reference.is_running = False
            except (ReferenceError, AttributeError):
                pass
        _ACTIVE.discard(self)

    def _detach(self, context):
        self._detached = True
        self._finish(context, clear_running=False)

    def modal(self, context, event):
        if self._detached:
            return {'CANCELLED'}
        from . import runtime
        if event.type == 'ESC':
            try:
                runtime.request_cancel(self._scene.local_depth_reference.last_run_dir)
            except (ValueError, OSError) as exc:
                self.report({'ERROR'}, str(exc))
            return {'RUNNING_MODAL'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        try:
            settings = self._scene.local_depth_reference
            progress = runtime.read_progress(settings.last_run_dir)
            state = _refresh_progress(settings, progress)
        except (OSError, ValueError, json.JSONDecodeError):
            return {'PASS_THROUGH'}
        except ReferenceError:
            self._finish(context)
            return {'CANCELLED'}
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        if state in _FINISHED or settings.needs_subject:
            self._finish(context)
            if state in {'failed', 'error'}:
                self.report({'ERROR'}, settings.job_status)
                return {'CANCELLED'}
            return {'FINISHED'}
        return {'PASS_THROUGH'}

    def cancel(self, context):
        if self._detached:
            self._finish(context, clear_running=False)
            return
        try:
            from . import runtime
            if self._scene is not None:
                runtime.request_cancel(self._scene.local_depth_reference.last_run_dir)
        except (OSError, ValueError, ReferenceError):
            pass
        self._finish(context)


class LDR_OT_generate(_JobModal, bpy.types.Operator):
    bl_idname = 'ldr.generate'
    bl_label = 'Generate Depth Package'
    bl_description = 'Generate depth using an automatic or supplied person matte outside Blender'

    @classmethod
    def poll(cls, context):
        return context.scene and not _settings(context).is_running

    def execute(self, context):
        from . import runtime
        settings = _settings(context)
        try:
            if not settings.footage_path:
                raise ValueError('Choose source footage first.')
            job = runtime.create_job(
                source=bpy.path.abspath(settings.footage_path),
                shot_dir=bpy.path.abspath(settings.shot_folder) if settings.shot_folder else None,
                scene_binding=_binding(context.scene), blend_path=bpy.data.filepath,
                config_path=_runtime_config(context),
                settings={'edge_radius': settings.cleanup_radius, 'edge_inset': settings.matte_inset,
                          'matte_source': settings.matte_source,
                          'matte_path': bpy.path.abspath(settings.matte_path) if settings.matte_path else '',
                          'matte_channel': settings.matte_channel, 'matte_invert': settings.matte_invert,
                          'cleanup_strength': settings.cleanup_strength})
            runtime.launch_job(job['job_path'])
            settings.subject_id = ''
            settings.preview_path = ''
            return self._start(context, job['run_dir'])
        except (OSError, ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class LDR_OT_export_ae(_JobModal, bpy.types.Operator):
    bl_idname = 'ldr.export_ae'
    bl_label = 'Export AE Maps'
    bl_description = 'Export normalized 16-bit AE maps into a new shot version without regenerating depth'

    @classmethod
    def poll(cls, context):
        return context.scene and bool(_settings(context).manifest_path) and not _settings(context).is_running

    def execute(self, context):
        from . import runtime
        settings = _settings(context)
        try:
            job = runtime.create_ae_export(bpy.path.abspath(settings.manifest_path),
                                          coverage=settings.ae_coverage, config_path=_runtime_config(context))
            runtime.launch_job(job['job_path'])
            return self._start(context, job['run_dir'], job_kind='ae_export')
        except (OSError, ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class LDR_OT_resume(_JobModal, bpy.types.Operator):
    bl_idname = 'ldr.resume_generation'
    bl_label = 'Continue with Subject'
    subject_points_json: StringProperty(default='', options={'HIDDEN'})

    def execute(self, context):
        from . import runtime
        settings = _settings(context)
        try:
            subject = settings.subject_id.strip() or None
            points = json.loads(self.subject_points_json) if self.subject_points_json else None
            if settings.job_kind == 'ae_export':
                subject = None
                if points is not None:
                    raise ValueError('AE exports reuse the person selected in their depth package.')
            if settings.needs_subject and subject is None and points is None:
                raise ValueError('Enter the subject ID shown in the selection preview.')
            runtime.resume_job(settings.last_run_dir, subject_id=subject, subject_points=points)
            return self._start(context, settings.last_run_dir, job_kind=settings.job_kind)
        except (OSError, ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class LDR_OT_cancel(bpy.types.Operator):
    bl_idname = 'ldr.cancel_generation'
    bl_label = 'Cancel Run'

    def execute(self, context):
        from . import runtime
        try:
            runtime.request_cancel(_settings(context).last_run_dir)
            _settings(context).job_status = 'Cancellation requested'
            return {'FINISHED'}
        except (OSError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class LDR_OT_refresh(bpy.types.Operator):
    bl_idname = 'ldr.refresh_status'
    bl_label = 'Refresh'

    def execute(self, context):
        from . import runtime
        settings = _settings(context)
        try:
            _refresh_progress(settings, runtime.read_progress(settings.last_run_dir))
            return {'FINISHED'}
        except (OSError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class LDR_OT_open_output(bpy.types.Operator):
    bl_idname = 'ldr.open_output'
    bl_label = 'Open Output'

    def execute(self, context):
        path = _settings(context).last_run_dir
        if not path or not Path(path).is_dir():
            self.report({'ERROR'}, 'No output folder is available.')
            return {'CANCELLED'}
        bpy.ops.wm.path_open(filepath=path)
        return {'FINISHED'}


class LDR_OT_open_preview(bpy.types.Operator):
    bl_idname = 'ldr.open_preview'
    bl_label = 'Open Selection Preview'

    def execute(self, context):
        path = _settings(context).preview_path
        if not path or not Path(path).is_file():
            self.report({'ERROR'}, 'The selection preview is not available yet.')
            return {'CANCELLED'}
        bpy.ops.wm.path_open(filepath=path)
        return {'FINISHED'}


class LDR_OT_open_ae_output(bpy.types.Operator):
    bl_idname = 'ldr.open_ae_output'
    bl_label = 'Open AE Export Folder'

    def execute(self, context):
        value = _settings(context).ae_manifest_path
        path = Path(bpy.path.abspath(value)) if value else None
        if not path or not path.is_file():
            self.report({'ERROR'}, 'No completed AE export is available.')
            return {'CANCELLED'}
        bpy.ops.wm.path_open(filepath=str(path.parent))
        return {'FINISHED'}


class LDR_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = 'ldr.import_manifest'
    bl_label = 'Import Blender Reference'
    bl_description = 'Import a validated manifest without changing scene settings, timing, or selection'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = _settings(context).manifest_path
        if self.filepath and Path(bpy.path.abspath(self.filepath)).is_file():
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from . import shot_paths
        settings = _settings(context)
        try:
            path = self.filepath or settings.manifest_path
            if not path:
                raise ValueError('Choose the generated manifest.json.')
            package = shot_paths.load_package(bpy.path.abspath(path))
            camera = _camera(context)
            collection = geometry.create_import(context, package, camera, settings.start_frame)
            settings.manifest_path = package['manifest_path']
            settings.import_collection = collection
            settings.main_camera = camera
            self.report({'INFO'}, 'Depth reference imported; scene settings and existing animation preserved.')
            return {'FINISHED'}
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class LDR_OT_match(bpy.types.Operator):
    bl_idname = 'ldr.match_camera_settings'
    bl_label = 'Match Camera Settings'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            geometry.match_camera_settings(_selected_import(context), _camera(context))
            return {'FINISHED'}
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class LDR_OT_align(bpy.types.Operator):
    bl_idname = 'ldr.align_and_parent'
    bl_label = 'Align and Parent to Main Camera'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            geometry.align_and_parent(_selected_import(context), _camera(context))
            return {'FINISHED'}
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class LDR_OT_offset(bpy.types.Operator):
    bl_idname = 'ldr.offset_frames'
    bl_label = 'Offset Imported Frames'
    bl_options = {'REGISTER', 'UNDO'}
    delta: IntProperty(default=1)

    def execute(self, context):
        try:
            controls = geometry.get_import_objects(_selected_import(context))['controls']
            controls['Frame Index Offset'] = max(-100000, min(100000, int(controls['Frame Index Offset'])+self.delta))
            controls.update_tag()
            context.view_layer.update()
            return {'FINISHED'}
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class LDR_OT_restore(bpy.types.Operator):
    bl_idname = 'ldr.restore_original'
    bl_label = 'Restore Import Original'
    bl_description = 'Restore this import’s initial controls, reference camera and parenting; no files or other objects are changed'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            result = geometry.restore_original(_selected_import(context))
            context.view_layer.update()
            if result['preserved_actions']:
                self.report({'INFO'}, 'Restored original. Detached import animation is retained as Action datablocks.')
            return {'FINISHED'}
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


def shutdown(context):
    global _STATUS_MONITOR_ENABLED
    _STATUS_MONITOR_ENABLED = False
    if bpy.app.timers.is_registered(_poll_status):
        bpy.app.timers.unregister(_poll_status)
    if _status_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_status_load_post)
    _STATUS_SEEN.clear()
    for operator in list(_ACTIVE):
        operator._detach(context)


CLASSES = (LDR_OT_generate, LDR_OT_export_ae, LDR_OT_resume, LDR_OT_cancel, LDR_OT_refresh,
           LDR_OT_open_output, LDR_OT_open_preview, LDR_OT_open_ae_output, LDR_OT_import,
           LDR_OT_match, LDR_OT_align, LDR_OT_offset, LDR_OT_restore)
