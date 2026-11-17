"""Sidebar and preferences for manual depth references."""
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty

from . import geometry


def _camera_poll(self, obj):
    return obj.type == 'CAMERA' and obj.get(geometry.ROLE) != 'camera'


def _collection_poll(self, collection):
    return geometry.is_import(collection)


class LDR_Settings(bpy.types.PropertyGroup):
    footage_path: StringProperty(name='Footage', subtype='FILE_PATH', description='Original footage used to generate this depth reference')
    shot_folder: StringProperty(name='Shot Folder', subtype='DIR_PATH', description='Explicit shot folder; output is resolved under its resources/depth map folder')
    matte_source: EnumProperty(name='Person Matte', items=(
        ('automatic', 'Generate Automatically', 'Track and refine the person using SAM3 and MatAnyone2'),
        ('custom', 'Use My Matte', 'Use your image sequence for person coverage and boundary cleanup; skip automatic matting')),
        default='automatic')
    matte_path: StringProperty(name='First Matte Frame', subtype='FILE_PATH',
                              description='First numbered PNG or TIFF image corresponding to the first source-video frame; 8-bit or 16-bit')
    matte_channel: EnumProperty(name='Read Matte From', items=(
        ('luma', 'Grayscale / RGB', 'Use image brightness; white is the person and black is background'),
        ('alpha', 'Alpha Channel', 'Use the embedded alpha; the sequence must contain an alpha channel')),
        default='luma')
    matte_invert: BoolProperty(name='Invert Matte', default=False, description='Use when black represents the person instead of white')
    main_camera: PointerProperty(name='Main Camera', type=bpy.types.Object, poll=_camera_poll,
                                description='Camera to match; its settings and animation are never edited')
    import_collection: PointerProperty(name='Depth Import', type=bpy.types.Collection, poll=_collection_poll)
    manifest_path: StringProperty(name='Depth Package', subtype='FILE_PATH', description='Completed depth manifest used for Blender and AE exports')
    ae_coverage: EnumProperty(name='AE Coverage', items=(
        ('whole_scene', 'Whole Scene', 'Export the complete scene depth, including the background'),
        ('person', 'Person Only', 'Export boundary-corrected person depth and a separate person matte'),
        ('both', 'Both', 'Export separate whole-scene and person depth sequences, plus the person matte')),
        default='both')
    ae_manifest_path: StringProperty(name='Last AE Export', subtype='FILE_PATH', description='Most recent completed AE export; separate from the selected Blender depth package')
    start_frame: IntProperty(name='Start Frame', default=1, min=-1048574, max=1048574)
    cleanup_radius: IntProperty(name='Cleanup Radius', default=2, min=0, max=32,
                                description='Confident matte interior radius used by boundary cleanup, in working pixels')
    matte_inset: IntProperty(name='Matte Inset', default=0, min=0, max=32,
                            description='Generation-time inward matte adjustment, in working pixels')
    cleanup_strength: FloatProperty(name='Cleanup Strength', default=1, min=0, max=1,
                                    description='Generation-time blend from raw depth to matte-guided boundary cleanup')
    show_generation_options: BoolProperty(name='Generation Options', default=False)
    last_run_dir: StringProperty(name='Last Run', subtype='DIR_PATH')
    job_status: StringProperty(name='Status', default='Ready')
    job_state: StringProperty(default='ready', options={'HIDDEN'})
    job_kind: StringProperty(default='depth', options={'HIDDEN'})
    job_stage: StringProperty(name='Stage', default='')
    job_progress: FloatProperty(name='Progress', default=0, min=0, max=1, subtype='FACTOR')
    is_running: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    needs_subject: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    subject_id: StringProperty(name='Subject ID', description='ID shown in the worker selection preview')
    preview_path: StringProperty(name='Selection Preview', subtype='FILE_PATH')


class LDR_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    runtime_config: StringProperty(name='Runtime Config', subtype='FILE_PATH',
                                  description='External Python/model runtime JSON. Leave blank to use the local default')

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'runtime_config')
        layout.label(text='Model inference runs in an external Python environment.')
        layout.label(text='Blender imports image sequences and uses native Geometry Nodes.')


