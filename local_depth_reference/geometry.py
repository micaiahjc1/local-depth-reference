"""Original native Blender depth-reference geometry. No model dependencies."""
import json
from pathlib import Path
import uuid

import bpy
from mathutils import Matrix

OWNER = 'ldr_owner'
ROLE = 'ldr_role'
IMPORT_MARKER = 'local_depth_reference'
MODIFIER = 'Local Depth Reference'
UV_NAME = 'ldr_source_uv'
CAMERA_UV = 'ldr_camera_uv'
CAMERA_FIELDS = ('type', 'lens', 'sensor_fit', 'sensor_width', 'sensor_height',
                 'shift_x', 'shift_y', 'clip_start', 'clip_end')
CONTROL_DEFS = {
    'Subject Distance': (1.0, .00001, 10000.0, 'Manual scene-unit distance. This is not measured metric depth.'),
    'Depth Strength': (1.0, 0.0, 1.0, 'Inverse-depth contrast: 0 is flat; 1 is the complete relative depth shape.'),
    'Start Frame': (1, -1048574, 1048574, 'Timeline frame at which source index zero begins.'),
    'Frame Index Offset': (1, -100000, 100000, '1 maps first file 00000 to Start Frame. Increase to advance only this import.'),
    'Use Clean Depth': (True, False, True, 'Use prepared boundary cleanup instead of raw model depth.'),
    'Matte Threshold': (.01, .001, 1.0, 'Remove points below this alpha before soft matte shading. 0.95 gives a strict inspection mask.'),
    'Blur Iterations': (2, 0, 8, 'Spatial neighbor smoothing, not temporal stabilization.'),
    'Grid Resolution': (769, 33, 1537, 'Grid vertices on each axis. Lower this for faster viewport evaluation.'),
    'Clay View': (False, False, True, 'Show a neutral lit material to inspect depth instead of source RGB.'),
}


def _tag(data, owner, role):
    data[OWNER] = owner
    data[ROLE] = role
    return data


def is_import(collection):
    return bool(collection and collection.get(IMPORT_MARKER) == 1 and collection.get(OWNER))


def get_import_objects(collection):
    if not is_import(collection):
        raise ValueError('Choose a Local Depth Reference import collection.')
    owner = collection[OWNER]
    result = {}
    for role in ('controls', 'camera', 'surface'):
        matches = [obj for obj in collection.objects
                   if obj.get(OWNER) == owner and obj.get(ROLE) == role]
        if len(matches) != 1:
            raise ValueError('The import has missing or duplicated owned '+role+' objects; no changes were made.')
        result[role] = matches[0]
    if result['camera'].type != 'CAMERA' or result['camera'].data.get(OWNER) != owner:
        raise ValueError('The reference camera no longer owns its camera data; no changes were made.')
    if result['surface'].type != 'MESH':
        raise ValueError('The owned depth surface is no longer a mesh.')
    return result


def _valid_camera(camera, scene=None):
    if not camera or camera.type != 'CAMERA' or camera.data.type != 'PERSP':
        raise ValueError('Choose a perspective main camera.')
    if scene is not None and scene.objects.get(camera.name) != camera:
        raise ValueError('The main camera must belong to the current scene.')


def _camera_values(camera):
    return {key: getattr(camera.data, key) for key in CAMERA_FIELDS}


def _matrix_values(matrix):
    return [list(row) for row in matrix]


def _rigid_matrix(matrix):
    location, rotation, _scale = matrix.decompose()
    return Matrix.LocRotScale(location, rotation, (1, 1, 1))


def _driver(owner, path, variables, expression):
    curve = owner.driver_add(path)
    curve.driver.type = 'SCRIPTED'
    for old in list(curve.driver.variables):
        curve.driver.variables.remove(old)
    for name, data, target_path in variables:
        var = curve.driver.variables.new()
        var.name = name
        var.type = 'SINGLE_PROP'
        var.targets[0].id_type = 'SCENE' if isinstance(data, bpy.types.Scene) else 'OBJECT'
        var.targets[0].id = data
        var.targets[0].data_path = target_path
    curve.driver.expression = expression
    return curve


