# Release validation — Local Depth Reference 0.3.0

Adds **Generate Automatically / Use My Matte** to the Blender sidebar and Codex CLI/skill. Supplied numbered PNG/TIFF sequences can use grayscale/RGB or embedded alpha, with optional inversion. Custom mode bypasses SAM3 and MatAnyone2; VDA remains the depth generator. Both Blender boundary cleanup and AE person coverage consume the chosen matte. Original8-bit automatic output is unchanged; supplied16-bit coverage remains16-bit through base and AE files.

Validated on Blender4.2.7 and After Effects25.2.2,16November2026:

- **47 runtime/CLI tests** pass, including automatic defaults, custom-only model configuration, argument overrides, input rejection before version reservation, and resume behavior.
- **45 worker tests** pass, including19 new supplied-matte cases. The automatic PNGalpha and raw/clean EXRs match packaged0.2.1 byte-for-byte. Custom tests cover both channels, inversion, resizing,16-bit precision, missing frames, aspect/type/channel mismatches, no confident core, input hash changes, cancellation/resume and export after the original supplied files become unavailable.
- **183 independent cleanup/export checks** pass across automatic8-bit, customRGBA16 and inverted grayscaleTIFF16 fixtures. Every AE coverage choice (whole/person/both) is checked. Evidence: `test-output/custom-matte-qa/fixtures/fixture_report.json`.
- **75 native Blender checks and18 clean-reopen checks** pass: scene/camera/rig/selection preservation, depth sampling, precision-sensitive alpha thresholds, empty mattes, import offsets, raw/clean controls, UI/operator routing and Cycles renders. Reopen works with the add-on disabled. All four Blender user directories, including extensions, are isolated. Evidence: `test-output/custom-matte-qa/blender/`.
- **24 upgrade,42 lifecycle,51 ZIP installation/operator checks**, plus48 existing AE compatibility checks, pass. Final source qualification is under `test-output/custom-matte-qa/final-regressions/` and `final-package/`. The final archive differs from that qualification only in its README; all executable files were compared byte-for-byte. The final archive was also installed and reloaded in the bound live Blender session.
- **Actual model integration** passed on12 frames from the current4K Tony_02 source, reduced to320x180 while retaining24000/1001fps. Automatic SAM3+MatAnyone2+VDA and suppliedRGBA16+VDA-only both produced validated Blender packages and AE exports. All12 custom alpha frames are exact in the base and AE outputs; automatic AE alpha equals8-bit values multiplied by257. The supplied test sequence was derived from prior generated coverage with deliberate precision values, not represented as artist-refined roto. Evidence: `test-output/custom-matte-real/cfr-mp4/integration_report.json`.
- **156 native AE frame readbacks** pass across synthetic and actual-model packages, covering whole scene, person depth, independent alpha, and composited person coverage. All source frames are accounted for, including transparent empty frames. The numeric comparison includes AE's verified integer-image conversion to32768-level channels in32-bpc projects. Evidence: `test-output/custom-matte-qa/ae-final/native_validation.json`.
- The installed skill passes `quick_validate.py`; its six files match the packaged source. Previous files are retained under `test-output/skill-update-030/previous`.
- The live add-on was upgraded in the user's existing `Tony_02_v003.blend` session. Version0.3.0, new matte controls, automatic default and status monitor were read back; scene settings, camera, playhead and selection remained unchanged. The project was not saved automatically. Evidence: `test-output/live-update-030/live_receipt.json`.

Release archives and exact hashes are in `dist/release.json`. No model inference or test render was run in the production Blender or AE project. Test harness corrections included using an MP4 timebase for the shortCFR fixture and a fresh AE output module after template discovery; the successful final runs are identified above.

Supported custom inputs are8/16-bit numbered PNG/TIF/TIFF sequences. The chosen first image maps to source frame0; exactly the source frame count is consumed. Image sequences do not carry authoritative FPS. Dimensions and contiguous files can be validated, but a wrong editorial trim/crop requires visual review. Coverage must contain some confident opaque foreground for the existing depth cleanup. Supplied coverage improves isolation and boundary guidance, not the depth model's interior accuracy. Replacing the matte requires a new base generation; completed versions stay immutable.

