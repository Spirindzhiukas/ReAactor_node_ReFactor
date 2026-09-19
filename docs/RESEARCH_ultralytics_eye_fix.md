# Research: ultralytics bbox models + an eye-fixing stage

*Investigation only — no code written (owner instruction). Status: awaiting
design decisions, see §7.*

## 1. What the models in `models/ultralytics/bbox/` are

That folder layout is the **ADetailer / Impact-Subpack convention**
(`bbox/` = plain-box detectors, `../segm/` = instance-seg detailers). Every
`.pt` there is an **Ultralytics YOLO training checkpoint stored as a Python
pickle**. Identified examples from the owner's folder:

| File | What it is | Output | Useful here? |
|---|---|---|---|
| `face_yolov8n_v2.pt`, `face_yolov8s.pt` | Bingsu/adetailer face detectors (YOLOv8n/s fine-tunes, photo+anime faces) | face **bbox only** (no landmarks) | yes, as auxiliary detectors |
| `Eyes.pt` | civitai 150925 "Eyes Detection Adetailer" — YOLOv8-based eye detector | eye **bbox** | yes — exactly the eye-fix locator |
| `Eyeful_v1.pt`, `Eyeful_v2-Paired.pt` | civitai 178518 "Eyeful — robust eye detection", custom YOLOv8; v2 trained for **individual and paired** eyes; built for ADetailer/ComfyUI; robust on illustration/beady/side-facing eyes | eye bbox (paired + single classes) | yes — best-in-class eye locator for this job |
| `yolo11x-obb.pt` | Ultralytics **official pretrained OBB** on DOTAv1 (aerial: planes, ships, vehicles…) | rotated boxes, 18 aerial classes | **no** — wrong domain; also needs OBB-specific decode |
| "lots of other face feature models" | arbitrary community detailers (same pickle format, incl. NSFW/attribute detailers) | varies | list generically if integrated; never curate |

## 2. Are they usable in ReFactor *today*?

**No.** Two independent blockers:

1. **Pickle loading requires the `ultralytics` package.** Verified via HF's
   pickle scan of `eyeful-paired-v2.pt`: the checkpoint contains
   `ultralytics.nn.tasks.DetectionModel`, `ultralytics.utils.*`,
   `C2f`, `SPPF`, `TaskAlignedAssigner`, … — `torch.load` must import those
   classes to unpickle. Without `ultralytics` installed the file cannot be
   opened at all (safely). Some older community models additionally need
   `dill`. We removed `ultralytics` as a dependency in the alpha1 audit on
   purpose.
2. **They don't fit our vendored YOLO.** Our `yolov5face` stack only accepts
   checkpoints matching its own yaml (v5-face archs). YOLOv8/11 heads
   (anchor-free, decoupled) are a different architecture.

Note: the owner's own env almost certainly *has* `ultralytics` already —
their logs show `comfyui-impact-subpack` active, and its
UltralyticsDetectorProvider is what normally populates/uses that folder.

## 3. Integration paths

### A. Soft-dependency provider (recommended for their collection)
Lazy-import `ultralytics` exactly the way we isolate `onnxruntime`: if the
package is present (Impact Subpack guarantees it in this env), a
`ReFactor` detection-provider node wraps `YOLO(path)` and returns typed
detections; if absent, the node lists/models degrade with a clear
"install ultralytics to use" message. We never install or pin it, never
import it at module load. Matches ADetailer/Impact semantics exactly and
supports every model in that folder (bbox, segm, pose) for free.

### B. One-time ONNX conversion (fits our zero-dep philosophy, later)
`YOLO(...).export(format="onnx")` once per model, cached under
`models/ultralytics/onnx/`; runtime is pure ORT with our own v8/v11
detect-head decode (anchor-free: output `[1, 4+nc(+k*3), N]`, simple
xywh→xyxy + per-class scores + NMS — trivially done in numpy; segm adds
proto-mask matmul; OBB adds an angle channel + rotated NMS — skip).
Runtime-clean after conversion, but the conversion step still needs
`ultralytics` once, and decode maintenance across archs is real work.
Good v2 if we want zero-runtime-dep.

