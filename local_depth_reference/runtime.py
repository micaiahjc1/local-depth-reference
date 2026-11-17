"""External worker lifecycle. Blender never imports torch or modifies its Python environment."""
from __future__ import annotations

import json
from contextlib import contextmanager
import os
import re
from pathlib import Path
import subprocess
import sys
import time

try:
    from .shot_paths import DepthError, atomic_json, read_json, resolve_shot, reserve_run, validate_binding, sha256_file
except ImportError:  # CLI direct execution
    from shot_paths import DepthError, atomic_json, read_json, resolve_shot, reserve_run, validate_binding, sha256_file

DEFAULT_SETTINGS = {"working_max_width": 1280, "vda_input_size": 518, "subject_prompt": "person",
                    "subject_id": None, "subject_points": [], "matte_short_side": 1400,
                    "matte_warmup": 5, "matte_reset_interval": 8,
                    "matte_source": "automatic", "matte_path": "", "matte_channel": "luma", "matte_invert": False,
                    "edge_radius": 2, "edge_inset": 0, "cleanup_strength": 1.0}
_PROCESSES = {}


def default_config_path():
    return str(Path.home() / ".codex" / "runtime" / "blender-depth-reference" / "runtime.json")


def load_config(path="", for_export=False, matte_source="automatic"):
    path = Path(path or default_config_path()).expanduser().resolve()
    config = read_json(path)
    runtime = config.get("runtime", config)
    if not isinstance(runtime, dict):
        raise DepthError("Runtime configuration must be a JSON object.")
    if matte_source not in ("automatic", "custom"):
        raise DepthError("Matte source must be automatic or custom.")
    required = ("vda_python",) if for_export else ("vda_python", "vda_repo", "vda_checkpoint")
    if not for_export and matte_source == "automatic":
        required += ("matting_python", "sam3_module_root", "sam3_checkpoint", "matanyone_root", "matanyone_checkpoint")
    for key in required:
        if not runtime.get(key) or not Path(runtime[key]).exists():
            raise DepthError(f"Runtime {key} is missing. Update {path}.")
    result = dict(config)
    result["runtime"] = dict(runtime)
    result["config_path"] = str(path)
    # Installed ZIP contains a worker subfolder inside the add-on package.
    filename = "ae_export.py" if for_export else "process.py"
    candidates = [Path(__file__).parent / "worker" / filename, Path(__file__).parent.parent / "worker" / filename]
    override = config.get("ae_export_worker" if for_export else "worker")
    if override:
        candidates.insert(0, Path(override))
    worker = next((p.resolve() for p in candidates if p.is_file()), None)
    if not worker:
        raise DepthError("Depth worker is missing; reinstall the add-on or update runtime.json.")
    result["worker"] = str(worker)
    return result


def create_job(source, shot_dir=None, scene_binding=None, blend_path="", config_path="", settings=None):
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise DepthError(f"Source video does not exist: {source}")
    if settings is not None and not isinstance(settings, dict):
        raise DepthError("Generation settings must be a JSON object.")
    options = dict(DEFAULT_SETTINGS)
    options.update(settings or {})
    if options["matte_source"] not in ("automatic", "custom"):
        raise DepthError("Matte source must be automatic or custom.")
    if options["matte_channel"] not in ("luma", "alpha"):
        raise DepthError("Matte channel must be luma or alpha.")
    if not isinstance(options["matte_invert"], bool):
        raise DepthError("Matte invert must be true or false.")
    if options["matte_source"] == "custom":
        if not isinstance(options["matte_path"], str) or not options["matte_path"].strip():
            raise DepthError("Choose the first PNG or TIFF frame of your matte sequence.")
        first = Path(options["matte_path"]).expanduser().resolve()
        if not first.is_file() or first.suffix.lower() not in (".png", ".tif", ".tiff") or not re.search(r"\d+$", first.stem):
            raise DepthError("Custom matte must start with an existing numbered PNG or TIFF image.")
        options["matte_path"] = str(first)
    config = load_config(config_path, matte_source=options["matte_source"])
    binding = resolve_shot(shot_dir, scene_binding, blend_path)
    run = reserve_run(binding)
    job = {"schema": 1, "source": str(source), "shot_dir": binding["shot_dir"], "run_dir": str(run),
           "binding": binding, "runtime": config["runtime"], "settings": options,
           "worker": config["worker"], "config_path": config["config_path"], "created_at": time.time()}
    atomic_json(run / "job.json", job)
    atomic_json(run / "progress.json", {"status": "ready", "stage": "ready", "message": "Ready to generate", "updated_at": time.time()})
    return {**job, "job_path": str(run / "job.json")}