def _control_driver(owner, path, controls, key):
    return _driver(owner, path, [('value', controls, '["'+key+'"]')], 'value')


def _projection_driver(owner, path, camera, scene, key):
    variables = [('f', camera, 'data.lens'), ('sw', camera, 'data.sensor_width'),
                 ('sh', camera, 'data.sensor_height'), ('fit', camera, 'data.sensor_fit'),
                 ('sx', camera, 'data.shift_x'), ('sy', camera, 'data.shift_y'),
                 ('rx', scene, 'render.resolution_x'), ('ry', scene, 'render.resolution_y'),
                 ('px', scene, 'render.pixel_aspect_x'), ('py', scene, 'render.pixel_aspect_y')]
    aspect = '(rx*px/(ry*py))'
    horizontal = '(fit==1 or (fit==0 and '+aspect+'>=1))'
    sensor = '(sh if fit==2 else sw)'
    sign = '-' if 'Min' in key else '+'
    if key.endswith('X'):
        expression = '(sx'+sign+'0.5*(1 if '+horizontal+' else '+aspect+'))*'+sensor+'/max(f,0.001)'
    else:
        expression = '(sy'+sign+'0.5/('+aspect+' if '+horizontal+' else 1))*'+sensor+'/max(f,0.001)'
    _driver(owner, path, variables, expression)


def _fit_driver(owner, path, scene, controls, key):
    variables = [('source', controls, '["Source Aspect"]'),
                 ('rx', scene, 'render.resolution_x'), ('ry', scene, 'render.resolution_y'),
                 ('px', scene, 'render.pixel_aspect_x'), ('py', scene, 'render.pixel_aspect_y')]
    aspect = '(rx*px/(ry*py))'
    expression = 'min(1,source/'+aspect+')' if key == 'Fit U' else 'min(1,'+aspect+'/source)'
    _driver(owner, path, variables, expression)


def _sequence(path, label, noncolor):
    """Share one Image ID per stream, with independent groups/image users."""
    absolute = Path(path).resolve()
    key = str(absolute).casefold()+'|'+('data' if noncolor else 'color')
    for image in bpy.data.images:
        if image.get('ldr_sequence_key') == key:
            return image
    image = bpy.data.images.load(str(absolute), check_existing=False)
    image.name = 'LDR | '+label
    image['ldr_sequence_key'] = key
    image['ldr_shared_sequence'] = True
    image.source = 'SEQUENCE'
    if noncolor:
        image.colorspace_settings.name = 'Non-Color'
    # Never refresh an evaluated sequence just to rewrite its saved path.
    if bpy.data.filepath:
        image.filepath_raw = bpy.path.relpath(str(absolute))
    return image


def _image_user(node, image, controls, count):
    node.image = image
    node.extension = 'EXTEND'
    user = node.image_user
    # frame_start is not animatable in Blender 4.2. Keep its base fixed and
    # compute the full requested filename index in the animatable offset.
    user.frame_start = 1
    user.frame_duration = count
    user.frame_offset = -1
    user.use_auto_refresh = True
    user.use_cyclic = False
    variables = [('start', controls, '["Start Frame"]'),
                 ('offset', controls, '["Frame Index Offset"]')]
    # Blender sequence indices are literal filename numbers. Both terms clamp,
    # including timeline frames outside the import's source duration.
    expression = ('min(max(frame-start+offset,1),'+str(count)+')-1'
                  '-min(max(frame,1),'+str(count)+')')
    _driver(user, 'frame_offset', variables, expression)


