# Local worker and Blender handoff

This reference covers base generation and Blender delivery. For normalized After Effects maps from a completed base package, use [AE export](ae-export.md); it does not repeat model inference or change Blender's float depth.

Use the Local Depth Reference add-on's installed `local_depth_reference/cli.py` as the orchestration entry point. Runtime configuration lives at `~/.codex/runtime/blender-depth-reference/runtime.json`; its `tool_root` locates the CLI and its worker paths locate the actual processing environment. Do not recreate depth inference, matting, version reservation or cache logic in ad hoc scripts.

The CLI can run directly with Python without importing `bpy`. Substitute verified absolute paths below; quote paths containing spaces. It writes structured JSON to stdout.

```text
python "<tool_root>/local_depth_reference/cli.py" configure --config "<runtime.json>" --check
python "<tool_root>/local_depth_reference/cli.py" resolve --shot "<shotdir>"
python "<tool_root>/local_depth_reference/cli.py" generate --source "<video>" --shot "<shotdir>"
python "<tool_root>/local_depth_reference/cli.py" status --run "<returned vNNN directory>"
python "<tool_root>/local_depth_reference/cli.py" validate --manifest "<completed manifest.json>"
```

`generate` also accepts `--config <runtime.json>` and `--settings-json <file>`. Omitted config uses the default path above. Without `--wait`, generation returns a job handle; retain its exact run directory and poll `status` without rerunning generation. This keeps native Blender access free while the worker runs. With `--wait`, generate/resume returns the worker's final exit code. Use the nonblocking form for long runs or a tool session that yields regularly.

Automatic matting is the default. For a supplied matte, follow [custom mattes](custom-matte.md) and pass `--matte-source custom --matte-path <first numbered image> --matte-channel luma|alpha`, optionally `--matte-invert`. Use `configure --check --matte-source custom` for its runtime check. Custom generation validates only the VDA/depth runtime dependencies and bypasses SAM3, MatAnyone2 and subject selection. Do not require an automatic-matting configuration check to succeed before a custom-only run. Explicit matte flags on `generate` override the corresponding settings JSON fields.

To continue a subject-selection pause or request cancellation:

```text
python "<tool_root>/local_depth_reference/cli.py" resume --run "<vNNN>" --subject-id <reviewed ID>
python "<tool_root>/local_depth_reference/cli.py" cancel --run "<vNNN>"
```

For automatic matting, the supported alternative `resume --subject-points <path.json>` uses the schema in that run's `awaiting_subject.json`. It is a list of records with zero-based `frame`, `positive` XY pairs and optional `negative` XY pairs, all in **original source pixel coordinates**, not the smaller working preview. Inspect the source and candidate preview before choosing points. Do not pass both subject-choice forms. A custom run resumes with its supplied matte settings and no subject ID or point prompts. `resume` also supports `--wait`. Use an already completed package directly after validation rather than starting a duplicate run.

## Worker contract

The CLI delegates to the bundled worker through `process.py --job <absolute job.json>`. The job binds `source`, `shot_dir`, `run_dir`, runtime/model paths, settings and the verified target binding. Source, source-to-shot timing, matte source and subject coverage belong to the run; neither the active Blender scene nor an AE selection may replace them mid-run.

Worker exit states:

| Exit | Meaning | Next action |
| --- | --- | --- |
| `0` | Complete | Validate the final package, then import if requested. |
| `20` | Awaiting subject | Inspect candidate previews; submit the selected actual ID through the supported CLI continuation. |
| `21` | Cancelled | Retain existing results; do not import a partial package. |
| `1` | Error | Read recorded error/progress; resolve the cause before a supported retry. |

Use the worker's progress record and cooperative cancellation mechanism. Do not infer completion from a quiet log, an existing EXR or elapsed time. Cancel only the task-owned run, not other Blender/render/GPU processes. Reuse a cache only when the CLI validates the source fingerprint, settings, subject and model lineage. A resume keeps the same binding; a changed source or subject requires the corresponding invalidation or a new version.

