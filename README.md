# Local Depth Reference for Blender 4.2 and After Effects

Generate local depth once, then use the completed package as an editable Blender reference, export separate After Effects depth maps, or do both. Blender keeps the existing floating-point depth and subject-boundary cleanup for manual head and arm alignment. AE receives normalized 16-bit maps for depth mattes, fog or grading. There is no automatic rig placement, pose solver or rig editing.

The workflow uses Video Depth Anything Small for relative depth. Choose automatic performer matting with SAM3 and MatAnyone2, or supply your own matte sequence. Processing runs in external Python environments, keeping Blender responsive and leaving its Python installation unchanged. Beeble is not required.

## Install and use

1. In Blender 4.2, open **Edit → Preferences → Add-ons → Install from Disk**, select the release ZIP, and enable **Local Depth Reference**.
2. Open the **Local Depth** tab in the 3D View sidebar. Select the source video and intended shot folder, or use its existing verified BlenderAE scene link. A main camera is needed only when importing a Blender reference.
3. Choose the **Matte Source**: automatic generation, or your own numbered PNG/TIFF sequence. For a custom matte, select its first frame, choose luminance or alpha, and enable inversion only if black represents the subject. Generate the depth package. The destination is always **`<shot>/<existing Resources casing>/depth map/vNNN`**. A new run reserves a new version. Completed versions are immutable.
4. In automatic mode, if more than one subject is detected, inspect the subject preview, enter the desired ID and resume. Never combine multiple performers by accident. The Codex CLI also accepts positive and negative point prompts in original source pixels. A supplied matte defines its own subject coverage and skips automatic tracking and refinement.
5. Choose **Import Blender Reference**, **Export AE Maps**, or both from the same completed base. For Blender, select the main perspective camera and use **Match Camera Settings**, **Align & Parent**, and the import's controls. Adjust subject distance and depth strength manually to fit your scene. The source's depth is relative, not measured meters.
6. For a Blender import, set Start Frame and Frame Index Offset. The default offset **1 actually maps file `00000` to Start Frame**. Each increment advances only this import's depth, matte and source RGB together. Frames outside the clip hold the first or last image. **Restore Original** restores this import's captured camera and controls. These Blender controls do not retime or recalibrate an AE export.

The add-on preserves the scene's FPS, resolution, render percentage, pixel aspect, active camera, timeline range and playhead. Camera matching changes the owned reference camera; the artist's main camera and rigs remain untouched. Different source/render aspect ratios fit centrally without stretching. The importer uses one source frame per scene frame; it does not silently resample when their FPS differs.

Camera settings are copied when you press Match Settings. Align & Parent follows camera motion and compensates for its current object scale. Run matching/alignment again after changing lens settings or camera scale. Restore Original detaches later animation on the import's owned controls/camera/surface and retains the Action datablocks for recovery; existing artist animation is never altered.

Raw/clean depth, source/clay display, matte threshold, spatial blur and grid density remain adjustable. Cleanup radius, matte inset and cleanup strength are generation options. All controls apply only to the selected depth import.

## Supply your own matte

Use an 8- or 16-bit numbered PNG, TIF or TIFF sequence. Select the frame corresponding to the first source-video frame; the sequence must cover the full clip without gaps and already match its timing, crop and orientation. Image sequences carry no FPS metadata, so the worker maps one matte frame to each decoded video frame without retiming. Matte dimensions, channel count and bit depth must stay consistent, with the source's aspect ratio. Matching mattes are resized to the package's working dimensions, with the conversion recorded in the manifest. Files before the selected first frame or after the clip's required window are ignored. Video mattes and EXR mattes are not supported in this release; export a PNG/TIFF sequence first.

Choose **Luminance** for a grayscale mask stored in the image's color values, or **Alpha** for an RGBA image's transparency channel. Alpha mode requires an alpha channel. White means foreground and black means background; **Invert Matte** reverses that convention. A supplied matte bypasses SAM3 and MatAnyone2, while VDA still generates depth. The worker reads and fingerprints the input sequence without changing its files.

Keep a mostly opaque subject interior: the clip needs a confident foreground core at alpha at least 250/255 after cleanup radius/inset. An empty clip matte or one with only translucent coverage is rejected; soft silhouette edges are supported.