### C. Skip ultralytics entirely for eyes — we already have an eye locator
Our analyzer **already runs `2d106det.onnx`** (when present in the
buffalo_l/antelopev2 dir — it is, owner confirmed) and stores full
106-point landmarks on every `Face`. In the insightface 106 scheme the eye
contours are **6 points each**: right eye = indices `66,67,69,70,71,73`,
left eye = `75,76,78,79,80,82` (verified mapping). Six contour points per
eye are enough for tight rotated/axis-aligned crops, template validation,
and closed-eye detection (contour height ≈ 0 → skip). Zero new
dependencies, zero new models, works today on photo faces; YOLO eye
models (Eyeful) are more robust on anime/stylized faces — hence the
pluggable design below.

**Recommendation: build the eye-fix stage with a pluggable locator — C as
the built-in default, A as the optional provider for the owner's
collection, B as a later optimization.**

## 4. Proposed eye-fix stage (post-face-pass, as the owner wants)

Converges to the same pipeline regardless of locator:

1. In `restore_face`, after the per-face restore inference, for each
   **aligned** face (we already have `affine_matrices` per face):
   - locate eyes:
     - built-in: transform the face's `landmark_2d_106` (original-image
       coords) through the face's affine matrix → exact aligned-space eye
       contours; OR
     - provider: run `Eyes.pt`/`Eyeful` on the aligned face crop;
   - crop each eye: contour bbox + margin (fraction of inter-eye distance,
     default ~50%), minimum-size guard (~48 px; skip smaller);
   - restore the eye crop with the selected FACE_RESTORE_MODEL — same
     engine, same upRes decision logic we just built (GFPGAN is strong on
     eye crops); optionally a *dedicated* eye restore model;
   - blend back into the aligned face with a feathered elliptical mask
     (our `masking/ops` + soft-mask machinery already do this);
2. continue with the normal face paste-back — eyes are already fixed when
   the face lands in the image.

Skips by design: sunglasses/occlusion (Eyeful ignores them explicitly),
closed eyes (contour-height threshold), too-small eyes.

Controls (proposal): `enable_eye_fix`, `eye_restore_model` (default =
face's model), `eye_margin`, `eye_visibility`, `min_eye_px`; per-eye count
logging in our usual style.

## 5. What the face bbox models could additionally give us

ADetailer-style **second-chance detection** (bbox → crop → restore → paste
back, no landmarks needed) as a fallback for faces our 5-kp detectors miss
(hard profiles, occlusion). Same optional provider node powers it. Future
scope, not part of eye-fix v1.

## 6. Risks / notes

- `yolo11x-obb.pt`: excluded (DOTA aerial classes, OBB decode). Separate
  feature if ever wanted.
- HF flags many of these `.pt` as "pickle unsafe" (they are pickles); in
  the owner's env `comfyui-unsafe-torch` / Impact `subcore` already wrap
  `torch.load` for exactly this. Our own code paths would go through
  ultralytics' own loader (path A) or our ONNX cache (path B), never a raw
  unpickle of our own.
- ultralytics version churn is real (one community model needs
  `ultralytics>=8.3.75`, older ones need `dill`). Path A never installs or
  pins; failures degrade to clear errors. Path B sidesteps churn entirely
  at runtime.

## 7. Decisions needed from the owner

1. Locator strategy order (built-in 106 landmarks first, YOLO models as
   opt-in override? or YOLO-first when available?).
2. Eye restore model: dedicated widget (choose a different model for eyes,
   e.g. GFPGAN for eyes + CodeFormer for face) or always reuse the face's?
3. Provider node shape (if A): one "Ultralytics detection loader" → typed
   `DETECTION` socket on the swap/restore nodes, or an eye-fix-specific
   socket?
4. Is the ONNX-conversion cache (path B) wanted at all, given ultralytics
   is already installed in this env?
