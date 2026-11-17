# Supplied subject mattes

Use this path when the user explicitly supplies a matte for the named footage. Automatic SAM3 and MatAnyone2 generation remains available through `matte_source: "automatic"`, the default. Custom mode replaces tracking and matte refinement; Video Depth Anything still generates depth, and the existing Blender boundary cleanup uses the supplied coverage.

## Bind the input

Accept a numbered sequence of 8- or 16-bit PNG, TIF or TIFF images. Choose the first image corresponding to the first decoded source-video frame. File numbering need not start at zero, but each following frame must be present in order for the full clip. The worker consumes exactly the source frame count from that starting image; files before or after that window are ignored. Image sequences have no intrinsic FPS; the worker maps one image to each source frame without skipping, duplicating or retiming. Verify first, last and a motion/occlusion frame against the source rather than inferring alignment from filenames alone.

All selected frames must have consistent dimensions, channel count and bit depth, with the source's aspect ratio, crop and orientation. The aspect check allows one pixel of height rounding; exact working dimensions are also valid when source decoding rounded to even pixels. The worker resizes valid coverage to the depth package's working dimensions using area reduction or linear enlargement, and records the conversion. A matching aspect ratio cannot detect a shifted crop or wrong editorial trim. If the supplied matte represents different footage or timing, resolve that mismatch before generation. This release does not accept video or EXR mattes; use a lossless PNG/TIFF sequence exported from the user's roto instead.

Choose `luma` for coverage encoded in grayscale/RGB values, or `alpha` for the transparency channel of an RGBA sequence. Luma uses Rec.709 weights on the stored RGB values with no gamma or profile transform, and ignores any alpha channel. Alpha mode requires an actual alpha channel; do not silently substitute RGB when it is missing. White/opaque is foreground, black/transparent is background. Use `matte_invert` only when the input uses the opposite convention. Interpret matte values as data, with no creative grade or color transform.

The clip needs some confident foreground at alpha at least 250/255 that survives the chosen cleanup radius and inset. A wholly empty or entirely translucent matte cannot establish the normalization core and is rejected. Keep soft edges, but do not reduce the whole subject's opacity as an effect-strength adjustment before generation.

## Run and retain provenance

Use the supported CLI with verified absolute paths:

```text
python "<tool_root>/local_depth_reference/cli.py" generate --source "<video>" --shot "<shotdir>" --matte-source custom --matte-path "<first numbered matte.png>" --matte-channel luma
```

Add `--matte-invert` if needed. The equivalent settings JSON fields are:

```json
{
  "matte_source": "custom",
  "matte_path": "<absolute first numbered image>",
  "matte_channel": "alpha",
  "matte_invert": false
}
```

Custom mode does not require the SAM3/MatAnyone2 environment or checkpoints, and has no subject-candidate selection. Use `configure --check --matte-source custom` when checking its runtime. Keep the returned version for status, cancellation and resume; do not pass subject IDs or point prompts to a custom resume. The worker fingerprints the selected sequence and keys its cache to the matte input and conversion settings. Do not edit input frames during a run or relabel an old cache to bypass changed-file checks. Completed base versions are immutable; a new matte for an existing shot requires a new base version.

Keep the source files unchanged. Retain the manifest's `processing.matte_source`, `processing.alpha_bit_depth` and `processing.matte_inference` records, including sequence fingerprints, channel choice, inversion, dimensions and resampling. User-provided does not imply hand-refined: accurately identify generated, artist-authored or externally supplied coverage when that provenance is known.

## Check both deliverables

Preserve supplied 16-bit coverage through base alpha and AE person export. Original 8-bit coverage remains 8-bit in the base and is scaled losslessly to 16-bit storage for AE. Check soft edges as well as fully opaque/transparent pixels; a conversion that leaves only binary values may have lost the user's feathering.

In Blender, confirm that the supplied matte drives the surface cutoff/material and the existing clean-depth boundary extension. Import timing and controls remain independent of the rest of the scene. In AE, Person uses the same coverage after depth remapping; Whole Scene continues to use unmasked raw depth. Their fixed normalized ranges differ and are not interchangeable distances.

Better coverage can improve silhouettes and boundary cleanup. It does not correct an inaccurate depth prediction inside the person, recover missing surfaces, or increase the model's spatial detail. Report observed improvements and remaining limitations separately.