The provided coverage guides the existing Blender boundary cleanup and becomes the separate AE person matte. Its 16-bit precision is retained through the base and AE exports; an 8-bit matte retains its original precision. Automatic generation remains the default and retains its existing 8-bit coverage. Whole-scene AE depth still covers the entire shot using raw depth; it is not masked down to the person.

Better roto can improve silhouettes and edge cleanup. It cannot correct predicted depth inside the subject or recover hidden geometry. Supply a matte covering the intended subject, then inspect its alignment with the source at the first, last and a difficult motion frame. Changing the matte for a completed package requires a new base version so both exports use the same coverage and provenance.

## Separate AE export

**Export AE Maps** converts a validated completed base package, including packages from add-on 0.1.0, into a new version under the same shot's `Resources/depth map`. Choose **Whole Scene**, **Person**, or **Both**. It does not rerun AI, change the base's Blender assets, apply gentle smoothing, or edit an AE project. The main camera, original video, model weights and AE installation are not needed for this conversion.

| Output | Input | Delivered data |
| --- | --- | --- |
| Blender reference | Existing raw/clean base depth | Positive float32 relative-Z EXRs and editable native geometry |
| AE whole scene | Base raw depth | `ae/whole_scene/00000.png`, 16-bit normalized depth including background |
| AE person | Base boundary-cleaned depth | `ae/person/00000.png`, 16-bit normalized depth, plus separate `ae/alpha/00000.png` |

AE depth is black 0 near and white 1 far. Whole scene and person use separate fixed ranges across the shot, recorded in the export manifest; values are not measured meters or interchangeable between those ranges. The person depth is unpremultiplied: apply its separate alpha for coverage instead of multiplying the depth values by alpha. Use the original float EXRs for Blender displacement.

The AE person matte comes from the base package's chosen automatic or supplied matte. A 16-bit base alpha is preserved as 16-bit; an 8-bit alpha is encoded as 16-bit by multiplying by 257, which adds no precision. Keep depth remapping and inversion before person coverage in AE so the background remains excluded.

AE exports preserve the exact source FPS and decoded frame order at the base's **working resolution**. The manifest records both original source dimensions and delivered dimensions. When importing into AE, use Preserve RGB for the depth data and explicitly conform each image sequence to the recorded FPS; do not accept a default 30 fps interpretation. Review in a 16- or 32-bpc project to retain depth precision. These exports add no model detail and can retain motion, occlusion, hair and depth-boundary artifacts.

The two deliverables have separate manifests: `local-depth-reference` for the Blender base and `local-depth-ae-export` for AE. Keep the base for later Blender imports and use the AE manifest's stream paths for compositing. Completed versions remain immutable; status, cancellation and resume apply to the newly reserved AE export job.

## Local model runtime

This workstation is configured at **`%USERPROFILE%/.codex/runtime/blender-depth-reference/runtime.json`** using the already tested local environments and weights. The add-on preferences can point to another configuration. The ZIP bundles the worker but does not bundle Python, CUDA, FFmpeg or large model weights.

For another workstation, copy `runtime.example.json` and provide the installed paths. The VDA environment needs its official repository, PyTorch/CUDA, NumPy, OpenCV, OpenEXR, SciPy and Pillow. Automatic matting additionally needs SAM3 and MatAnyone2 plus their dependencies and checkpoints. Supplied-matte generation does not require those matting models or their Python environment. FFmpeg and FFprobe must be on PATH or configured as absolute executable paths. The VDA Small relative checkpoint is the supported depth model in this release.

The tested workstation uses Windows, Blender 4.2.7, an NVIDIA RTX 5090, and VDA PyTorch 2.7.1+cu128. This release is intended for this configured local setup; it is not an automatic model installer.

The worker supports constant-frame-rate, square-pixel video. V1 rejects rotation metadata, variable frame rate, non-square-pixel video, empty clips and changing frame dimensions with an actionable error. Bake rotation or convert the source deliberately before processing. Portrait footage with actual portrait pixels is supported.

## Codex skill and CLI