def create_ae_export(manifest_path, coverage="both", config_path=""):
    """Reserve an AE derivative without changing the completed Blender package.

    Only metadata is checked on the caller/UI thread. The external exporter
    validates every source frame and hash before publishing any final manifest.
    """
    if coverage not in ("whole_scene", "person", "both"):
        raise DepthError("AE coverage must be whole_scene, person or both.")
    path = Path(manifest_path).expanduser().resolve()
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema") != 1 or data.get("type") != "local-depth-reference" or data.get("status") != "complete":
        raise DepthError("Choose a completed Blender depth package as the AE export source.")
    if not isinstance(data.get("binding"), dict) or not data["binding"].get("shot_dir"):
        raise DepthError("The source package has no shot binding for its AE export.")
    for field in ("shot_dir", "resources_dir", "depth_root"):
        if not isinstance(data["binding"].get(field), str) or not data["binding"][field]:
            raise DepthError(f"The source package has an invalid shot binding: missing {field}.")
    binding = validate_binding(data["binding"])
    config = load_config(config_path, for_export=True)
    fingerprint = sha256_file(path)
    run = reserve_run(binding)
    job = {"schema": 1, "kind": "ae_export", "source_manifest": str(path),
           "source_manifest_sha256": fingerprint, "shot_dir": binding["shot_dir"],
           "run_dir": str(run), "binding": binding, "runtime": config["runtime"],
           "settings": {"ae_coverage": coverage}, "worker": config["worker"],
           "config_path": config["config_path"], "created_at": time.time()}
    atomic_json(run / "job.json", job)
    atomic_json(run / "progress.json", {"status": "ready", "stage": "ready", "job_kind": "ae_export",
                                       "message": "Ready to export AE maps", "updated_at": time.time()})
    return {**job, "job_path": str(run / "job.json")}


def _pid_alive(pid):
    if pid in _PROCESSES:
        process = _PROCESSES[pid]
        if process.poll() is None:
            return True
        del _PROCESSES[pid]
        return False
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _validate_run(run):
    run = Path(run).expanduser().resolve()
    job = read_json(run / "job.json")
    if Path(job.get("run_dir", "")).resolve() != run:
        raise DepthError("Job destination does not match its containing run folder.")
    if not isinstance(job.get("binding"), dict) or not isinstance(job.get("settings"), dict) or not isinstance(job.get("runtime"), dict):
        raise DepthError("Malformed job: binding, settings and runtime must be JSON objects.")
    if job.get("kind", "depth") not in ("depth", "ae_export"):
        raise DepthError("Unsupported depth job kind.")
    binding = validate_binding(job["binding"])
    if run.parent != Path(binding["depth_root"]).resolve() or not re.fullmatch(r"v\d{3,}", run.name):
        raise DepthError("Job is not inside its bound shot resources/depth map version folder.")
    return run, job