def _material(owner, controls, images, count):
    material = _tag(bpy.data.materials.new('LDR | source or clay'), owner, 'material')
    material.use_nodes = True
    material.diffuse_color = (.5, .54, .6, 1)
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    output.location = (800, 0)
    uv = nodes.new('ShaderNodeAttribute')
    uv.attribute_name = UV_NAME
    uv.location = (-800, 0)
    rgb = nodes.new('ShaderNodeTexImage')
    rgb.label = 'Source RGB — synchronized import timing'
    rgb.location = (-550, 200)
    _image_user(rgb, images['source'], controls, count)
    alpha = nodes.new('ShaderNodeTexImage')
    alpha.label = 'Independent matte — synchronized import timing'
    alpha.location = (-550, -200)
    _image_user(alpha, images['alpha'], controls, count)
    links.new(uv.outputs['Vector'], rgb.inputs['Vector'])
    links.new(uv.outputs['Vector'], alpha.inputs['Vector'])
    emission = nodes.new('ShaderNodeEmission')
    emission.location = (-200, 200)
    links.new(rgb.outputs['Color'], emission.inputs['Color'])
    clay = nodes.new('ShaderNodeBsdfPrincipled')
    clay.location = (-200, 450)
    clay.inputs['Base Color'].default_value = (.5, .54, .6, 1)
    clay.inputs['Roughness'].default_value = .68
    choose = nodes.new('ShaderNodeMixShader')
    choose.label = 'Manual source / clay view'
    choose.location = (150, 200)
    _control_driver(choose.inputs[0], 'default_value', controls, 'Clay View')
    links.new(emission.outputs[0], choose.inputs[1])
    links.new(clay.outputs[0], choose.inputs[2])
    transparent = nodes.new('ShaderNodeBsdfTransparent')
    transparent.location = (150, -150)
    matte = nodes.new('ShaderNodeMixShader')
    matte.location = (500, 0)
    links.new(alpha.outputs['Color'], matte.inputs[0])
    links.new(transparent.outputs[0], matte.inputs[1])
    links.new(choose.outputs[0], matte.inputs[2])
    links.new(matte.outputs[0], output.inputs['Surface'])
    return material


def _socket(group, name, kind, default, minimum=None, maximum=None):
    socket = group.interface.new_socket(name=name, in_out='INPUT', socket_type=kind)
    socket.default_value = default
    if minimum is not None:
        socket.min_value = minimum
    if maximum is not None:
        socket.max_value = maximum
    return socket.identifier