class LDR_PT_reference(bpy.types.Panel):
    bl_label = 'Local Depth Reference'
    bl_idname = 'LDR_PT_reference'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Local Depth'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.local_depth_reference
        generate = layout.box()
        generate.label(text='Generate for this shot', icon='FILE_MOVIE')
        generate.prop(settings, 'footage_path')
        generate.prop(settings, 'shot_folder')
        matte = generate.column()
        matte.enabled = not settings.is_running
        matte.prop(settings, 'matte_source')
        if settings.matte_source == 'custom':
            matte.prop(settings, 'matte_path')
            matte.prop(settings, 'matte_channel')
            matte.prop(settings, 'matte_invert')
            matte.label(text='PNG/TIFF sequence, 8-bit or 16-bit.')
            matte.label(text='First matte frame = first footage frame.')
        generate.prop(settings, 'show_generation_options', icon='TRIA_DOWN' if settings.show_generation_options else 'TRIA_RIGHT', emboss=False)
        if settings.show_generation_options:
            generate.prop(settings, 'cleanup_radius')
            generate.prop(settings, 'matte_inset')
            generate.prop(settings, 'cleanup_strength')
        row = generate.row(align=True)
        row.enabled = not settings.is_running
        row.operator('ldr.generate', icon='PLAY')
        if settings.is_running:
            generate.operator('ldr.cancel_generation', icon='CANCEL')
        if settings.last_run_dir:
            generate.label(text=settings.job_status)
            if settings.job_stage:
                generate.label(text=settings.job_stage)
            if settings.is_running:
                generate.progress(factor=settings.job_progress, type='BAR', text=f'{settings.job_progress*100:.0f}%')
            row = generate.row(align=True)
            row.operator('ldr.refresh_status', icon='FILE_REFRESH')
            row.operator('ldr.open_output', icon='FILE_FOLDER')
            if not settings.is_running and not settings.needs_subject and settings.job_state in {'error', 'failed', 'cancelled', 'canceled', 'ready'}:
                generate.operator('ldr.resume_generation', text='Resume Run', icon='PLAY')
        if settings.needs_subject:
            generate.label(text='Choose the foreground subject to continue.', icon='INFO')
            if settings.preview_path:
                generate.operator('ldr.open_preview', icon='IMAGE_DATA')
            generate.prop(settings, 'subject_id')
            generate.operator('ldr.resume_generation', icon='PLAY')

        package = layout.box()
        package.label(text='Completed depth package', icon='PACKAGE')
        package.prop(settings, 'manifest_path')

        ae = layout.box()
        ae.label(text='After Effects export', icon='IMAGE_DATA')
        ae.prop(settings, 'ae_coverage')
        row = ae.row()
        row.enabled = bool(settings.manifest_path) and not settings.is_running
        row.operator('ldr.export_ae', icon='EXPORT')
        ae.label(text='Reuses the depth package; no new generation.')
        if settings.ae_manifest_path:
            row = ae.row()
            row.enabled = False
            row.prop(settings, 'ae_manifest_path')
            ae.operator('ldr.open_ae_output', icon='FILE_FOLDER')

        imported = layout.box()
        imported.label(text='Blender reference', icon='MOD_DISPLACE')
        imported.prop(settings, 'main_camera')
        imported.prop(settings, 'start_frame')
        imported.operator('ldr.import_manifest', icon='IMPORT')
        imported.prop(settings, 'import_collection')
        collection = settings.import_collection
        try:
            objects = geometry.get_import_objects(collection) if collection else None
        except ValueError as exc:
            imported.label(text=str(exc), icon='ERROR')
            objects = None
        if objects:
            row = imported.row(align=True)
            row.operator('ldr.match_camera_settings', text='Match Settings')
            row.operator('ldr.align_and_parent', text='Align & Parent')
            controls = objects['controls']
            imported.prop(controls, '["Subject Distance"]', text='Subject Distance')
            imported.prop(controls, '["Depth Strength"]', text='Depth Strength')
            imported.prop(controls, '["Start Frame"]', text='Start Frame')
            row = imported.row(align=True)
            row.operator('ldr.offset_frames', text='−1').delta = -1
            row.prop(controls, '["Frame Index Offset"]', text='Index Offset')
            row.operator('ldr.offset_frames', text='+1').delta = 1
            imported.label(text='Offset 1 maps file 00000 to Start Frame.', icon='INFO')
            imported.prop(controls, '["Use Clean Depth"]', text='Clean Depth')
            imported.prop(controls, '["Matte Threshold"]', text='Matte Threshold')
            imported.prop(controls, '["Blur Iterations"]', text='Spatial Blur')
            imported.prop(controls, '["Grid Resolution"]', text='Grid Resolution')
            imported.prop(controls, '["Clay View"]', text='Clay View')
            imported.operator('ldr.restore_original', icon='LOOP_BACK')
            imported.label(text='Manual reference only; no rig placement.')
            imported.label(text='Source aspect is fitted inside the camera frame.')
            fps_num = collection.get('ldr_source_fps_num', 0)
            fps_den = collection.get('ldr_source_fps_den', 1)
            source_fps = fps_num / max(fps_den, 1)
            scene_fps = context.scene.render.fps / context.scene.render.fps_base
            if source_fps and abs(source_fps-scene_fps) > .001:
                row = imported.row()
                row.alert = True
                row.label(text=f'Source {source_fps:.3f} fps; scene {scene_fps:.3f} fps.', icon='ERROR')
                imported.label(text='Frames are not resampled; scene timing is preserved.')


CLASSES = (LDR_Settings, LDR_Preferences, LDR_PT_reference)
