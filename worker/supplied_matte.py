"""Read artist-supplied numbered mattes without models or source-file changes."""
from pathlib import Path
import re
import time

from common import file_hash, digest, sequence_files


def read_coverage(path, width, height):
    import cv2
    import numpy as np
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None or image.dtype not in (np.uint8, np.uint16) or image.shape != (height, width):
        raise ValueError('Coverage must be 8- or 16-bit grayscale with declared dimensions: ' + str(path))
    return image


def coverage_scale(image):
    """Convert legacy 8-bit threshold values without quantizing the coverage."""
    import numpy as np
    if image.dtype == np.uint8:
        return 1
    if image.dtype == np.uint16:
        return 257
    raise ValueError('Unsupported coverage precision')


def _decode(path):
    import cv2
    import numpy as np
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError('Cannot decode supplied matte: ' + str(path))
    if image.dtype not in (np.uint8, np.uint16):
        raise ValueError('Supplied mattes must use unsigned 8- or 16-bit PNG/TIFF pixels: ' + str(path))
    if image.ndim == 2:
        channels = 1
    elif image.ndim == 3 and image.shape[2] in (3, 4):
        channels = image.shape[2]
    else:
        raise ValueError('Supplied matte must be grayscale, RGB or RGBA: ' + str(path))
    return image, channels


def inspect_sequence(ctx, source):
    """Pin the exact N-frame window, validating every frame before conversion."""
    settings = ctx.settings
    first = Path(settings['matte_path']).expanduser().resolve(strict=True)
    match = re.fullmatch(r'(.*?)(\d+)(\.[^.]+)', first.name)
    if not match or first.suffix.lower() not in ('.png', '.tif', '.tiff'):
        raise ValueError('Choose the first numbered PNG or TIFF of the supplied matte sequence')
    prefix, number, suffix = match.groups()
    start = int(number)
    count = source['frame_count']
    width, height = source['working_width'], source['working_height']
    inventory = []
    spec = None
    for index in range(count):
        ctx.cancel()
        path = first.with_name(f'{prefix}{start + index:0{len(number)}d}{suffix}').resolve()
        if path.is_relative_to(ctx.run):
            raise ValueError('Supplied matte inputs must be outside this output run')
        if not path.is_file():
            raise ValueError(f'Supplied matte sequence is missing source frame {index}: {path.name}')
        before = file_hash(path, ctx.cancel)
        image, channels = _decode(path)
        h, w = image.shape[:2]
        current = (w, h, image.dtype.name, channels)
        if spec is None:
            spec = current
            if (w, h) != (width, height) and abs(h - w * source['height'] / source['width']) > 1.000001:
                raise ValueError('Supplied matte aspect ratio does not match the source; bake the same crop/orientation first')
        elif spec != current:
            raise ValueError('Supplied matte dimensions, bit depth and channels must stay consistent across the sequence')
        if settings['matte_channel'] == 'alpha' and channels != 4:
            raise ValueError('Alpha channel was selected, but a supplied matte frame has no alpha channel: ' + path.name)
        if file_hash(path, ctx.cancel) != before:
            raise ValueError('Supplied matte changed during inspection: ' + path.name)
        inventory.append({'path': str(path), 'bytes': path.stat().st_size, 'sha256': before,
                          'source_index': index, 'file_index': start + index})
        ctx.progress('matte', .2 * (index + 1) / count,
                     f'Checking supplied matte {index + 1}/{count}', frame=index + 1, frame_count=count)
    w, h, dtype, channels = spec
    return {'source_type': 'custom', 'first_frame': str(first), 'first_file_index': start,
            'frames': count, 'channel': settings['matte_channel'], 'invert': settings['matte_invert'],
            'input_resolution': [w, h], 'output_resolution': [width, height],
            'input_dtype': dtype, 'output_dtype': dtype, 'input_channels': channels,
            'resampling': 'none' if (w, h) == (width, height) else
                          'area' if width <= w and height <= h else 'linear',
            'luma_formula': '0.2126 R + 0.7152 G + 0.0722 B on stored values; no color/profile transform',
            'frame_mapping': 'selected first numbered file = source decoded frame 0; consume N consecutive frames; no retiming',
            'window_policy': 'Files before/after the selected N-frame window are not consumed',
            'source_fps_num': source['fps_num'], 'source_fps_den': source['fps_den'],
            'inventory': inventory}


def verify_sequence(ctx, snapshot):
    for item in snapshot['inventory']:
        ctx.cancel()
        path = Path(item['path'])
        if path.is_relative_to(ctx.run) or not path.is_file() or path.stat().st_size != item['bytes'] or file_hash(path, ctx.cancel) != item['sha256']:
            raise ValueError('Supplied matte changed during processing: ' + path.name)


def import_sequence(ctx, key, snapshot):
    """Copy only the selected channel to the run, preserving integer precision."""
    import cv2
    import numpy as np
    from process import clear_owned
    start = time.perf_counter()
    verify_sequence(ctx, snapshot)
    folder = ctx.run / 'matte'
    clear_owned(ctx, folder)
    width, height = snapshot['output_resolution']
    for index, item in enumerate(snapshot['inventory']):
        ctx.cancel()
        path = Path(item['path'])
        if file_hash(path, ctx.cancel) != item['sha256']:
            raise ValueError('Supplied matte changed before decoding: ' + path.name)
        image, channels = _decode(path)
        if snapshot['channel'] == 'alpha':
            if channels != 4:
                raise ValueError('Supplied matte alpha channel disappeared')
            coverage = image[:, :, 3].copy()
        elif channels == 1:
            coverage = image.copy()
        else:
            coverage = np.rint(image[:, :, 2].astype(np.float64) * .2126 +
                               image[:, :, 1].astype(np.float64) * .7152 +
                               image[:, :, 0].astype(np.float64) * .0722).astype(image.dtype)
        if snapshot['invert']:
            coverage = np.iinfo(coverage.dtype).max - coverage
        if coverage.shape != (height, width):
            interpolation = cv2.INTER_AREA if snapshot['resampling'] == 'area' else cv2.INTER_LINEAR
            coverage = cv2.resize(coverage, (width, height), interpolation=interpolation)
        target = folder / f'{index:05d}.png'
        if not cv2.imwrite(str(target), coverage, [cv2.IMWRITE_PNG_COMPRESSION, 2]):
            raise IOError('Supplied matte write failed')
        decoded = read_coverage(target, width, height)
        if not np.array_equal(decoded, coverage):
            raise ValueError('Supplied matte PNG readback changed its pixels')
        if file_hash(path, ctx.cancel) != item['sha256']:
            raise ValueError('Supplied matte changed during decoding: ' + path.name)
        ctx.progress('matte', .2 + .8 * (index + 1) / snapshot['frames'],
                     f'Importing supplied matte {index + 1}/{snapshot["frames"]}', frame=index + 1, frame_count=snapshot['frames'])
    verify_sequence(ctx, snapshot)
    data = {**snapshot, 'sequence_sha256': digest(snapshot), 'seconds': time.perf_counter() - start,
            'models_used': False, 'source_files_unchanged': True,
            'postprocess': 'Selected channel, optional inversion, and recorded resize only; no generated refinement'}
    return ctx.finish_stage('matte', key, sequence_files(folder, snapshot['frames'], '.png'), data)
