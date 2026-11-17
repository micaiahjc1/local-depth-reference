"""Shot binding and portable depth package validation. Pure Python; no Blender imports."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import time
import uuid


class DepthError(ValueError):
    """An actionable input or package error."""


def read_json(path):
    try:
        # Atomic replacement and antivirus indexing can briefly deny readers
        # on Windows. A progress poll must not abort a healthy worker for that.
        for attempt in range(20):
            try:
                with Path(path).open("r", encoding="utf-8-sig") as stream:
                    result = json.load(stream)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.025)
    except (OSError, ValueError) as exc:
        raise DepthError(f"Cannot read JSON {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise DepthError(f"Expected a JSON object: {path}")
    return result


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        for attempt in range(20):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.025)
    finally:
        temporary.unlink(missing_ok=True)


def contained(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    try:
        path.relative_to(parent)
    except ValueError as exc:
        raise DepthError(f"Path escapes its destination: {path} (expected inside {parent})") from exc
    return path


def _child(parent, name):
    matches = [p for p in parent.iterdir() if p.name.casefold() == name.casefold()]
    if len(matches) > 1:
        raise DepthError(f"Ambiguous {name} folders in {parent}")
    child = matches[0] if matches else parent / name
    if child.exists() and not child.is_dir():
        raise DepthError(f"Expected a folder: {child}")
    contained(child, parent)
    return child


def resolve_shot(shot_dir=None, scene_binding=None, blend_path=""):
    """Resolve an explicit shot or verified BlenderAE link. Never infer from video/cwd."""
    link = dict(scene_binding or {})
    link_path = link.get("manifest_path")
    explicit = bool(shot_dir and str(shot_dir).strip())
    if explicit:
        root = Path(shot_dir).expanduser().resolve()
    elif link_path:
        if str(link_path).startswith("//"):
            if not blend_path:
                raise DepthError("Save the Blender file or select an explicit shot folder.")
            link_path = Path(blend_path).resolve().parent / str(link_path)[2:]
        root = Path(link_path).expanduser().resolve().parent
    else:
        raise DepthError("Select a shot folder or use a scene with a verified BlenderAE shot link.")
    if not root.is_dir():
        raise DepthError(f"Shot folder does not exist: {root}")
    manifest = root / "shot.json"
    data = read_json(manifest) if manifest.exists() else None
    if data is None and not explicit:
        raise DepthError(f"Shot manifest is missing: {manifest}")
    if data is not None:
        if data.get("schema") != "blenderae.shot" or data.get("schema_version") != 1:
            raise DepthError(f"Unsupported shot manifest: {manifest}")
        if not data.get("shot_id") or not data.get("project_id"):
            raise DepthError("Shot manifest must contain project_id and shot_id.")
        try:
            for field in ("shot_id", "project_id"):
                uuid.UUID(data[field])
        except (ValueError, TypeError, AttributeError) as exc:
            raise DepthError("Shot manifest project_id and shot_id must be valid UUID strings.") from exc
        # Explicit selection can target a different shot, but an automatic link must agree.
        if not explicit:
            if link_path and Path(link_path).resolve() != manifest.resolve():
                raise DepthError("The scene link does not point to this shot.json.")
            for field in ("shot_id", "project_id"):
                if not link.get(field) or link[field] != data[field]:
                    raise DepthError(f"Scene {field} disagrees with shot.json; select the intended shot explicitly.")
    resources = _child(root, "resources")
    depth_root = _child(resources, "depth map") if resources.exists() else resources / "depth map"
    result = {"shot_dir": str(root), "resources_dir": str(resources), "depth_root": str(depth_root),
              "binding_kind": "blenderae.shot" if data is not None else "explicit_folder"}
    if data is not None:
        result.update({"manifest_path": str(manifest), "shot_id": data["shot_id"],
                       "project_id": data["project_id"], "shot_label": data.get("label", root.name),
                       "shot_revision": data.get("revision"), "ae": data.get("ae", {})})
    return result


def validate_binding(binding):
    """Recheck the destination and UUIDs immediately before writing."""
    current = resolve_shot(binding.get("shot_dir"))
    for field in ("shot_id", "project_id"):
        if binding.get(field) and binding[field] != current.get(field):
            raise DepthError(f"Shot binding changed: {field} no longer matches.")
    for field in ("resources_dir", "depth_root"):
        if Path(binding[field]).resolve() != Path(current[field]).resolve():
            raise DepthError(f"Shot destination changed: {field} no longer matches.")
    return current


def reserve_run(binding):
    binding = validate_binding(binding)
    root = Path(binding["depth_root"])
    root.mkdir(parents=True, exist_ok=True)
    contained(root, binding["resources_dir"])
    existing = [int(p.name[1:]) for p in root.iterdir() if re.fullmatch(r"v\d{3,}", p.name)]
    number = max(existing, default=0) + 1
    while True:
        run = root / f"v{number:03d}"
        try:
            run.mkdir()
            return run
        except FileExistsError:
            number += 1


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _positive_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DepthError(f"Invalid {label}: {value!r}")
    return value


def load_package(manifest_path):
    """Validate all sequence members before creating any Blender datablocks."""
    path = Path(manifest_path).expanduser().resolve()
    data = read_json(path)
    if data.get("schema") != 1 or data.get("type") != "local-depth-reference" or data.get("status") != "complete":
        raise DepthError("Select a completed Local Depth Reference manifest.json.")
    run = path.parent
    for field in ("source", "processing", "sequence", "paths", "binding"):
        if not isinstance(data.get(field), dict):
            raise DepthError(f"Package {field} must be a JSON object.")
    source, processing, sequence = data.get("source", {}), data.get("processing", {}), data.get("sequence", {})
    count = _positive_int(source.get("frame_count"), "source frame count")
    width = _positive_int(source.get("width"), "source width")
    height = _positive_int(source.get("height"), "source height")
    fps_num = _positive_int(source.get("fps_num"), "FPS numerator")
    fps_den = _positive_int(source.get("fps_den"), "FPS denominator")
    ww = _positive_int(processing.get("working_width"), "working width")
    wh = _positive_int(processing.get("working_height"), "working height")
    if sequence.get("first_index") != 0 or sequence.get("count") != count:
        raise DepthError("Package sequences must begin at zero and match the source frame count.")
    digits = _positive_int(sequence.get("digits"), "sequence padding")
    if digits > 12 or count > 10000000:
        raise DepthError("Sequence dimensions exceed supported limits.")
    try:
        pixel_aspect = float(source.get("pixel_aspect", 1.0))
    except (ValueError, TypeError) as exc:
        raise DepthError("Invalid source pixel aspect.") from exc
    if not math.isfinite(pixel_aspect) or pixel_aspect <= 0:
        raise DepthError("Invalid source pixel aspect.")
    records = {}
    cache = data.get("stage_cache")
    if not isinstance(cache, dict):
        raise DepthError("Package has no frame integrity inventory. Regenerate it with Local Depth Reference.")
    for stage in cache.values():
        if not isinstance(stage, dict) or not isinstance(stage.get("artifacts"), list):
            raise DepthError("Malformed package integrity inventory.")
        for item in stage["artifacts"]:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise DepthError("Malformed package frame integrity record.")
            records[item["path"].replace("\\", "/")] = item
    streams = {}
    for name in ("raw_depth", "clean_depth", "alpha", "source"):
        value = data.get("paths", {}).get(name)
        if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts:
            raise DepthError(f"Invalid relative stream path: {name}")
        first = contained(run / value, run)
        expected_suffix = ".exr" if "depth" in name else ".png"
        if first.stem != f"{0:0{digits}d}" or first.suffix.lower() != expected_suffix:
            raise DepthError(f"Invalid first sequence file: {first}")
        for index in range(count):
            member = first.parent / f"{index:0{digits}d}{first.suffix}"
            contained(member, run)
            if not member.is_file() or not member.stat().st_size:
                raise DepthError(f"Missing or empty {name} frame {index}: {member}")
            record = records.get(member.relative_to(run).as_posix())
            if not record or record.get("bytes") != member.stat().st_size or record.get("sha256") != sha256_file(member):
                raise DepthError(f"Frame integrity check failed for {name} frame {index}: {member}")
        extra = first.parent / f"{count:0{digits}d}{first.suffix}"
        if extra.exists():
            raise DepthError(f"Unexpected extra sequence frame: {extra}")
        streams[name] = str(first)
    binding = data.get("binding", {})
    # Packages remain portable: record their original binding, do not require the old disk.
    return {"manifest_path": str(path), "run_dir": str(run), "shot_root": binding.get("shot_dir", ""),
            "resources_dir": binding.get("resources_dir", ""), "frame_count": count,
            "width": width, "height": height, "working_width": ww, "working_height": wh,
            "fps_num": fps_num, "fps_den": fps_den, "pixel_aspect": pixel_aspect,
            "streams": streams, "metadata": data, "binding": binding}


def load_ae_package(manifest_path):
    """Validate a portable normalized AE export, never a Blender geometry input."""
    import struct
    path = Path(manifest_path).expanduser().resolve()
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema") != 1 or data.get("type") != "local-depth-ae-export" or data.get("status") != "complete":
        raise DepthError("Select a completed Local Depth Reference AE export manifest.json.")
    for key in ("source", "processing", "sequence", "streams", "binding", "encoding"):
        if not isinstance(data.get(key), dict):
            raise DepthError(f"AE export {key} must be a JSON object.")
    source, processing, sequence = data["source"], data["processing"], data["sequence"]
    count = _positive_int(source.get("frame_count"), "source frame count")
    fps_num = _positive_int(source.get("fps_num"), "FPS numerator")
    fps_den = _positive_int(source.get("fps_den"), "FPS denominator")
    width = _positive_int(processing.get("working_width"), "working width")
    height = _positive_int(processing.get("working_height"), "working height")
    digits = _positive_int(sequence.get("digits"), "sequence padding")
    if sequence.get("first_index") != 0 or sequence.get("count") != count or digits > 12 or count > 10000000:
        raise DepthError("Invalid AE sequence frame numbering or count.")
    expected = {"whole_scene": {"whole_scene"}, "person": {"person"}, "both": {"whole_scene", "person"}}.get(data.get("coverage"))
    if expected is None or set(data["streams"]) != expected:
        raise DepthError("AE export coverage does not match its streams.")
    if data["encoding"].get("dtype") != "uint16" or data["encoding"].get("person_depth_premultiplied") is not False:
        raise DepthError("AE maps must be unpremultiplied 16-bit depth data.")
    inventory = data.get("inventory")
    if not isinstance(inventory, list):
        raise DepthError("AE export has no frame integrity inventory.")
    records = {}
    for item in inventory:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise DepthError("Malformed AE frame integrity record.")
        key = item["path"].replace("\\", "/")
        if key in records:
            raise DepthError("Duplicate AE frame integrity record.")
        records[key] = item
    streams = {}
    for name, stream in data["streams"].items():
        if not isinstance(stream, dict):
            raise DepthError("Malformed AE stream description.")
        try:
            near, far = float(stream["near"]), float(stream["far"])
        except (KeyError, ValueError, TypeError) as exc:
            raise DepthError("Invalid AE fixed depth range.") from exc
        if not math.isfinite(near) or not math.isfinite(far) or far <= near:
            raise DepthError("AE fixed depth range must be finite and increasing.")
        for field in (("path", "alpha_path") if name == "person" else ("path",)):
            value = stream.get(field)
            if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts:
                raise DepthError("Invalid relative AE sequence path.")
            first = contained(path.parent / value, path.parent)
            if first.name != f"{0:0{digits}d}.png":
                raise DepthError("AE sequences must start with zero-numbered PNGs.")
            for index in range(count):
                member = contained(first.parent / f"{index:0{digits}d}.png", path.parent)
                if not member.is_file():
                    raise DepthError(f"Missing AE {name} {field} frame {index}: {member}")
                record = records.get(member.relative_to(path.parent).as_posix())
                if not record or record.get("bytes") != member.stat().st_size or record.get("sha256") != sha256_file(member):
                    raise DepthError(f"AE frame integrity check failed: {member}")
                with member.open("rb") as handle:
                    header = handle.read(26)
                if (len(header) != 26 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR" or
                        struct.unpack(">II", header[16:24]) != (width, height) or header[24:26] != bytes((16, 0))):
                    raise DepthError(f"AE frame is not a native-size 16-bit grayscale PNG: {member}")
            if (first.parent / f"{count:0{digits}d}.png").exists():
                raise DepthError("Unexpected extra AE sequence frame.")
            streams[name if field == "path" else "person_alpha"] = str(first)
    return {"manifest_path": str(path), "run_dir": str(path.parent), "coverage": data["coverage"],
            "frame_count": count, "fps_num": fps_num, "fps_den": fps_den,
            "working_width": width, "working_height": height, "streams": streams,
            "metadata": data, "binding": data["binding"]}
