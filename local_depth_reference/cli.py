"""Headless Codex entry point; direct-file invocation needs only Python's standard library."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime
from shot_paths import DepthError, load_package, load_ae_package, read_json, resolve_shot


def parser():
    p = argparse.ArgumentParser(description="Generate local depth and export Blender references or AE depth maps.")
    sub = p.add_subparsers(dest="command", required=True)
    resolve = sub.add_parser("resolve", help="Verify the shot and show its depth destination")
    resolve.add_argument("--shot", required=True)
    configure = sub.add_parser("configure", help="Check existing runtime configuration")
    configure.add_argument("--config", default="")
    configure.add_argument("--check", action="store_true")
    configure.add_argument("--matte-source", choices=("automatic", "custom"), default="automatic")
    generate = sub.add_parser("generate", help="Reserve a new shot version and launch the worker")
    generate.add_argument("--source", required=True)
    generate.add_argument("--shot", required=True)
    generate.add_argument("--config", default="")
    generate.add_argument("--settings-json")
    generate.add_argument("--matte-source", choices=("automatic", "custom"))
    generate.add_argument("--matte-path", help="First numbered PNG/TIFF frame matching the first footage frame")
    generate.add_argument("--matte-channel", choices=("luma", "alpha"))
    generate.add_argument("--matte-invert", action=argparse.BooleanOptionalAction, default=None)
    generate.add_argument("--wait", action="store_true")
    export = sub.add_parser("export-ae", help="Export normalized AE maps from a completed Blender depth package without rerunning models")
    export.add_argument("--manifest", required=True)
    export.add_argument("--coverage", choices=("whole_scene", "person", "both"), default="both")
    export.add_argument("--config", default="")
    export.add_argument("--wait", action="store_true")
    for name in ("status", "cancel", "resume"):
        action = sub.add_parser(name)
        action.add_argument("--run", required=True)
        if name == "resume":
            subject = action.add_mutually_exclusive_group()
            subject.add_argument("--subject-id", type=int)
            subject.add_argument("--subject-points")
            action.add_argument("--wait", action="store_true")
    validate = sub.add_parser("validate", help="Validate every frame in a completed depth package")
    validate.add_argument("--manifest", required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "resolve":
            result = resolve_shot(args.shot)
        elif args.command == "configure":
            result = runtime.load_config(args.config, matte_source=args.matte_source)
        elif args.command == "generate":
            options = json.loads(Path(args.settings_json).read_text(encoding="utf-8-sig")) if args.settings_json else {}
            if not isinstance(options, dict):
                raise DepthError("Settings JSON must be an object.")
            for field in ("matte_source", "matte_path", "matte_channel", "matte_invert"):
                value = getattr(args, field)
                if value is not None:
                    options[field] = value
            job = runtime.create_job(args.source, args.shot, config_path=args.config, settings=options)
            result = runtime.launch_job(job["job_path"])
        elif args.command == "status":
            result = runtime.read_progress(args.run)
        elif args.command == "export-ae":
            job = runtime.create_ae_export(args.manifest, args.coverage, config_path=args.config)
            result = runtime.launch_job(job["job_path"])
        elif args.command == "cancel":
            runtime.request_cancel(args.run)
            result = {"status": "cancellation_requested", "run_dir": str(Path(args.run).resolve())}
        elif args.command == "resume":
            points = json.loads(Path(args.subject_points).read_text(encoding="utf-8-sig")) if args.subject_points else None
            result = runtime.resume_job(args.run, args.subject_id, points)
        else:
            data = read_json(args.manifest)
            result = load_ae_package(args.manifest) if isinstance(data, dict) and data.get("type") == "local-depth-ae-export" else load_package(args.manifest)
        if getattr(args, "wait", False):
            result = runtime.wait_job(result["run_dir"])
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        if getattr(args, "wait", False):
            return {"complete": 0, "awaiting_subject": 20, "cancelled": 21}.get(result.get("status"), 1)
        return 0
    except (DepthError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