# Earlier 0.2.1 validation history

This maintenance release fixes in-session addon upgrades retaining cached UI/operator modules, and stale progress when the original modal monitor no longer exists. The completed user job was verified on disk before recovering its UI; no inference was restarted. The new entry point reloads child modules, and a persistent status timer reconnects to recorded jobs after upgrades or file loads. Disabling the addon detaches monitoring; explicit Cancel/ESC still cancels the worker.

- The native upgrade test installs0.1.0 and then0.2.1 in the same isolated Blender4.2.7 process. AE controls appear, camera and generation settings survive, and a stale58% fixture automatically resolves to its completed manifest after the registered timer runs. Evidence: `test-output/upgrade-021-final/upgrade_report.json`.
- **42 progress lifecycle checks passed**, including active-worker reconnection, completed/failed/subject-selection states, transient unreadable records, disable/re-enable without cancellation, detached modal callbacks, explicit cancellation and actual saved-file reopen recovery. Evidence: `test-output/status-021/status_lifecycle_report.json`.
- **48 existing AE compatibility checks and51 ZIP/operator checks passed.** The final package was validated against the user's completed `Rerun_01` base package. Registration also handles Blender's restricted addon-enable context by deferring its scene scan. Evidence: `test-output/status-021/ae-backcompat/blender_ae_exports_report.json` and `test-output/package-021-final/package_smoke_report.json`.
- The user's Blender instance20708 was repaired without saving over the project or altering scene timing/camera/objects. It now runs0.2.1 with registered AE controls and automatic progress monitoring. Its base v001 and AE v002 both show complete; existing depth data was reused unchanged.

The upgrade test exposed incomplete environment isolation in the older test runner: Blender's extension-wheel housekeeping reached the shared extension dependency directory. Future tests now set and verify `BLENDER_USER_EXTENSIONS` as well as scripts/config/datafiles. The affected enabled extension dependency, BlenderAe's bundled psutil6.1.0, was restored with missing-file-only writes, all34 wheel members matched exactly, and import was verified in Blender's Python with its application DLL paths. Disabled extension binaries and live preferences were not changed. Repair evidence: `test-output/live-update-021/extension_dependency_restore.json`.

The installed addon was updated and reloaded in the targeted Blender instance. A reusable ZIP is `dist/local-depth-reference-0.2.1-blender-4.2.zip`; current hashes are in `dist/release.json`. Export algorithms, maps and the installed Codex skill are unchanged by this maintenance release.

# Earlier 0.2.0 validation history

Tested on Windows with Blender 4.2.7, 16 November 2026. This release adds AE export from completed depth packages. It does not change Blender geometry, depth inference, matting or boundary cleanup.

## Current release checks

- **39 path/runtime/CLI tests passed:** 22 existing tests plus 17 AE export tests. Coverage includes all three coverage choices, same-shot version reservation, no model/video dependency for export, package integrity, cancellation/resume, separate completion routing, malformed bindings and immutable completed output.
- **26 numerical/worker tests passed:** 16 existing tests plus 10 AE export tests using real float EXR and 16-bit PNG fixtures. Tests cover exact fixed normalization, preserved cleaned person values, separate unpremultiplied alpha, small distant subjects, constant depth, bounded-memory exact percentiles, missing/corrupt/nonfinite images, cancellation and scratch cleanup, source changes during processing, binding/hash checks and completed-version protection. No AI models were rerun for this release.
- **48 isolated Blender checks passed:** new properties and panel actions, coverage dispatch, separate AE versus Blender output paths, cancellation/error handling, old generation/import compatibility, registration/unregistration, and preservation of artist timing, camera, selection, scene objects and animation. Worker dispatch is mocked in this UI test; the external exporter is exercised by the separate real-clip test. Evidence: `test-output/ae-export-020/blender_ae_exports_report.json`.
- **50 final ZIP installation/operator checks passed:** the final archive was installed into an isolated Blender user directory. Existing imports, camera matching/parenting, offsets, restore, bundled-worker discovery and standard-library CLI use passed. Evidence: `test-output/package-020-final/package_smoke_report.json`.
- **Real Tony02 export from the extracted final ZIP passed:** 146 frames at 24000/1001 fps, 1280x720 depth and matte output, both AE coverage types, new `Resources/depth map/v004`. The test used a minimal config containing only the image-processing Python path, with no model repositories or checkpoints. The source package and shot.json hashes stayed unchanged. Evidence: `test-output/ae-release-020-final/integration_report.json`.
- **All-frame comparison to the previous native AE test passed:** final whole-scene and boundary-cleaned person maps differ by at most one code out of65535 from the previously AE-tested normalized maps; alpha is bitwise identical. The one-code difference comes from float64 normalization arithmetic. Evidence: `test-output/ae-release-020-final/pixel_comparison.json`. AE was not launched again for this code release; native AE rendering and numeric transport were tested in the preceding comparison.
- **Blender processing preservation:** `worker/process.py`, `worker/common.py`, `worker/stages.py` and `local_depth_reference/geometry.py` are byte-identical to their0.1.0 ZIP copies. The stronger existing boundary treatment remains in use. AE export introduces no gentle spatial filtering.
- **Skill update passed:** repository and installed `blender-depth-reference` skill validate; references and CLI flags resolve. The installed skill files match the packaged source, with previous files retained under `test-output/skill-update-020/previous`.