The skill source is `skill/blender-depth-reference`. Install it under `%USERPROFILE%/.codex/skills/blender-depth-reference`. The skill name remains compatible with existing invocations and now covers both exports. Example: “Use blender-depth-reference to generate depth for this shot, import a Blender reference, and export both whole-scene and person AE maps.” An existing completed base can be used directly for AE export.

Direct CLI commands need only standard-library Python; inference runs in the configured model environments:

```text
python local_depth_reference/cli.py configure --check
python local_depth_reference/cli.py resolve --shot "D:/Project/Shot02"
python local_depth_reference/cli.py generate --source "D:/Project/Shot02/Footage/source.mp4" --shot "D:/Project/Shot02"
python local_depth_reference/cli.py generate --source "D:/Project/Shot02/Footage/source.mp4" --shot "D:/Project/Shot02" --matte-source custom --matte-path "D:/Project/Shot02/Roto/matte_01001.png" --matte-channel luma
python local_depth_reference/cli.py status --run "D:/Project/Shot02/Resources/depth map/v001"
python local_depth_reference/cli.py resume --run "D:/Project/Shot02/Resources/depth map/v001" --subject-id 1
python local_depth_reference/cli.py cancel --run "D:/Project/Shot02/Resources/depth map/v001"
python local_depth_reference/cli.py validate --manifest "D:/Project/Shot02/Resources/depth map/v001/manifest.json"
python local_depth_reference/cli.py export-ae --manifest "D:/Project/Shot02/Resources/depth map/v001/manifest.json" --coverage both
```

`generate`, `export-ae` and `resume` return immediately by default. Add `--wait` for a command-line job: exit 0 means complete, 20 means subject selection needed during base generation, 21 means cancelled, and 1 means error. `export-ae` accepts `--coverage whole_scene|person|both` (default `both`) and `--config <runtime.json>`. Keep the returned run directory for `status`, `cancel` or `resume`; AE export has no subject-selection step. `validate` accepts either completed manifest type.

`generate --matte-source automatic` selects the default automatic workflow. For a supplied matte, use `--matte-source custom --matte-path <first numbered image> --matte-channel luma|alpha`, optionally with `--matte-invert`. The equivalent settings keys are `matte_source`, `matte_path`, `matte_channel` and `matte_invert`. A custom run has no automatic subject-selection step.

Use `configure --check --matte-source custom` to check a runtime intended for supplied mattes without requiring automatic-matting dependencies. Explicit matte flags on `generate` override the corresponding values from `--settings-json`.

Cancellation is cooperative and may take until the current operation completes. Resume reuses caches only when hashes and relevant settings agree. Logs and progress stay inside the run folder. The tool never modifies the AE-owned `shot.json` or its resource registry. AE conversion needs the configured VDA Python environment's image/numeric dependencies but does not load the models or the matting environment.

## Data and geometry

Each base package contains source RGB and alpha PNG sequences, raw and cleaned float32 EXR depth sequences, a provenance manifest, and resumable caches. Each separate AE export contains its requested normalized PNG streams, the person matte when requested, and a manifest retaining base provenance, ranges, timing and integrity records. All image sequences are zero-numbered. Source dimensions, exact rational FPS, decoded frame count and SHA-256 are recorded independently of the scene settings.

The animated surface uses native Geometry Nodes: a grid sampled from the depth image, multiplied along camera rays, with alpha-based background removal and source-color shading. It is saved as editable Blender data without Python frame handlers. Camera projection follows the owned reference camera and the scene's output aspect. Geometry evaluates with script auto-execution disabled.

Whole-shot foreground inverse-depth normalization avoids per-frame rescaling. Edge cleanup extends nearby confident depth into soft matte pixels; it does not reconstruct missing geometry. Fast motion, occlusion, loose hair and depth boundaries can still show artifacts. This is a camera-view depth reference, not a watertight body scan or an automatic rigging solution.

## Development

Run the tests under `tests/`, the worker tests under `worker/tests/`, and the Blender integration scripts with Blender 4.2 in background mode. `scripts/build_release.py` creates deterministic add-on and skill ZIPs containing only integration code and documentation. See `TEST_REPORT.md` for the actual release validation and remaining limits.
