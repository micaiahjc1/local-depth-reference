---
name: blender-depth-reference
description: Generate independent local video depth for manual Blender alignment and export After Effects depth maps for depth mattes, fog or grading. Use for a named shot or explicit footage; not automatic rig placement, motion capture, or animation retargeting.
---

# Blender and After Effects depth

Generate one reusable local depth package, then deliver a Blender reference, AE depth maps, or both as requested. Blender uses floating-point depth with the existing subject-boundary cleanup. AE uses a separate normalized export derived from that completed package. The artist aligns Blender rigs manually; do not run pose estimation, place or keyframe rigs, or adjust existing animation.

For an existing completed package, validate and reuse it instead of rerunning inference. Read [execution](references/execution.md) for generation and Blender import; read [AE export](references/ae-export.md) when delivering normalized maps. When the user supplies a matte, also read [custom mattes](references/custom-matte.md). The retained skill name `blender-depth-reference` supports existing invocations.

## Bind the shot and camera

Read [shot binding](references/shot-binding.md) before accessing a linked shot. Resolve the user's named shot or explicit footage against existing project links; announce the resolved project, shot and destination. Ask only when the target remains ambiguous. An active window, selected comp or similarly named directory does not establish the target.

Use that shot's physical `resources/depth map` directory, preserving an existing `Resources` casing. Let the workflow CLI reserve each version. Keep completed packages and the original `shot.json` identities unchanged. A main camera is needed only for Blender import; generation and AE export do not require one. For Blender import, read the intended scene's existing main camera without changing the scene's FPS, resolution, frame range, color management or rig animation.

## Generate and inspect

Use the installed workflow CLI and local worker. Keep inference outside Blender. Automatic matting remains the default. If the user supplies a matte, use the supported custom input with its recorded provenance; do not silently substitute another matte, prior depth or fitted scale. Raw RGB from an explicitly chosen clip is valid even if stored in an older test folder. Base generation retains raw depth, subject-boundary-cleaned depth, source RGB and the chosen matte; the subsequent export choice does not change those files.

In automatic mode, when the worker needs a subject choice, inspect its candidate preview and bind the intended person using that run's actual ID. Resolve ambiguous people with the user; do not assume object 1 identifies the desired person across clips. Custom mattes define the subject coverage and skip automatic tracking/refinement. They still use generated depth and the existing boundary cleanup. Monitor the recorded progress and cancellation state. A cancelled, failed or incomplete package is not importable.

Check first/last-frame correspondence, sequence completeness and sample raw/clean depth against the source and chosen matte. Include an occlusion or fast motion frame. Preserve supplied 16-bit matte precision and report any resize to working dimensions. Treat relative depth scale and edge extrapolation as manual aids, not measured anatomy or metric accuracy. Better coverage can improve boundaries; it does not correct internal depth or recover hidden surfaces.

## Import for manual alignment

Use the add-on's manifest import API with the bound scene and main camera. Verify the added collection, depth surface, reference camera and source timing. Test that the package's controls affect only its owned import; camera matching and alignment/parenting operate through the add-on. Keep frame offset separate from one-based image indexing.

Save a requested candidate copy or apply the short authorized import to the resolved live scene. Preserve the user's workspace and existing scene settings. Report the versioned package/Blender location, actual checks and any visible limitations. Do not describe a manual reference surface as a solved rig.

## Export for After Effects

Use `export-ae` on the validated base package. Choose whole scene, person, or both from the user's intended effect: whole-scene depth covers the plate for fog or grading; person depth accompanies the existing subject matte. When both are requested, export both in one job. This creates a new version under the same bound shot without rerunning AI or altering the Blender package.

Follow [AE export](references/ae-export.md) for data interpretation and validation. Report exact FPS, frame order, exported dimensions and the separate fixed range for each coverage. The export creates files only; editing an AE project is a separate requested delivery step. Neither normalized values nor the underlying relative depth are measured meters.