The full-clip test exposed a transient Windows sharing violation while polling atomically replaced progress.json. A bounded reader retry fixed it, a regression test covers the failure, and the final packaged full-clip run passed. The initial export itself completed in v003; v004 is the final release test. Completed source and output versions were not overwritten.

The release ZIP is `dist/local-depth-reference-0.2.0-blender-4.2.zip`; the matching skill ZIP is `dist/blender-depth-reference-skill-0.2.0.zip`. Exact final archive hashes are recorded in `dist/release.json`.

## Current delivery limits

AE maps are normalized relative-depth data, with a fixed separate range for each coverage. They retain the base's working resolution, not new source-resolution detail. Person data requires its separate matte; whole-scene export uses raw background depth. Original 8-bit matte values are encoded in16-bit PNGs without adding precision. Export does not open AE, change native project settings, apply Blender scene offsets, or certify metric accuracy. The selected person remains the one in the completed base package.

The addon was packaged and tested in isolated Blender instances; the artist's live addon was not hot-reloaded. Install the new ZIP in Blender to load the new controls. The Codex skill has been updated in place.

# Earlier 0.1.0 validation history

Tested on Windows with Blender 4.2.7 and the configured local NVIDIA RTX 5090 model environments, 16 November 2026.

## Processing and shot ownership

- **22 Python path/runtime tests pass.** Coverage includes existing `Resources` casing, no writes during resolution, explicit legacy folders, matching BlenderAE scene UUIDs, malformed/empty manifests, concurrent exclusive version reservation, changed shot identity, missing middle frames, extra frames, SHA-256 corruption detection, invalid nested metadata, cancellation, subject selection/resume, immutable completed versions, stale launcher files, duplicate launches, immediate resume status, recovery after committed publication and CLI exit codes.
- **16 worker tests pass.** Coverage includes exact rational FPS, arbitrary frame count/resolution, decode cancellation, shot/path containment, manifest identity changes, concurrent workers, transient Windows file-lock retry, cache corruption/settings changes, multiple-subject selection, cleanup strength, empty subject frames, whole-shot normalization, and explicit rejection of rotated or non-square-pixel input.
- **Actual Tony02 footage processed independently:** 146 frames at 24000/1001 FPS, source 2048×1152, working output 1280×720. All 146 source/alpha pairs and 292 float32 EXRs decoded and validated. The foreground Tony was explicitly selected after inspection of three candidates. No Beeble depth, matte, camera calibration or pose data was used.
- Output is in the existing shot's **`Resources/depth map/v001`**. The shot's `shot.json` and original Blender projects were not modified.
- The raw depth array is bitwise identical to the independent control run. Cleanup leaves the confident interior unchanged; no invalid/zero depth floor events or missing foreground-core frames occurred in this shot. All new matte frames were inspected in contact sheets; the intended performer remains selected and background people are excluded.
- **All-model lifecycle fixture:** 12 frames at 25 FPS, 320×180. Point-prompted SAM3 selection, cancellation during MatAnyone2, same-version resume with validated decode/tracking cache reuse, final PNG/EXR validation, and completed-version immutability passed. Machine-readable evidence is in `worker/tests/model_integration_result.json`.

