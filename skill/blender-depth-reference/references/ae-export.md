# After Effects depth-map export

Use this mode when the user wants depth mattes, fog, depth-based grading or other AE effects from an existing local depth package. The export job creates image sequences and metadata; it does not open or edit an AE project.

## Reuse the completed base

Accept a validated `local-depth-reference` base manifest, including packages made with add-on 0.1.0. Keep its float EXRs, source RGB, selected-person matte and existing boundary cleanup unchanged. No model inference, new matte, gentle spatial filter or Blender scene export is needed. The original video, model weights, Blender camera and AE installation are not required for this conversion; the completed base assets and the configured export Python dependencies are required.

Read the base's shot binding and source/matte provenance. The new AE version belongs under that same shot's `Resources/depth map`, with existing casing preserved. Do not retarget it to an active AE comp or change its subject coverage. The matte may be automatically generated or supplied by the user; reuse the base's recorded choice. A different matte or subject requires an appropriate new base package, not a different subject ID or replacement matte on an AE resume.

## Run the supported export

Use the installed CLI located through the runtime configuration as described in [execution](execution.md). Substitute verified absolute paths:

```text
python "<tool_root>/local_depth_reference/cli.py" validate --manifest "<completed base manifest.json>"
python "<tool_root>/local_depth_reference/cli.py" export-ae --manifest "<completed base manifest.json>" --coverage both
python "<tool_root>/local_depth_reference/cli.py" status --run "<returned AE vNNN directory>"
python "<tool_root>/local_depth_reference/cli.py" validate --manifest "<completed AE vNNN>/manifest.json"
```

Coverage is `whole_scene`, `person`, or `both`; omitted coverage defaults to `both`. Use the requested coverage rather than treating the default as permission to expand a person-only or whole-scene-only request. The CLI reserves a new version, returns its exact run directory and retains the base manifest as provenance. Do not rerun `generate` or start a second export to poll the first.

`export-ae` accepts `--config <runtime.json>` and `--wait`. Omitted config uses `~/.codex/runtime/blender-depth-reference/runtime.json`. Export needs the configured VDA Python environment's image/numeric dependencies, not the model or matting environments. The full-generation configuration check should not become an unnecessary requirement for a valid export-only setup.

Use the same `status`, `cancel --run <AE vNNN>` and `resume --run <AE vNNN>` commands as other jobs. An AE resume has no subject-selection step. `--wait` on export/resume returns exit 0 for complete, 21 for cancelled or 1 for error. A cancelled or failed export must finish through the supported resume path and pass validation before delivery. Completed versions are immutable.

## Keep the two data products distinct

| Coverage | Input from the base | Exported sequence | Intended coverage |
| --- | --- | --- | --- |
| Whole scene | Raw float relative Z | `ae/whole_scene/00000.png` | Entire plate, including background |
| Person | Existing boundary-cleaned float relative Z | `ae/person/00000.png` plus `ae/alpha/00000.png` | Selected person, isolated with the separate matte |

Depth exports are 16-bit PNG data: black 0 is near and white 1 is far within the recorded range. Each coverage uses its own fixed range across the whole shot, with no per-frame normalization. Whole-scene and person gray values therefore are not interchangeable physical distances. Retain the recorded range and clipping diagnostics with the sequence. These maps are not suitable substitutes for Blender's positive float Z inputs.

The person map is unpremultiplied depth data. Use its separate alpha for coverage; multiplying depth by alpha would change the values at soft edges. Background pixels in that person data are not a whole-scene depth solution. No new smoothing is applied during export.

The separate AE person alpha preserves a 16-bit base matte without reducing it to 8-bit. An 8-bit base matte is encoded as 16-bit by multiplying by 257; that encoding adds no precision. Automatic matting currently produces 8-bit coverage, while supplied PNG/TIFF mattes may be 8- or 16-bit. Report the actual input/output precision and any earlier resize from the base metadata. Whole-scene depth remains unmasked raw depth regardless of matte source. Depth/Range remapping and inversion in AE belong before the person silhouette matte so transparent background stays excluded.

The completed AE manifest has `type: "local-depth-ae-export"`, `status: "complete"`, source provenance and exact timing, output dimensions, fixed range records, and validated artifact inventories. `streams.whole_scene.path` and `streams.person.path` identify the first depth files; the person stream's `alpha_path` identifies its matte. Paths are relative to the AE manifest. Coverage controls which streams exist; do not assume both are present. `validate` handles either base or AE manifest type, but only the base manifest is a Blender geometry import.

## Check and hand off

Validate all recorded frames and inspect the first, last and an occlusion or fast-motion frame. Confirm matching frame count/order and matte timing, 16-bit depth data, a stable shot-wide mapping, and the recorded output size. Export keeps the source's rational FPS and frame order at the base's working resolution; do not label the output as original-source resolution or recovered high-resolution detail.

If AE import is explicitly requested, interpret the depth PNGs as data with Preserve RGB and explicitly conform the sequence to the manifest's source FPS. Do the same timing check for the separate matte; do not accept AE's default image-sequence frame rate. Validate the 16-bit values in a 16- or 32-bpc review project. Preserve production-project bit depth and color-management choices unless the requested task includes changing them; route these data assets appropriately. Effect-specific inversion or remapping belongs in the comp and should not overwrite the exported maps.

Report the AE manifest and first sequence files, requested coverage, exact FPS, frame count, exported dimensions and any clipping or visible boundary artifacts. Leave the base package available for Blender. A cleaner or higher-bit-depth image does not prove more accurate geometry.
