DEPTH MASK CONTROLS 1.0.0

Select a grayscale depth-map layer in an AE composition. Choose File > Scripts
> Run Script File and select Depth Mask Controls.jsx. No extra plugin needed.

Depth: 0-100, moves the transition midpoint from near to far.
Range: 0-100, widens the transition from a hard cutoff to a gradual blend.
Invert: chooses the opposite side of the transition.

Use the controlled layer as a Luma Track Matte for your effect layer or a
duplicate of your footage containing your effects. White reveals, black hides,
gray blends. Adjust the affected layer's opacity to control overall strength.
The controls change mask selection; they do not change scene geometry.

The standalone script starts at Depth 50, Range 100, Invert off (identity).
The Tony_02 whole-scene example starts at Depth 8, Range 10, Invert on.
These values refer to each export's fixed normalized range, not meters.
Whole-scene and person exports have different ranges.

Controls are keyframeable. Rename or duplicate the layer normally. Keep the
effect names Depth, Range, Invert and Depth Mask - Remap unchanged because the
expressions use them. Leave the remap enabled. Rerunning the script on its
unchanged stack keeps existing values and keyframes and adds no duplicates.
Other effects are preserved; the new stack is appended after them.

For person-only maps, apply these controls to the unpremultiplied person DEPTH,
then apply the separate person alpha as a Luma matte. Inversion must happen
before silhouette masking. This keeps the background transparent.

Data import: correct source FPS, first image as a PNG sequence, Preserve RGB
in color-managed projects. Match source timing and transforms. No extra
Blender frame-offset correction is needed in AE.

Use 16- or 32-bpc for smooth masks. An 8-bpc project quantizes the depth before
the effect, and very narrow ranges can reveal steps. This script preserves
project bit depth and color settings. It cannot recover missing depth detail.
Range zero uses a minimum width of one 16-bit code value to avoid division by
zero. Values entered outside 0-100 are clamped by the expressions.

TONY_02 PLAYGROUND

Open Depth_Mask_Playground.aep, or find Depth Mask Playground v001 in the
existing project after delivery. Open 03 PLAY - blend an effect, select
Depth Mask - adjust Depth and Range, and change its Effect Controls.
The example brightens a duplicate of the source through a Luma Track Matte.
Replace that brightness effect with your own effects; use the original
footage layer underneath for a smooth blend. The two MASK comps provide
separate whole-scene and person views.

The standalone playground is 32-bpc, uses the current 4K Tony_02.mp4 source
and the Rerun_01 v002 1280x720 maps at 300% scale, and contains146frames at
24000/1001fps. Importing it into another project uses that project's bit depth.
The data sequences remain external and must stay at their recorded locations.