@contextmanager
def _launcher_lock(path):
    """OS locks release on process exit, so a crashed launcher cannot strand a run."""
    with Path(path).open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            raise DepthError("Another launcher is starting this job; retry shortly.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def launch_job(job_path):
    run, job = _validate_run(Path(job_path).parent)
    if (run / "manifest.json").exists():
        raise DepthError("This run is complete. Generate a new version to change it.")
    with _launcher_lock(run / "launcher.lock"):
        if (run / "manifest.json").exists():
            raise DepthError("This run is complete. Generate a new version to change it.")
        old = read_json(run / "process.json") if (run / "process.json").is_file() else {}
        if _pid_alive(old.get("pid")):
            raise DepthError("This depth job is already running.")
        (run / "cancel.flag").unlink(missing_ok=True)
        command = [job["runtime"]["vda_python"], job["worker"], "--job", str(run / "job.json")]
        kwargs = {"cwd": str(Path(job["worker"]).parent), "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        atomic_json(run / "progress.json", {"status": "running", "stage": "launch", "fraction": 0,
                                            "message": "Starting depth worker", "updated_at": time.time()})
        try:
            with (run / "worker.log").open("ab", buffering=0) as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **kwargs)
        except OSError as exc:
            atomic_json(run / "progress.json", {"status": "error", "stage": "launch", "message": str(exc)})
            raise
        _PROCESSES[process.pid] = process
        handle = {"pid": process.pid, "run_dir": str(run), "job_path": str(run / "job.json"), "started_at": time.time()}
        atomic_json(run / "process.json", handle)
        return handle


def read_progress(run_dir):
    run = Path(run_dir).expanduser().resolve()
    result = read_json(run / "progress.json") if (run / "progress.json").is_file() else {"status": "unknown"}
    handle = read_json(run / "process.json") if (run / "process.json").is_file() else {}
    alive = _pid_alive(handle.get("pid"))
    result.update({"run_dir": str(run), "alive": alive, "pid": handle.get("pid")})
    job = read_json(run / "job.json") if (run / "job.json").is_file() else {}
    result["job_kind"] = job.get("kind", "depth")
    if result.get("status") in ("running", "ready", "unknown") and handle and not alive:
        result.update(status="error", message="Worker stopped before completion. Review worker.log, then Resume.")
    if result.get("status") == "awaiting_subject" and (run / "awaiting_subject.json").is_file():
        result["subject_selection"] = read_json(run / "awaiting_subject.json")
    if (run / "manifest.json").is_file():
        result["manifest_path"] = str(run / "manifest.json")
        committed = read_json(run / "manifest.json")
        expected_type = "local-depth-ae-export" if result["job_kind"] == "ae_export" else "local-depth-reference"
        if committed.get("schema") == 1 and committed.get("type") == expected_type and committed.get("status") == "complete":
            # Publication is the transaction commit. A crash before the final
            # progress update must not strand an already validated package.
            result.update(status="complete", fraction=1.0,
                          message="AE depth maps ready" if result["job_kind"] == "ae_export" else "Depth reference ready")
            if result["job_kind"] == "ae_export":
                result["ae_manifest_path"] = str(run / "manifest.json")
    return result


def request_cancel(run_dir):
    run, _job = _validate_run(run_dir)
    if (run / "manifest.json").exists():
        raise DepthError("This run has already completed.")
    (run / "cancel.flag").touch()


def resume_job(run_dir, subject_id=None, subject_points=None):
    run, job = _validate_run(run_dir)
    if (run / "manifest.json").exists():
        raise DepthError("Completed versions are immutable; generate a new version.")
    state = read_progress(run)
    if state.get("alive"):
        raise DepthError("Wait for the existing worker to stop before resuming.")
    if job.get("kind") == "ae_export":
        if subject_id is not None or subject_points is not None:
            raise DepthError("AE export reuses its source package's selected person; it cannot change subjects.")
        return launch_job(run / "job.json")
    if job["settings"].get("matte_source", "automatic") == "custom" and (subject_id is not None or subject_points is not None):
        raise DepthError("A supplied matte defines its subject; resume it without subject selection.")
    if subject_id is not None:
        subject_id = int(subject_id)
        if subject_id < 0:
            raise DepthError("Subject ID must be nonnegative.")
        job["settings"]["subject_id"] = subject_id
        job["settings"]["subject_points"] = []
    if subject_points is not None:
        if not isinstance(subject_points, list):
            raise DepthError("Subject points must be a JSON list.")
        job["settings"]["subject_points"] = subject_points
        job["settings"]["subject_id"] = None
    atomic_json(run / "job.json", job)
    return launch_job(run / "job.json")


def wait_job(run_dir):
    """CLI-only; never called on Blender's UI thread."""
    while True:
        state = read_progress(run_dir)
        if not state.get("alive"):
            return state
        time.sleep(0.5)