The completed package uses `manifest.json` with `schema: 1`, `type: "local-depth-reference"`, and `status: "complete"`. It records:

- `source`: path, SHA-256, width, height, FPS numerator/denominator, frame count.
- `processing`: `working_width`, `working_height`, shot-wide inverse-depth normalization `q_median`, and the automatic or supplied matte's provenance.
- `paths`: first relative files for `raw_depth`, `clean_depth`, `alpha`, `source`.
- `sequence`: `first_index: 0`, count and five filename digits.
- `binding`: the run's actual shot/project/source identities where established.

The first frame paths normally are `depth/raw_z/00000.exr`, `depth/clean_z/00000.exr`, `alpha/00000.png`, and `source/00000.png`. Resolve them relative to the final manifest, validate their bounds/completeness, and retain the worker's validation/provenance records. The CLI and import validate every final stream member against its recorded byte count and SHA-256 inventory; keep that inventory with a portable package. Do not change `status` to make an incomplete result importable. This manifest is not a BlenderAE `shot.json` or managed processing receipt.

## Depth interpretation

VDA relative output is not measured metric distance. A fixed shot-wide normalization produces positive sample Z; manual Subject Distance and Depth Strength control its scene representation. Keep raw and cleaned sequences available. Depth and matte are Non-Color data; source RGB uses its verified color interpretation. Preserve frame rate and order without frame duplication or a second one-frame content shift.

Check source/depth/alpha together at the beginning, end and a difficult frame. Confirm a stable selected subject, matching silhouette/timing, finite positive depth and readable sequences. Inspect narrow limbs, hair, occlusion and foreground/background discontinuities. Edge cleanup extrapolates from confident foreground; a cleaner silhouette does not prove correct anatomical or hidden depth. If the user explicitly supplies a matte from another tool, retain that provenance rather than labeling it independently generated. Do not substitute external depth or fitted coefficients for the local depth model's output.

## Blender delivery

Use the installed add-on's supported manifest import with the resolved scene and existing main camera. Bind the operator context explicitly when multiple scenes/windows exist. Do not import by constructing another custom geometry implementation inside the skill.

For a verified target scene and camera in the connected Blender session:

```python
import bpy

# Resolve these names from the named shot and existing scene; do not use defaults.
scene = bpy.data.scenes[target_scene_name]
camera = bpy.data.objects[main_camera_name]
assert camera.type == 'CAMERA' and camera.name in scene.objects
settings = scene.local_depth_reference
settings.main_camera = camera
settings.start_frame = intended_first_scene_frame
settings.manifest_path = absolute_manifest_path
with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
    result = bpy.ops.ldr.import_manifest('EXEC_DEFAULT', filepath=absolute_manifest_path)
if result != {'FINISHED'}:
    raise RuntimeError('Depth reference import failed; inspect the operator report.')
owned_collection = settings.import_collection
```

Choose `intended_first_scene_frame` from the explicit source-to-scene timing; the default first scene frame is 1, while sequence filenames start at 00000. The default Frame Index Offset of 1 maps `Start Frame` to file 00000; it does not add another physical one-frame shift. The import operator validates the package and sets `import_collection` to its new collection. Target that owned import for subsequent `ldr.match_camera_settings`, `ldr.align_and_parent`, `ldr.offset_frames` or `ldr.restore_original` actions; inspect their exposed arguments before invoking, and never route them to an unrelated collection.

The import must create only its owned reference collection and controls. Verify image-frame mapping, matching projection and parent transform, raw/clean switching, manual distance/strength, frame offset and Restore Original on the imported setup. Restore Original refers to the import's captured state; it is not a global undo of artist edits. Frame offset must move the import's depth, matte and source RGB together while leaving the main scene and rigs unchanged.

Do not require an add-on or script auto-run merely for native image-sequence geometry to evaluate in a delivered scene. Reopen a saved candidate under Blender 4.2 when a portable file is requested and check relative media paths. Keep Cycles selected for saved projects. An import into a live project should be short and preserve active viewers, selection, playhead and unrelated edits; inference and review belong outside that live session.
