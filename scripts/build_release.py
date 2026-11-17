"""Build a clean legacy Blender 4.2 add-on ZIP and a matching Codex skill ZIP."""
import ast
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
module = ROOT / "local_depth_reference"
tree = ast.parse((module / "__init__.py").read_text(encoding="utf-8"))
info = next(ast.literal_eval(node.value) for node in tree.body
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "bl_info" for t in node.targets))
version = ".".join(map(str, info["version"]))
dest = ROOT / "dist"
dest.mkdir(exist_ok=True)


def archive(path, entries):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as out:
        for source, name in sorted(entries, key=lambda row: row[1]):
            if source.suffix == ".py":
                compile(source.read_text(encoding="utf-8"), str(source), "exec")
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 16, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            out.writestr(info, source.read_bytes())
    return {"path": str(path), "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


entries = [(p, "local_depth_reference/" + p.relative_to(module).as_posix()) for p in module.glob("*.py")]
entries += [(p, "local_depth_reference/worker/" + p.name) for p in (ROOT / "worker").glob("*.py")]
entries += [(ROOT / name, "local_depth_reference/" + name) for name in ("LICENSE", "README.md", "runtime.example.json")]
addon = archive(dest / f"local-depth-reference-{version}-blender-4.2.zip", entries)
skill_dir = ROOT / "skill/blender-depth-reference"
skill = archive(dest / f"blender-depth-reference-skill-{version}.zip",
                [(p, "blender-depth-reference/" + p.relative_to(skill_dir).as_posix()) for p in skill_dir.rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts])
report = {"version": version, "addon": addon, "skill": skill}
(dest / "release.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, indent=2))
