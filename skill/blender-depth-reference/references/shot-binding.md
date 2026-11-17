# Shot identity and output ownership

This workflow follows the user's shared shot convention in `~/.codex/skills/_shared/shot-workflow.md`. The paused Shot tools panel is not required. Normal depth generation needs no managed BlenderAE processing job or new shot registration.

## Resolve once

Retain a small binding record with the explicit request, absolute shot directory/manifest, source provenance and fingerprint, exact frame mapping, and versioned delivery path. Include project/Blender file, scene and main-camera identity when Blender import is requested. For a linked shot also retain its verified project UUID, shot UUID, owner comp ID and relevant source/layer IDs. Do not manufacture those identities for an unlinked shot.

If the user names a shot, resolve it in the intended project and existing links. If the user gives explicit footage and a shot directory, use that explicit scope. With neither, request the target rather than picking the active comp. Multiple Blender instances require the intended process/file/scene to be identified before native access. A scene made for rendering can share a shot binding with the artist's working scene; matching UUIDs alone does not select the delivery scene.

## BlenderAE contract

The installed Blender add-on is `blenderae_import.py` under Blender's add-ons directory. Its scene binding is:

| Field | Meaning |
| --- | --- |
| `scene.blenderae_props.manifest_path` | Absolute `shot.json`, or a saved-blend-relative `//` path |
| `shot_id`, `project_id` | Existing manifest identities |
| `shot_revision`, `shot_label` | Last read revision and display label |
| `prep_camera`, `prepared_camera` | Explicit selected/prepared camera object pointers |
| `selected_export_id`, `utility_export_id` | Utility selection; not ownership of a new depth package |

Read these from the resolved scene, resolve `//` against that saved blend, and compare IDs with the actual manifest. If BlenderAE is disabled, the persisted `scene.get('blenderae_props')` IDPropertyGroup can contain the same binding keys; read the primitive manifest/identity fields without copying the entire group. The add-on's `read_shot_manifest(path, expected_shot_id, expected_project_id)` validates without rebinding. Do not call its link/change-shot/apply-settings operations merely to generate depth. A newer disk revision may contain changed source/timing; inspect the relevant fields instead of overwriting the older scene binding.

Use an explicitly selected camera where supplied. Otherwise a verified linked `prep_camera`/`prepared_camera` or the bound scene's camera is evidence; confirm that it belongs to the intended scene and is the actual main camera. Resolve conflicting camera pointers before import.

The manifest schema is `schema: "blenderae.shot"`, `schema_version: 1`, with `project_id`, `shot_id`, positive `revision`, `label`, `ae`, `image`, `timing`, `color`, `sources`, `exports`, and `paths`. `ae` contains `project_path`, `comp_id`, `comp_name`; `image` contains width, height, pixel aspect; timing includes frame rate/duration/count, comp duration and display/work-area offsets. Paths inside the manifest are relative to its directory unless explicitly absolute.

For a live AE source, read the installed ownership logic in `engine/BlenderAEShotCore.jsxinc` within the BlenderAE CEP extension. `BlenderAEShotCore.context(named_comp)` checks the comp's `BlenderAEShot1` tag, owner comp ID, persistent `BlenderAEProject1` project identity and matching manifest. Duplicate comp tags or reused numeric comp IDs in another AEP do not establish ownership. Resolve the named comp by native ID; do not use `app.project.activeItem` as a substitute. Inspect existing links without creating metadata or relinking.

**AE owns `shot.json`; Blender reads it.** Its current utility export roles are `proxy`, `roto`, and `still`. Do not insert a fabricated `depth` export, resource UUID, processing acceptance or BlenderAE receipt. Store this add-on's separate manifest within its own versioned depth package.

## Exact source and physical destination

Choose explicit footage or a verified existing shot plate. Do not silently use the first `sources` entry: it may be an adjustment layer, duplicate, disabled item, or untrimmed camera original. Preserve source-to-shot timing, crop/transform, color treatment, stretch and time remapping. A raw clip is not equivalent to a treated AE comp merely because names or durations resemble each other. When a needed treated plate is unavailable, prepare it through an explicitly bound export route or resolve the missing input; do not bake unsaved live edits by reopening a stale project.

Use `<actual shot directory>/<existing Resources casing>/depth map/vNNN/`. `paths.export_root` commonly points to `Resources/Utilities`; it is not the desired depth destination. Resolve existing case without creating parallel `Resources` and `resources` trees; ambiguity on case-sensitive storage needs resolution. The CLI owns exclusive version reservation, cache matching, progress and cancellation. Keep previous versions and another chat's partial runs intact.

For an AE export from a completed base package, reuse and validate that package's recorded binding and source mapping. The conversion reads its existing depth/matte assets and may proceed when the original video is offline; it does not need a live camera or AE comp. Reserve a separate AE version under the same shot through `export-ae`, retaining the base unchanged. The source path remains provenance, not permission to substitute a newly selected clip.

Revalidate binding and source mapping immediately before import. A target change does not authorize moving finished results to the newly active shot. Keep long inference and review outside the live Blender/AE session; deliver only the short import after completion. Do not change existing camera backgrounds, render settings, playhead, selection or unrelated objects to review the result.