def _geometry(owner, controls, images, count, material):
    group = _tag(bpy.data.node_groups.new('LDR | native image-driven surface', 'GeometryNodeTree'), owner, 'geometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    sockets = {}
    for key, (value, minimum, maximum, _) in CONTROL_DEFS.items():
        if key == 'Clay View':
            continue
        kind = 'NodeSocketBool' if isinstance(value, bool) else 'NodeSocketInt' if isinstance(value, int) else 'NodeSocketFloat'
        sockets[key] = _socket(group, key, kind, controls[key], None if isinstance(value, bool) else minimum,
                               None if isinstance(value, bool) else maximum)
    for key in ('Ray Min X', 'Ray Max X', 'Ray Min Y', 'Ray Max Y', 'Fit U', 'Fit V'):
        sockets[key] = _socket(group, key, 'NodeSocketFloat', 1 if key.startswith('Fit') else 0)
    nodes, links = group.nodes, group.links
    inp = nodes.new('NodeGroupInput')
    inp.location = (-1300, 650)
    out = nodes.new('NodeGroupOutput')
    out.location = (1500, 450)
    index = 0
    def math_node(operation, a, b=None, label=''):
        nonlocal index
        node = nodes.new('ShaderNodeMath')
        node.operation = operation
        node.label = label
        node.location = (-400+(index % 7)*180, -500-(index//7)*180)
        index += 1
        for i, value in enumerate((a, b)):
            if value is not None:
                if isinstance(value, (float, int)):
                    node.inputs[i].default_value = value
                else:
                    links.new(value, node.inputs[i])
        return node.outputs[0]
    def store(name, data_type, geometry, value):
        node = nodes.new('GeometryNodeStoreNamedAttribute')
        node.data_type = data_type
        node.domain = 'POINT'
        node.inputs['Name'].default_value = name
        links.new(geometry, node.inputs['Geometry'])
        links.new(value, node.inputs['Value'])
        return node.outputs['Geometry']
    def named(name, data_type):
        node = nodes.new('GeometryNodeInputNamedAttribute')
        node.data_type = data_type
        node.inputs['Name'].default_value = name
        return node.outputs['Attribute']
    grid = nodes.new('GeometryNodeMeshGrid')
    grid.location = (-1300, 300)
    grid.inputs['Size X'].default_value = 2
    grid.inputs['Size Y'].default_value = 2
    resolution = math_node('MINIMUM', math_node('MAXIMUM', inp.outputs['Grid Resolution'], 33), 1537)
    links.new(resolution, grid.inputs['Vertices X'])
    links.new(resolution, grid.inputs['Vertices Y'])
    position = nodes.new('GeometryNodeInputPosition')
    uv = nodes.new('ShaderNodeVectorMath')
    uv.operation = 'MULTIPLY_ADD'
    uv.inputs[1].default_value = (.5, .5, 0)
    uv.inputs[2].default_value = (.5, .5, 0)
    links.new(position.outputs[0], uv.inputs[0])
    geometry = store(UV_NAME, 'FLOAT_VECTOR', grid.outputs['Mesh'], uv.outputs[0])
    source_uv = named(UV_NAME, 'FLOAT_VECTOR')
    time = nodes.new('GeometryNodeInputSceneTime')
    logical_frame = math_node('ADD', math_node('SUBTRACT', time.outputs['Frame'], inp.outputs['Start Frame']), inp.outputs['Frame Index Offset'])
    file_index = math_node('SUBTRACT', math_node('MINIMUM', math_node('MAXIMUM', logical_frame, 1), count), 1,
                           'Logical image frame 1 → filename 00000')
    textures = {}
    for i, key in enumerate(('raw_depth', 'clean_depth', 'alpha')):
        node = nodes.new('GeometryNodeImageTexture')
        node.name = 'LDR '+key
        node.label = key.replace('_', ' ').title()
        node.location = (-1000, 150-i*280)
        node.inputs['Image'].default_value = images[key]
        node.interpolation = 'Linear'
        node.extension = 'EXTEND'
        links.new(source_uv, node.inputs['Vector'])
        links.new(file_index, node.inputs['Frame'])
        textures[key] = node.outputs['Color']
    choose = nodes.new('GeometryNodeSwitch')
    choose.input_type = 'FLOAT'
    links.new(inp.outputs['Use Clean Depth'], choose.inputs['Switch'])
    links.new(textures['raw_depth'], choose.inputs['False'])
    links.new(textures['clean_depth'], choose.inputs['True'])
    blur = nodes.new('GeometryNodeBlurAttribute')
    blur.data_type = 'FLOAT'
    links.new(choose.outputs[0], blur.inputs['Value'])
    links.new(math_node('MINIMUM', math_node('MAXIMUM', inp.outputs['Blur Iterations'], 0), 8), blur.inputs['Iterations'])
    blur.inputs['Weight'].default_value = 1
    sample = math_node('MAXIMUM', blur.outputs['Value'], .000001)
    strength = math_node('MINIMUM', math_node('MAXIMUM', inp.outputs['Depth Strength'], 0), 1)
    distance = math_node('MAXIMUM', inp.outputs['Subject Distance'], .00001)
    denominator = math_node('ADD', math_node('SUBTRACT', 1, strength), math_node('DIVIDE', strength, sample))
    z = math_node('DIVIDE', distance, denominator, 'Z = D / ((1-S) + S/sampleZ)')
    geometry = store('ldr_depth_z', 'FLOAT', geometry, z)
    split = nodes.new('ShaderNodeSeparateXYZ')
    links.new(source_uv, split.inputs['Vector'])
    fit_uv = nodes.new('ShaderNodeCombineXYZ')
    for axis, key in [('X', 'Fit U'), ('Y', 'Fit V')]:
        fit = math_node('ADD', math_node('MULTIPLY', math_node('SUBTRACT', split.outputs[axis], .5), inp.outputs[key]), .5)
        links.new(fit, fit_uv.inputs[axis])
    geometry = store(CAMERA_UV, 'FLOAT_VECTOR', geometry, fit_uv.outputs[0])
    camera_split = nodes.new('ShaderNodeSeparateXYZ')
    links.new(named(CAMERA_UV, 'FLOAT_VECTOR'), camera_split.inputs['Vector'])
    ray = nodes.new('ShaderNodeCombineXYZ')
    for axis in ('X', 'Y'):
        width = math_node('SUBTRACT', inp.outputs['Ray Max '+axis], inp.outputs['Ray Min '+axis])
        coord = math_node('ADD', math_node('MULTIPLY', camera_split.outputs[axis], width), inp.outputs['Ray Min '+axis])
        links.new(coord, ray.inputs[axis])
    ray.inputs['Z'].default_value = -1
    scale = nodes.new('ShaderNodeVectorMath')
    scale.operation = 'SCALE'
    links.new(ray.outputs[0], scale.inputs[0])
    links.new(named('ldr_depth_z', 'FLOAT'), scale.inputs['Scale'])
    set_position = nodes.new('GeometryNodeSetPosition')
    links.new(geometry, set_position.inputs['Geometry'])
    links.new(scale.outputs[0], set_position.inputs['Position'])
    delete = nodes.new('GeometryNodeDeleteGeometry')
    delete.domain = 'POINT'
    delete.mode = 'ALL'
    threshold = math_node('MINIMUM', math_node('MAXIMUM', inp.outputs['Matte Threshold'], .001), 1)
    links.new(math_node('LESS_THAN', textures['alpha'], threshold), delete.inputs['Selection'])
    links.new(set_position.outputs['Geometry'], delete.inputs['Geometry'])
    assign = nodes.new('GeometryNodeSetMaterial')
    assign.inputs['Material'].default_value = material
    links.new(delete.outputs['Geometry'], assign.inputs['Geometry'])
    smooth = nodes.new('GeometryNodeSetShadeSmooth')
    links.new(assign.outputs['Geometry'], smooth.inputs['Geometry'])
    links.new(smooth.outputs['Geometry'], out.inputs['Geometry'])
    return group, sockets


def _copy_camera(source, destination):
    _valid_camera(source)
    bpy.context.view_layer.update()
    evaluated = source.evaluated_get(bpy.context.evaluated_depsgraph_get())
    for key in CAMERA_FIELDS:
        setattr(destination.data, key, getattr(evaluated.data, key))


def create_import(context, package, main_camera, start_frame=1):
    """Add an owned reference without changing scene settings or selection."""
    _valid_camera(main_camera, context.scene)
    count = int(package['frame_count'])
    if count < 1:
        raise ValueError('The manifest has no frames.')
    streams = package['streams']
    for key in ('raw_depth', 'clean_depth', 'alpha', 'source'):
        first = Path(streams[key]).resolve()
        if not first.is_file():
            raise ValueError('Missing '+key+' first frame: '+str(first))
        if first.stem != '00000':
            raise ValueError('Expected zero-indexed five-digit sequences beginning 00000: '+str(first))
        if not first.with_name(f'{count-1:05d}'+first.suffix).is_file():
            raise ValueError('Missing '+key+' final frame.')
    owner = uuid.uuid4().hex
    images_before = {image.as_pointer() for image in bpy.data.images}
    collection = _tag(bpy.data.collections.new('Local Depth Reference'), owner, 'collection')
    collection[IMPORT_MARKER] = 1
    collection['ldr_manifest'] = str(Path(package['manifest_path']).resolve())
    collection['ldr_shot_root'] = str(package.get('shot_root', ''))
    collection['ldr_frame_count'] = count
    collection['ldr_source_size'] = [int(package['width']), int(package['height'])]
    collection['ldr_source_fps_num'] = int(package.get('fps_num', 0))
    collection['ldr_source_fps_den'] = int(package.get('fps_den', 1))
    created = []
    try:
        context.scene.collection.children.link(collection)
        controls = _tag(bpy.data.objects.new('Depth Controls', None), owner, 'controls')
        created.append(controls)
        collection.objects.link(controls)
        controls.empty_display_type = 'PLAIN_AXES'
        controls.empty_display_size = .1
        for key, (value, minimum, maximum, description) in CONTROL_DEFS.items():
            controls[key] = int(start_frame) if key == 'Start Frame' else value
            if isinstance(value, bool):
                controls.id_properties_ui(key).update(description=description)
            else:
                controls.id_properties_ui(key).update(min=minimum, max=maximum, description=description,
                    soft_min=minimum, soft_max=min(maximum, 10) if key == 'Subject Distance' else maximum)
        aspect = float(package.get('source_aspect', package['width']*package.get('pixel_aspect', 1.0)/package['height']))
        if not .001 < aspect < 1000:
            raise ValueError('Invalid source aspect ratio.')
        controls['Source Aspect'] = aspect
        controls['Depth Formula'] = 'Z = D / ((1-S) + S/max(sampleZ,0.000001)); manual scale, not metric depth'
        controls['Timing'] = 'filename index = clamp(frame - Start Frame + Frame Index Offset,1,N)-1'
        controls['Frame Fit'] = 'Centered FIT preserves source aspect without changing the scene resolution.'
        camera_data = _tag(bpy.data.cameras.new('Depth Reference Camera'), owner, 'camera_data')
        camera = _tag(bpy.data.objects.new('Depth Reference Camera', camera_data), owner, 'camera')
        created.append(camera)
        collection.objects.link(camera)
        _copy_camera(main_camera, camera)
        context.view_layer.update()
        camera.matrix_world = _rigid_matrix(main_camera.evaluated_get(context.evaluated_depsgraph_get()).matrix_world)
        camera.hide_render = True
        images = {key: _sequence(streams[key], key, key != 'source') for key in streams
                  if key in ('raw_depth', 'clean_depth', 'alpha', 'source')}
        material = _material(owner, controls, images, count)
        mesh = _tag(bpy.data.meshes.new('Depth Reference procedural input'), owner, 'surface_data')
        surface = _tag(bpy.data.objects.new('Depth Reference Surface', mesh), owner, 'surface')
        created.append(surface)
        collection.objects.link(surface)
        surface.parent = camera
        surface.matrix_parent_inverse = Matrix.Identity(4)
        surface.matrix_basis = Matrix.Identity(4)
        group, sockets = _geometry(owner, controls, images, count, material)
        modifier = surface.modifiers.new(MODIFIER, 'NODES')
        modifier.node_group = group
        for key, identifier in sockets.items():
            path = '["'+identifier+'"]'
            if key in CONTROL_DEFS:
                modifier[identifier] = controls[key]
                _control_driver(modifier, path, controls, key)
            elif key.startswith('Fit'):
                _fit_driver(modifier, path, context.scene, controls, key)
            else:
                _projection_driver(modifier, path, camera, context.scene, key)
        snapshot = {'controls': {key: controls[key] for key in CONTROL_DEFS},
                    'camera': _camera_values(camera), 'camera_matrix': _matrix_values(camera.matrix_world),
                    'controls_matrix': _matrix_values(controls.matrix_world),
                    'surface_matrix_basis': _matrix_values(surface.matrix_basis)}
        collection['ldr_original'] = json.dumps(snapshot)
        collection['ldr_main_camera'] = main_camera
        return collection
    except Exception:
        # Roll back only objects and data created for this UUID. Never purge
        # unrelated or shared user data, and never remove files on disk.
        for obj in reversed(created):
            if obj.name in bpy.data.objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        if collection.name in bpy.data.collections:
            bpy.data.collections.remove(collection)
        for data_collection in (bpy.data.node_groups, bpy.data.materials, bpy.data.meshes, bpy.data.cameras):
            for data in list(data_collection):
                if data.get(OWNER) == owner and data.users == 0:
                    data_collection.remove(data)
        for image in list(bpy.data.images):
            if image.as_pointer() not in images_before and image.get('ldr_shared_sequence') and image.users == 0:
                bpy.data.images.remove(image)
        raise


def match_camera_settings(collection, main_camera):
    _valid_camera(main_camera)
    objects = get_import_objects(collection)
    if main_camera == objects['camera']:
        raise ValueError('Choose the scene main camera, not the import reference camera.')
    _copy_camera(main_camera, objects['camera'])
    collection['ldr_main_camera'] = main_camera


def align_and_parent(collection, main_camera):
    _valid_camera(main_camera)
    objects = get_import_objects(collection)
    camera = objects['camera']
    ancestor = main_camera
    while ancestor:
        if ancestor == camera:
            raise ValueError('That camera would create a parenting cycle.')
        ancestor = ancestor.parent
    bpy.context.view_layer.update()
    matrix = main_camera.evaluated_get(bpy.context.evaluated_depsgraph_get()).matrix_world.copy()
    if abs(matrix.determinant()) < 1e-10:
        raise ValueError('The main camera has zero scale; it cannot provide a valid parent transform.')
    camera.parent = main_camera
    # Camera projection ignores object scale. Cancel the current parent's scale
    # while preserving its location/rotation; rerun alignment after scale edits.
    camera.matrix_parent_inverse = matrix.inverted() @ _rigid_matrix(matrix)
    camera.matrix_basis = Matrix.Identity(4)
    collection['ldr_main_camera'] = main_camera


def restore_original(collection):
    objects = get_import_objects(collection)
    snapshot = json.loads(collection['ldr_original'])
    controls, camera, surface = (objects[key] for key in ('controls', 'camera', 'surface'))
    preserved_actions = []
    def detach_animation(data, keep_modifier_drivers=False):
        animation = data.animation_data
        if not animation:
            return
        if animation.action:
            action = animation.action
            if action.users == 1:
                action.use_fake_user = True
            preserved_actions.append(action.name)
            animation.action = None
        for track in animation.nla_tracks:
            track.mute = True
        for curve in animation.drivers:
            if not (keep_modifier_drivers and curve.data_path.startswith('modifiers[')):
                curve.mute = True
    # Imported assets have no original user animation. Detach/mute later
    # animation only on these owned assets; retain Action IDs for recovery.
    detach_animation(controls)
    detach_animation(camera)
    detach_animation(camera.data)
    detach_animation(surface, keep_modifier_drivers=True)
    for key, value in snapshot['controls'].items():
        controls[key] = value
    controls.parent = None
    controls.matrix_world = Matrix(snapshot['controls_matrix'])
    camera.parent = None
    camera.matrix_parent_inverse = Matrix.Identity(4)
    camera.matrix_world = Matrix(snapshot['camera_matrix'])
    for key, value in snapshot['camera'].items():
        setattr(camera.data, key, value)
    surface.parent = camera
    surface.matrix_parent_inverse = Matrix.Identity(4)
    surface.matrix_basis = Matrix(snapshot['surface_matrix_basis'])
    controls.update_tag()
    return {'preserved_actions': preserved_actions}