## Blender and package checks

- **50 final ZIP installation/operator checks pass** in an isolated Blender 4.2.7 user directory. Installation, disabling/re-enabling, bundled external worker resolution, CLI use with Blender's standard-library Python, two real Tony imports, independent offsets, camera matching/parenting, restore, foreign-collection rejection, mocked generation/cancel/resume/status operators, and artist camera/rig/timing/selection preservation all passed. No live user preferences were changed. Evidence: `test-output/package-final/package_smoke_report.json` and `blender.log`.
- **Nine numerical geometry/timing cases pass**, plus portrait aspect fit, unique Geometry Nodes groups, four shared image sequences, nonunit camera scale compensation and restore/ownership checks. Maximum reported projection error is below 0.0003 normalized units; depth error is below 0.000001 scene units for the samples. Evidence: `tests/test-output/agent_geometry_report.json`.
- **Eight Cycles rendered timing cases pass.** Synthetic red/green/blue source frames, distinct alpha masks and known depth values verify actual file selection at Start Frame 1001, positive/negative offsets, endpoint holds and repeated frames. This tests visible RGB, matte and geometry together, rather than just the displayed offset number. Evidence: `tests/test-output/render_timing_report.json`.
- **Native reopen passes with the add-on absent and auto-execution disabled.** Seven geometry timing cases plus a Cycles color render remain correct. Evidence: `tests/test-output/synthetic_fixture/native_reopen_report.json`.
- A new **`Local_Depth_Reference_Manual_Demo.blend`** is saved in the Tony02 shot's `Blender` folder. It contains source-color and oblique clay inspection scenes using Cycles, and no armatures. Both scenes were imported through the documented add-on operator. Ten CPU render evaluations completed, including repeated frames. The source view aligns cleanly; the oblique clay view visibly shows the depth-boundary stretching described below.
- The actual Tony demo also reopened in factory Blender with the add-on absent and auto-execution disabled. Both scenes evaluated frames 1, 49, 146 and repeated 49 correctly. All four image sequence paths are shot-relative (`//../Resources/depth map/v001/...`). Evidence: `tests/test-output/tony_demo/demo_native_reopen_report.json`.

## Codex skill

- `blender-depth-reference` is installed under the user's Codex skills directory and passes the skill-creator validator.
- **Ten independent forward checks pass:** named Hero scene selection despite another active scene, duplicate shot labels, relative scene links, UUID mismatches, ambiguous Blender instances, subject-selection status, incomplete-package rejection, configured runtime and read-only validation of the completed Tony package. Fixture rigs and scene metadata were preserved. This scenario uses fixture scene metadata; the actual Blender operator handoff is separately exercised by the package test and two-scene Tony demo.
- Evidence: `worker/tests/forward_skill_result.json`. The skill calls the same CLI and worker as the add-on; it does not duplicate the model pipeline.

Repeatable test scripts live under `tests/` and `worker/tests/`; their generated outputs are excluded from the installable ZIP. The release archive contains no models, footage, Beeble assets or automatic rig-placement code.

## Remaining limits

- Model output is a relative camera-view depth reference. It is not measured metric anatomy, watertight geometry or a solved rig. Boundary extrapolation can still show stretched surfaces around fast motion and occlusion.
- V1 accepts constant-frame-rate square-pixel videos with orientation baked into the pixels. Rotation metadata, non-square pixels and variable cadence are rejected explicitly.
- One source frame maps to one scene frame. Different source and scene FPS are preserved and reported; the tool does not silently retime footage.
- The add-on ZIP contains the external worker, not Python/model weights. This workstation's existing local runtimes are configured separately. Other workstations need compatible external runtimes and checkpoints.
- Automated checks validate mechanics, data integrity and scene preservation. Artistic alignment and depth quality remain subject to the user's review.
