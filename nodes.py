import glob
import logging
import os
import sys
from typing import List

import numpy as np
import torch
import cv2
from PIL import Image

import comfy.model_management as model_management
import comfy.utils
import folder_paths

from .rfactor import model_paths
from .rfactor.engine.face_objects import Face
from .rfactor.faceboost.archs.registry import ARCH_REGISTRY
from .rfactor.faceboost.archs import model_loading
from .rfactor.faceboost import restorer as _faceboost_restorer
from .rfactor.faceboost.facelib.utils.face_restoration_helper import FaceRestoreHelper
from .rfactor.log import logger, set_console_level
from .rfactor.masking import ReFactorMaskBuilder
from .rfactor.ort_utils import create_session, resolve_providers
from .rfactor.scripting import state, ProcessingImg2Img
from .rfactor.swapper import (
    unload_all_models,
    analyze_faces,
    half_det_size,
    get_current_faces_model,
)
from .rfactor.faceswap_script import FaceSwapScript
from .rfactor.loaders import (
    ReFactorFaceSwapModelLoader,
    ReFactorFaceRestoreModelLoader,
    ReFactorFaceDetectionModelLoader,
    detection_model_name,
)
from .rfactor.dlssnr import ReFactorDLSS5Enhancer
from .rfactor.torch_utils import normalize_ as normalize, stat_mode
from .rfactor.utils import (
    batch_tensor_to_pil,
    batched_pil_to_tensor,
    tensor_to_pil,
    img2tensor,
    tensor2img,
    save_face_model,
    load_face_model,
    prepare_cropped_face,
    normalize_cropped_face,
    rgba2rgb_tensor,
    progress_bar,
    progress_bar_reset,
)
# Model folders are registered centrally in rfactor.model_paths (called from __init__.py).
models_dir = folder_paths.models_dir
REACTOR_MODELS_PATH = model_paths.REACTOR_MODELS_PATH
FACE_MODELS_PATH = model_paths.FACE_MODELS_PATH
dir_facerestore_models = model_paths.facerestore_models_path

BLENDED_FACE_MODEL = None
FACE_SIZE: int = 512
FACE_HELPER = None

def get_facemodels():
    models = []
    for d in model_paths.face_models_dirs():
        models.extend(glob.glob(os.path.join(d, "*")))
    return [x for x in models if x.endswith(".safetensors")]

def get_model_names(get_models):
    models = get_models()
    names = []
    for x in models:
        names.append(os.path.basename(x))
    names.sort(key=str.lower)
    names.insert(0, "none")
    return names


class ReFactorFaceSwap:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "enabled": ("BOOLEAN", {"default": True, "label_off": "OFF", "label_on": "ON"}),
                "original_image": ("IMAGE",),
                "FaceSwap_model": ("FACE_SWAP_MODEL",),
                "face_restore_visibility": ("FLOAT", {"default": 1, "min": 0.1, "max": 1, "step": 0.05}),
                "codeformer_fidelity": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1, "step": 0.05,
                                                  "tooltip": "CodeFormer fidelity weight (0 = better quality, 1 = better identity)"}),
                "detect_gender_input": (["no","female","male"], {"default": "no"}),
                "detect_gender_source": (["no","female","male"], {"default": "no"}),
                "input_faces_index": ("STRING", {"default": "0"}),
                "source_faces_index": ("STRING", {"default": "0"}),
                "console_log_level": ([0, 1, 2], {"default": 1}),
            },
            "optional": {
                "target_face_image": ("IMAGE",),
                "target_face_model": ("FACE_MODEL",),
                "FaceRestore_model": ("FACE_RESTORE_MODEL",),
                "FaceDetection_model": ("FACE_DETECT_MODEL",),
                "face_boost": ("FACE_BOOST",),
            },
            "hidden": {"faces_order": "FACES_ORDER"},
        }

    RETURN_TYPES = ("IMAGE","FACE_MODEL","IMAGE")
    RETURN_NAMES = ("SWAPPED_IMAGE","FACE_MODEL","ORIGINAL_IMAGE")
    FUNCTION = "execute"
    CATEGORY = "ReFactor"

    def __init__(self):
        # self.face_helper = None
        self.faces_order = ["large-small", "large-small"]
        # self.face_size = FACE_SIZE
        self.face_boost_enabled = False
        self.restore = True
        self.boost_model = None
        self.interpolation = "Bicubic"
        self.boost_model_visibility = 1
        self.boost_cf_weight = 0.5
        self.last_swapped_bboxes = None
        # self.last_swapped_indices = None
        self.restore_swapped_only = True

    def restore_face(
        self,
        input_image,
        face_restore_model,
        face_restore_visibility,
        codeformer_fidelity,
        face_detection_model="retinaface_resnet50",
    ):
        """face_restore_model: FACE_RESTORE_MODEL dict {"name","path"} or None."""

        # from datetime import datetime
        # def _current_time():
        #     current_datetime = datetime.now()
        #     return current_datetime.strftime("%M:%S")
        
        # локальная функция IoU
        def _iou(b1, b2):
            xA = max(b1[0], b2[0])
            yA = max(b1[1], b2[1])
            xB = min(b1[2], b2[2])
            yB = min(b1[3], b2[3])
            interW = max(0.0, xB - xA)
            interH = max(0.0, yB - yA)
            interArea = interW * interH
            if interArea == 0:
                return 0.0
            boxAArea = max(1.0, (b1[2]-b1[0]) * (b1[3]-b1[1]))
            boxBArea = max(1.0, (b2[2]-b2[0]) * (b2[3]-b2[1]))
            return interArea / float(boxAArea + boxBArea - interArea)
        
        result = input_image

        if face_restore_model is not None and not model_management.processing_interrupted():

            global FACE_SIZE, FACE_HELPER

            self.face_helper = FACE_HELPER

            restore_model_name = face_restore_model.get("name") if isinstance(face_restore_model, dict) else face_restore_model

            faceSize = 512
            if "1024" in restore_model_name.lower():
                faceSize = 1024
            elif "2048" in restore_model_name.lower():
                faceSize = 2048

            logger.status(f"Restoring with {restore_model_name} | Face Size is set to {faceSize}")

            model_path = face_restore_model.get("path") if isinstance(face_restore_model, dict) else None
            if not model_path or not os.path.exists(model_path):
                model_path = _faceboost_restorer.ensure_facerestore_model(restore_model_name)
            if model_path is None:
                logger.error(f"Face restoration model '{restore_model_name}' could not be found or downloaded.")
                return input_image

            device = model_management.get_torch_device()

            if "codeformer" in restore_model_name.lower():

                codeformer_net = ARCH_REGISTRY.get("CodeFormer")(
                    dim_embd=512,
                    codebook_size=1024,
                    n_head=8,
                    n_layers=9,
                    connect_list=["32", "64", "128", "256"],
                ).to(device)
                checkpoint = torch.load(model_path, weights_only=True, map_location="cpu")["params_ema"]
                codeformer_net.load_state_dict(checkpoint)
                facerestore_model = codeformer_net.eval()

            elif ".onnx" in restore_model_name:

                ort_session = create_session(model_path, providers=resolve_providers())
                ort_session_inputs = {}
                facerestore_model = ort_session

            else:

                sd = comfy.utils.load_torch_file(model_path, safe_load=True)
                facerestore_model = model_loading.load_state_dict(sd).eval()
                facerestore_model.to(device)

            if faceSize != FACE_SIZE or self.face_helper is None:
                self.face_helper = FaceRestoreHelper(1, face_size=faceSize, crop_ratio=(1, 1), det_model=face_detection_model, save_ext='png', use_parse=True, device=device)
                FACE_SIZE = faceSize
                FACE_HELPER = self.face_helper

            # Copying Tensor to CPU (if it isn't) to convert torch.Tensor to np.ndarray
            image_np = 255. * result.cpu().numpy()

            total_images = image_np.shape[0]

            out_images = []
            
            pbar = progress_bar(total_images)

            for i in range(total_images):

                # if total_images > 1:
                #     logger.status(f"Restoring {i}")

                cur_image_np = image_np[i,:, :, ::-1]

                original_resolution = cur_image_np.shape[0:2]

                if facerestore_model is None or self.face_helper is None:
                    return result

                self.face_helper.clean_all()
                self.face_helper.read_image(cur_image_np)
                self.face_helper.get_face_landmarks_5(only_center_face=False, resize=640, eye_dist_threshold=5)
                self.face_helper.align_warp_face()
                
                # restored_face = None
                restored_faces = []
                
                # берем сохранённые bbox из swap (или None)
                swapped_bboxes = getattr(self, "last_swapped_bboxes", None)
                # флаги, чтобы одно сохранённое bbox не совпало с несколькими лицами
                used_swapped = [False] * len(swapped_bboxes) if swapped_bboxes else None

                IOU_THRESHOLD = 0.5

                for idx, cropped_face in enumerate(self.face_helper.cropped_faces):

                    # определяем bbox текущего лица, который дал детектор внутри FaceRestoreHelper
                    current_bbox = None
                    if hasattr(self.face_helper, 'det_faces') and len(self.face_helper.det_faces) > idx:
                        det = self.face_helper.det_faces[idx]
                        # det м.б. [x1,y1,x2,y2,score]
                        current_bbox = (float(det[0]), float(det[1]), float(det[2]), float(det[3]))

                    # логика: если у нас есть сохранённые bbox — ресторим только если iou с одним из них > порог
                    do_restore = True
                    # if self.last_swapped_indices is not None:
                    #     do_restore = idx in self.last_swapped_indices
                    if swapped_bboxes and self.restore_swapped_only:
                        do_restore = False
                        if current_bbox is not None:
                            for s_idx, sbox in enumerate(swapped_bboxes):
                                if used_swapped is not None and used_swapped[s_idx]:
                                    continue
                                if _iou(current_bbox, sbox) >= IOU_THRESHOLD:
                                    cx1 = (current_bbox[0] + current_bbox[2]) / 2
                                    cy1 = (current_bbox[1] + current_bbox[3]) / 2
                                    cx2 = (sbox[0] + sbox[2]) / 2
                                    cy2 = (sbox[1] + sbox[3]) / 2
                                    if abs(cx1 - cx2) < (current_bbox[2] - current_bbox[0]) * 0.25:
                                        if abs(cy1 - cy2) < (current_bbox[3] - current_bbox[1]) * 0.25:
                                            do_restore = True
                                    # do_restore = True
                                    if used_swapped is not None:
                                        used_swapped[s_idx] = True
                                    break
                    
                    if do_restore:
                    
                        # if ".pth" in face_restore_model:
                        cropped_face_t = img2tensor(cropped_face / 255., bgr2rgb=True, float32=True)
                        normalize(cropped_face_t, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5), inplace=True)
                        cropped_face_t = cropped_face_t.unsqueeze(0).to(device)

                        try:

                            with torch.no_grad():

                                if ".onnx" in face_restore_model: # ONNX models

                                    for ort_session_input in ort_session.get_inputs():
                                        if ort_session_input.name == "input":
                                            cropped_face_prep = prepare_cropped_face(cropped_face)
                                            ort_session_inputs[ort_session_input.name] = cropped_face_prep
                                        if ort_session_input.name == "weight":
                                            weight = np.array([ 1 ], dtype = np.double)
                                            ort_session_inputs[ort_session_input.name] = weight

                                    output = ort_session.run(None, ort_session_inputs)[0][0]
                                    restored_face = normalize_cropped_face(output)

                                else: # PTH models

                                    output = facerestore_model(cropped_face_t, w=codeformer_fidelity)[0] if "codeformer" in restore_model_name.lower() else facerestore_model(cropped_face_t)[0]
                                    restored_face = tensor2img(output, rgb2bgr=True, min_max=(-1, 1))

                            del output
                            torch.cuda.empty_cache()

                        except Exception as error:

                            print(f"\tFailed inference: {error}", file=sys.stderr)
                            # restored_face = tensor2img(cropped_face_t, rgb2bgr=True, min_max=(-1, 1))
                            restored_face = cropped_face.copy()
                        
                    else:
                        restored_face = cropped_face.copy()
                    
                    if face_restore_visibility < 1:
                        restored_face = cropped_face * (1 - face_restore_visibility) + restored_face * face_restore_visibility

                    restored_face = restored_face.astype("uint8")
                    self.face_helper.add_restored_face(restored_face)
                
                self.face_helper.get_inverse_affine(None)

                restored_img = self.face_helper.paste_faces_to_input_image()
                restored_img = restored_img[:, :, ::-1]

                if original_resolution != restored_img.shape[0:2]:
                    restored_img = cv2.resize(restored_img, (0, 0), fx=original_resolution[1]/restored_img.shape[1], fy=original_resolution[0]/restored_img.shape[0], interpolation=cv2.INTER_AREA)

                self.face_helper.clean_all()

                # out_images[i] = restored_img
                out_images.append(restored_img)

                if state.interrupted or model_management.processing_interrupted():
                    logger.status("Interrupted by User")
                    return input_image
                
                pbar.update(1)

            restored_img_np = np.array(out_images).astype(np.float32) / 255.0
            restored_img_tensor = torch.from_numpy(restored_img_np)

            result = restored_img_tensor

            progress_bar_reset(pbar)

        # if hasattr(self, "last_swapped_bboxes"):
        self.last_swapped_bboxes = None
        # if hasattr(self, "last_swapped_indices"):
        # self.last_swapped_indices = None
        
        return result


    def execute(self, enabled, original_image, FaceSwap_model, detect_gender_source, detect_gender_input, source_faces_index, input_faces_index, console_log_level, face_restore_visibility, codeformer_fidelity, target_face_image=None, target_face_model=None, FaceRestore_model=None, FaceDetection_model=None, faces_order=None, face_boost=None):

        device = model_management.get_torch_device()

        if isinstance(original_image, torch.Tensor) and original_image.device != device:
            original_image = original_image.to(device)

        if face_boost is not None:
            self.face_boost_enabled = face_boost["enabled"]
            self.boost_model = face_boost.get("face_restore_model")
            self.interpolation = face_boost["interpolation"]
            self.boost_model_visibility = face_boost["visibility"]
            self.boost_cf_weight = face_boost["codeformer_fidelity"]
            self.restore = face_boost["restore_with_main_after"]
        else:
            self.face_boost_enabled = False

        # Effective restore/detect models: the FaceBoost bundle wins when it
        # carries one, otherwise the node's own loader inputs are used.
        if self.face_boost_enabled and self.boost_model is not None:
            restore_info = self.boost_model
            restore_visibility, restore_fidelity = self.boost_model_visibility, self.boost_cf_weight
        else:
            restore_info = FaceRestore_model
            restore_visibility, restore_fidelity = face_restore_visibility, codeformer_fidelity
        det_name = detection_model_name(FaceDetection_model)

        if faces_order is None:
            faces_order = self.faces_order

        set_console_level(console_log_level)

        if not enabled:
            return (original_image, target_face_model)
        elif target_face_image is None and target_face_model is None:
            logger.error("Please provide 'target_face_image' or 'target_face_model'")
            return (original_image, target_face_model)

        script = FaceSwapScript()
        pil_images = batch_tensor_to_pil(original_image)

        if len(pil_images) > 0:

            if target_face_image is not None:
                source = tensor_to_pil(target_face_image)
            else:
                source = None

            p = ProcessingImg2Img(pil_images)
            script.process(
                p=p,
                img=source,
                enable=True,
                source_faces_index=source_faces_index,
                faces_index=input_faces_index,
                model=FaceSwap_model,
                swap_in_source=True,
                swap_in_generated=True,
                gender_source=detect_gender_source,
                gender_target=detect_gender_input,
                face_model=target_face_model,
                faces_order=faces_order,
                # face boost:
                face_boost_enabled=self.face_boost_enabled,
                face_restore_model=self.boost_model,
                face_restore_visibility=self.boost_model_visibility,
                codeformer_fidelity=self.boost_cf_weight,
                interpolation=self.interpolation,
            )
            result = batched_pil_to_tensor(p.init_images)
            if len(p.bbox) > 0:
                self.last_swapped_bboxes = p.bbox

            if target_face_model is None:
                current_face_model = get_current_faces_model()
                face_model_to_provide = current_face_model[0] if (current_face_model is not None and len(current_face_model) > 0) else target_face_model
            else:
                face_model_to_provide = target_face_model

            if self.restore or not self.face_boost_enabled:
                result = ReFactorFaceSwap.restore_face(self, result, restore_info, restore_visibility, restore_fidelity, det_name)

        else:
            image_black = Image.new("RGB", (512, 512))
            result = batched_pil_to_tensor([image_black])
            face_model_to_provide = None
            original_image = result

        return (result, face_model_to_provide, original_image)


class ReFactorFaceSwapOpt:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "enabled": ("BOOLEAN", {"default": True, "label_off": "OFF", "label_on": "ON"}),
                "original_image": ("IMAGE",),
                "FaceSwap_model": ("FACE_SWAP_MODEL",),
                "face_restore_visibility": ("FLOAT", {"default": 1, "min": 0.1, "max": 1, "step": 0.05}),
                "codeformer_fidelity": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1, "step": 0.05,
                                                  "tooltip": "CodeFormer fidelity weight (0 = better quality, 1 = better identity)"}),
            },
            "optional": {
                "target_face_image": ("IMAGE",),
                "target_face_model": ("FACE_MODEL",),
                "options": ("OPTIONS",),
                "FaceRestore_model": ("FACE_RESTORE_MODEL",),
                "FaceDetection_model": ("FACE_DETECT_MODEL",),
                "face_boost": ("FACE_BOOST",),
            }
        }

    RETURN_TYPES = ("IMAGE","FACE_MODEL","IMAGE")
    RETURN_NAMES = ("SWAPPED_IMAGE","FACE_MODEL","ORIGINAL_IMAGE")
    FUNCTION = "execute"
    CATEGORY = "ReFactor"

    def __init__(self):
        # self.face_helper = None
        self.faces_order = ["large-small", "large-small"]
        self.detect_gender_input = "no"
        self.detect_gender_source = "no"
        self.input_faces_index = "0"
        self.source_faces_index = "0"
        self.console_log_level = 1
        self.restore_swapped_only = True
        # self.face_size = 512
        self.face_boost_enabled = False
        self.restore = True
        self.boost_model = None
        self.interpolation = "Bicubic"
        self.boost_model_visibility = 1
        self.boost_cf_weight = 0.5

    def execute(self, enabled, original_image, FaceSwap_model, face_restore_visibility, codeformer_fidelity, target_face_image=None, target_face_model=None, options=None, FaceRestore_model=None, FaceDetection_model=None, face_boost=None):

        if options is not None:
            self.faces_order = [options["input_faces_order"], options["source_faces_order"]]
            self.console_log_level = options["console_log_level"]
            self.detect_gender_input = options["detect_gender_input"]
            self.detect_gender_source = options["detect_gender_source"]
            self.input_faces_index = options["input_faces_index"]
            self.source_faces_index = options["source_faces_index"]
            self.restore_swapped_only = options["restore_swapped_only"]

        if face_boost is not None:
            self.face_boost_enabled = face_boost["enabled"]
            self.restore = face_boost["restore_with_main_after"]
        else:
            self.face_boost_enabled = False

        result = ReFactorFaceSwap.execute(
            self,enabled,original_image,FaceSwap_model,self.detect_gender_source,self.detect_gender_input,self.source_faces_index,self.input_faces_index,self.console_log_level,face_restore_visibility,codeformer_fidelity,target_face_image,target_face_model,FaceRestore_model,FaceDetection_model,self.faces_order, face_boost=face_boost
        )

        return result


class ReFactorLoadFaceModel:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "face_model": (get_model_names(get_facemodels),),
            }
        }

    RETURN_TYPES = ("FACE_MODEL","STRING")
    RETURN_NAMES = ("FACE_MODEL","FACE_MODEL_NAME")
    FUNCTION = "load_model"
    CATEGORY = "ReFactor"

    def load_model(self, face_model):
        self.face_model = face_model
        face_model = face_model.split(".safetensors")[0] if ".safetensors" in face_model else face_model
        out = None
        if self.face_model != "none":
            face_model_path = None
            for d in model_paths.face_models_dirs():
                candidate = os.path.join(d, face_model + ".safetensors")
                if os.path.exists(candidate):
                    face_model_path = candidate
                    break
            if face_model_path is None:
                logger.error(f"Face model '{face_model}.safetensors' not found in any face models dir")
            else:
                out = load_face_model(face_model_path)
        return (out, face_model)


class ReFactorSetWeight:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "input_image": ("IMAGE",),
                "faceswap_weight": (["0%", "12.5%", "25%", "37.5%", "50%", "62.5%", "75%", "87.5%", "100%"], {"default": "50%"}),
            },
            "optional": {
                "source_image": ("IMAGE",),
                "face_model": ("FACE_MODEL",),
            }
        }
    
    RETURN_TYPES = ("IMAGE","FACE_MODEL")
    RETURN_NAMES = ("INPUT_IMAGE","FACE_MODEL")
    FUNCTION = "set_weight"

    OUTPUT_NODE = True

    CATEGORY = "ReFactor"

    def set_weight(self, input_image, faceswap_weight, face_model=None, source_image=None):

        if input_image is None:
            logger.error("Please provide `input_image`")
            return (input_image,None)
        
        if source_image is None and face_model is None:
            logger.error("Please provide `source_image` or `face_model`")
            return (input_image,None)

        weight = float(faceswap_weight.split("%")[0])

        images = []
        faces = [] if face_model is None else [face_model]
        embeddings = [] if face_model is None else [face_model.embedding]

        if weight == 0:
            images = [input_image]
            faces = []
            embeddings = []
        elif weight == 100:
            if face_model is None:
                images = [source_image]
        else:
            if weight > 50:
                images = [input_image]
                count = round(100/(100-weight))
            else:
                if face_model is None:
                    images = [source_image]
                count = round(100/(weight))
            for i in range(count-1):
                if weight > 50:
                    if face_model is None:
                        images.append(source_image)
                    else:
                        faces.append(face_model)
                        embeddings.append(face_model.embedding)
                else:
                    images.append(input_image)
        
        images_list: List[Image.Image] = []

        set_console_level(0)

        if len(images) > 0:

            for image in images:
                img = tensor_to_pil(image)
                images_list.append(img)

            for image in images_list:
                face = ReFactorBuildFaceModel.build_face_model(self,image)
                if isinstance(face, str):
                    continue
                faces.append(face)
                embeddings.append(face.embedding)
        
        if len(faces) > 0:
            blended_embedding = np.mean(embeddings, axis=0)
            blended_face = Face(
                bbox=faces[0].bbox,
                kps=faces[0].kps,
                det_score=faces[0].det_score,
                landmark_3d_68=faces[0].landmark_3d_68,
                pose=faces[0].pose,
                landmark_2d_106=faces[0].landmark_2d_106,
                embedding=blended_embedding,
                gender=faces[0].gender,
                age=faces[0].age
            )
            if blended_face is None:
                no_face_msg = "Something went wrong, please try another set of images"
                logger.error(no_face_msg)

        return (input_image,blended_face)


class ReFactorBuildFaceModel:
    def __init__(self):
        self.output_dir = FACE_MODELS_PATH

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "save_mode": ("BOOLEAN", {"default": True, "label_off": "OFF", "label_on": "ON"}),
                "send_only": ("BOOLEAN", {"default": False, "label_off": "NO", "label_on": "YES"}),
                "face_model_name": ("STRING", {"default": "default"}),
                "compute_method": (["Mean", "Median", "Mode"], {"default": "Mean"}),
            },
            "optional": {
                "images": ("IMAGE",),
                "face_models": ("FACE_MODEL",),
            }
        }

    RETURN_TYPES = ("FACE_MODEL",)
    FUNCTION = "blend_faces"

    OUTPUT_NODE = True

    CATEGORY = "ReFactor"

    def build_face_model(self, image: Image.Image, det_size=(640, 640)):
        logging.StreamHandler.terminator = "\n"
        if image is None:
            error_msg = "Please load an Image"
            logger.error(error_msg)
            return error_msg
        image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        face_model = analyze_faces(image, det_size)

        if len(face_model) == 0:
            print("")
            det_size_half = half_det_size(det_size)
            face_model = analyze_faces(image, det_size_half)
            if face_model is not None and len(face_model) > 0:
                print("...........................................................", end=" ")

        if face_model is not None and len(face_model) > 0:
            return face_model[0]
        else:
            no_face_msg = "No face found, please try another image"
            # logger.error(no_face_msg)
            return no_face_msg

    def blend_faces(self, save_mode, send_only, face_model_name, compute_method, images=None, face_models=None):
        global BLENDED_FACE_MODEL
        blended_face: Face = BLENDED_FACE_MODEL

        if send_only and blended_face is None:
            send_only = False

        if (images is not None or face_models is not None) and not send_only:

            faces = []
            embeddings = []

            set_console_level(0)

            if images is not None:
                images_list: List[Image.Image] = batch_tensor_to_pil(images)

                n = len(images_list)

                for i,image in enumerate(images_list):
                    logging.StreamHandler.terminator = " "
                    logger.status(f"Building Face Model {i+1} of {n}...")
                    face = self.build_face_model(image)
                    if isinstance(face, str):
                        logger.error(f"No faces found in image {i+1}, skipping")
                        continue
                    else:
                        print(f"{int(((i+1)/n)*100)}%")
                    faces.append(face)
                    embeddings.append(face.embedding)

            elif face_models is not None:

                n = len(face_models)

                for i,face_model in enumerate(face_models):
                    logging.StreamHandler.terminator = " "
                    logger.status(f"Extracting Face Model {i+1} of {n}...")
                    face = face_model
                    if isinstance(face, str):
                        logger.error(f"No faces found for face_model {i+1}, skipping")
                        continue
                    else:
                        print(f"{int(((i+1)/n)*100)}%")
                    faces.append(face)
                    embeddings.append(face.embedding)

            logging.StreamHandler.terminator = "\n"
            if len(faces) > 0:
                # compute_method_name = "Mean" if compute_method == 0 else "Median" if compute_method == 1 else "Mode"
                logger.status(f"Blending with Compute Method '{compute_method}'...")
                blended_embedding = np.mean(embeddings, axis=0) if compute_method == "Mean" else np.median(embeddings, axis=0) if compute_method == "Median" else stat_mode(np.stack(embeddings), axis=0).astype(np.float32)
                blended_face = Face(
                    bbox=faces[0].bbox,
                    kps=faces[0].kps,
                    det_score=faces[0].det_score,
                    landmark_3d_68=faces[0].landmark_3d_68,
                    pose=faces[0].pose,
                    landmark_2d_106=faces[0].landmark_2d_106,
                    embedding=blended_embedding,
                    gender=faces[0].gender,
                    age=faces[0].age
                )
                if blended_face is not None:
                    BLENDED_FACE_MODEL = blended_face
                    if save_mode:
                        face_model_path = os.path.join(FACE_MODELS_PATH, face_model_name + ".safetensors")
                        save_face_model(blended_face,face_model_path)
                        # done_msg = f"Face model has been saved to '{face_model_path}'"
                        # logger.status(done_msg)
                    logger.status("--Done!--")
                    # return (blended_face,)
                else:
                    no_face_msg = "Something went wrong, please try another set of images"
                    logger.error(no_face_msg)
                    # return (blended_face,)
            # logger.status("--Done!--")
        if images is None and face_models is None:
            logger.error("Please provide `images` or `face_models`")
        return (blended_face,)


class ReFactorSaveFaceModel:
    def __init__(self):
        self.output_dir = FACE_MODELS_PATH

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "save_mode": ("BOOLEAN", {"default": True, "label_off": "OFF", "label_on": "ON"}),
                "face_model_name": ("STRING", {"default": "default"}),
                "select_face_index": ("INT", {"default": 0, "min": 0}),
            },
            "optional": {
                "image": ("IMAGE",),
                "face_model": ("FACE_MODEL",),
            }
        }

    RETURN_TYPES = ()
    FUNCTION = "save_model"

    OUTPUT_NODE = True

    CATEGORY = "ReFactor"

    def save_model(self, save_mode, face_model_name, select_face_index, image=None, face_model=None, det_size=(640, 640)):
        if save_mode and image is not None:
            source = tensor_to_pil(image)
            source = cv2.cvtColor(np.array(source), cv2.COLOR_RGB2BGR)
            set_console_level(0)
            logger.status("Building Face Model...")
            face_model_raw = analyze_faces(source, det_size)
            if len(face_model_raw) == 0:
                det_size_half = half_det_size(det_size)
                face_model_raw = analyze_faces(source, det_size_half)
            try:
                face_model = face_model_raw[select_face_index]
            except:
                logger.error("No face(s) found")
                return face_model_name
            logger.status("--Done!--")
        if save_mode and (face_model != "none" or face_model is not None):
            face_model_path = os.path.join(self.output_dir, face_model_name + ".safetensors")
            save_face_model(face_model,face_model_path)
        if image is None and face_model is None:
            logger.error("Please provide `face_model` or `image`")
        return face_model_name


class ReFactorRestoreFace:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "FaceRestore_model": ("FACE_RESTORE_MODEL",),
                "visibility": ("FLOAT", {"default": 1, "min": 0.0, "max": 1, "step": 0.05}),
                "codeformer_fidelity": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1, "step": 0.05,
                                                  "tooltip": "CodeFormer fidelity weight (0 = better quality, 1 = better identity)"}),
            },
            "optional": {
                "FaceDetection_model": ("FACE_DETECT_MODEL",),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "execute"
    CATEGORY = "ReFactor"

    def execute(self, image, FaceRestore_model, visibility, codeformer_fidelity, FaceDetection_model=None):
        result = ReFactorFaceSwap.restore_face(
            self, image, FaceRestore_model, visibility, codeformer_fidelity,
            detection_model_name(FaceDetection_model)
        )
        return (result,)


class ReFactorRestoreFaceAdvanced:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "FaceRestore_model": ("FACE_RESTORE_MODEL",),
                "visibility": ("FLOAT", {"default": 1, "min": 0.0, "max": 1, "step": 0.05}),
                "codeformer_fidelity": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1, "step": 0.05,
                                                  "tooltip": "CodeFormer fidelity weight (0 = better quality, 1 = better identity)"}),
                "face_selection": (["all", "filter", "largest"],{"default": "all"}),
            },
            "optional": {
                "FaceDetection_model": ("FACE_DETECT_MODEL",),
                "sort_by": (["area", "x_position", "y_position", "detection_confidence"],{"default": "area"}),
                "reverse_order": ("BOOLEAN", {"default": False}),
                "take_start": ("INT", {"default": 0, "min": 0, "max": 100, "step": 1}),
                "take_count": ("INT", {"default": 1, "min": 1, "max": 100, "step": 1}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "execute"
    CATEGORY = "ReFactor"

    def execute(
            self, image, FaceRestore_model, visibility, codeformer_fidelity, face_selection, sort_by="area", reverse_order=False, take_start=0, take_count=1, FaceDetection_model=None
        ):

        min_x_position=0.0
        max_x_position=1.0
        min_y_position=0.0
        max_y_position=1.0

        result = image

        face_restore_model = FaceRestore_model

        if face_restore_model is not None and not model_management.processing_interrupted():

            global FACE_SIZE, FACE_HELPER

            self.face_helper = FACE_HELPER

            restore_model_name = face_restore_model.get("name") if isinstance(face_restore_model, dict) else face_restore_model

            faceSize = 512
            if "1024" in restore_model_name.lower():
                faceSize = 1024
            elif "2048" in restore_model_name.lower():
                faceSize = 2048

            logger.status(f"Restoring with {restore_model_name} | Face Size is set to {faceSize}")

            model_path = face_restore_model.get("path") if isinstance(face_restore_model, dict) else None
            if not model_path or not os.path.exists(model_path):
                model_path = _faceboost_restorer.ensure_facerestore_model(restore_model_name)
            if model_path is None:
                logger.error(f"Face restoration model '{restore_model_name}' could not be found or downloaded.")
                return image

            device = model_management.get_torch_device()

            if "codeformer" in face_restore_model.lower():

                codeformer_net = ARCH_REGISTRY.get("CodeFormer")(
                    dim_embd=512,
                    codebook_size=1024,
                    n_head=8,
                    n_layers=9,
                    connect_list=["32", "64", "128", "256"],
                ).to(device)
                checkpoint = torch.load(model_path, weights_only=True, map_location="cpu")["params_ema"]
                codeformer_net.load_state_dict(checkpoint)
                facerestore_model = codeformer_net.eval()

            elif ".onnx" in restore_model_name:

                ort_session = create_session(model_path, providers=resolve_providers())
                ort_session_inputs = {}
                facerestore_model = ort_session

            else:

                sd = comfy.utils.load_torch_file(model_path, safe_load=True)
                facerestore_model = model_loading.load_state_dict(sd).eval()
                facerestore_model.to(device)

            if faceSize != FACE_SIZE or self.face_helper is None:
                self.face_helper = FaceRestoreHelper(1, face_size=faceSize, crop_ratio=(1, 1), det_model=detection_model_name(FaceDetection_model), save_ext='png', use_parse=True, device=device)
                FACE_SIZE = faceSize
                FACE_HELPER = self.face_helper

            image_np = 255. * result.cpu().numpy()

            total_images = image_np.shape[0]

            out_images = []
            
            pbar = progress_bar(total_images)

            for i in range(total_images):

                cur_image_np = image_np[i,:, :, ::-1]

                original_resolution = cur_image_np.shape[0:2]

                if facerestore_model is None or self.face_helper is None:
                    return result

                self.face_helper.clean_all()
                self.face_helper.read_image(cur_image_np)
                self.face_helper.get_face_landmarks_5(only_center_face=False, resize=640, eye_dist_threshold=5)
                self.face_helper.align_warp_face()

                # Face-Filter Mode

                # Фильтрация лиц
                if face_selection != "all" and self.face_helper.cropped_faces:
                    # Собираем информацию о лицах для фильтрации
                    face_info = []
                    img_height, img_width = cur_image_np.shape[0:2]
                    
                    for j, face in enumerate(self.face_helper.cropped_faces):
                        # Используем центр лица вместо левого верхнего угла
                        if hasattr(self.face_helper, 'det_faces') and len(self.face_helper.det_faces) > j:
                            bbox = self.face_helper.det_faces[j]
                            # Вычисляем центр лица для более точного позиционирования
                            x1 = ((bbox[0] + bbox[2]) / 2) / img_width  # центр x
                            y1 = ((bbox[1] + bbox[3]) / 2) / img_height  # центр y
                            area = face.shape[0] * face.shape[1]
                            confidence = bbox[4] if len(bbox) > 4 else 1.0
                        else:
                            # Если информация о bbox недоступна, используем приблизительные данные
                            area = face.shape[0] * face.shape[1]
                            x1, y1 = 0.5, 0.5  # центр изображения
                            confidence = 1.0
                            
                        face_info.append({
                            'index': j,
                            'area': area,
                            'x_position': x1,
                            'y_position': y1,
                            'detection_confidence': confidence
                        })
                    
                    # Сначала сортируем все лица по выбранному критерию
                    all_indices = list(range(len(self.face_helper.cropped_faces)))
                    
                    # Вывод для x_position и y_position
                    if sort_by == "y_position":
                        all_positions = [(idx, face_info[idx]['y_position']) for idx in all_indices]
                    elif sort_by == "x_position":
                        all_positions = [(idx, face_info[idx]['x_position']) for idx in all_indices]
                    
                    # Сортировка по выбранному критерию
                    sorted_indices = sorted(
                        all_indices,
                        key=lambda idx: face_info[idx][sort_by],
                        reverse=reverse_order
                    )
                    
                    # Отладочный вывод после сортировки
                    if sort_by == "y_position":
                        sorted_positions = [(idx, face_info[idx]['y_position']) for idx in sorted_indices]
                    elif sort_by == "x_position":
                        sorted_positions = [(idx, face_info[idx]['x_position']) for idx in sorted_indices]
                    
                    # Применяем фильтрацию в зависимости от режима
                    if face_selection == "filter":
                        # Фильтрация по координатам
                        filtered_indices = [
                            idx for idx in sorted_indices
                            if min_x_position <= face_info[idx]['x_position'] <= max_x_position and
                               min_y_position <= face_info[idx]['y_position'] <= max_y_position
                        ]
                        
                        # Выборка по take_start и take_count
                        selected_indices = filtered_indices[take_start:take_start + take_count]
                    
                    elif face_selection == "largest":
                        # При выборе "largest" просто берем take_count лиц с наибольшей площадью, начиная с take_start
                        selected_indices = sorted_indices[take_start:take_start + take_count]
                    
                    elif face_selection == "index":
                        # В режиме "index" просто берем лица, начиная с take_start
                        selected_indices = sorted_indices[take_start:take_start + take_count]

                    if selected_indices:
                        self.face_helper.cropped_faces = [self.face_helper.cropped_faces[j] for j in selected_indices]
                        if hasattr(self.face_helper, 'restored_faces'):
                            self.face_helper.restored_faces = []
                        if hasattr(self.face_helper, 'affine_matrices'):
                            self.face_helper.affine_matrices = [self.face_helper.affine_matrices[j] for j in selected_indices]
                        if hasattr(self.face_helper, 'det_faces'):
                            self.face_helper.det_faces = [self.face_helper.det_faces[j] for j in selected_indices]

                # Face-Filter Mode END
                
                restored_face = None

                for idx, cropped_face in enumerate(self.face_helper.cropped_faces):

                    cropped_face_t = img2tensor(cropped_face / 255., bgr2rgb=True, float32=True)
                    normalize(cropped_face_t, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5), inplace=True)
                    cropped_face_t = cropped_face_t.unsqueeze(0).to(device)

                    try:

                        with torch.no_grad():

                            if ".onnx" in face_restore_model: # ONNX models

                                for ort_session_input in ort_session.get_inputs():
                                    if ort_session_input.name == "input":
                                        cropped_face_prep = prepare_cropped_face(cropped_face)
                                        ort_session_inputs[ort_session_input.name] = cropped_face_prep
                                    if ort_session_input.name == "weight":
                                        weight = np.array([ 1 ], dtype = np.double)
                                        ort_session_inputs[ort_session_input.name] = weight

                                output = ort_session.run(None, ort_session_inputs)[0][0]
                                restored_face = normalize_cropped_face(output)

                            else: # PTH models

                                output = facerestore_model(cropped_face_t, w=codeformer_fidelity)[0] if "codeformer" in restore_model_name.lower() else facerestore_model(cropped_face_t)[0]
                                restored_face = tensor2img(output, rgb2bgr=True, min_max=(-1, 1))

                        del output
                        torch.cuda.empty_cache()

                    except Exception as error:

                        print(f"\tFailed inference: {error}", file=sys.stderr)
                        restored_face = cropped_face.copy()

                    if visibility < 1:
                        restored_face = cropped_face * (1 - visibility) + restored_face * visibility

                    restored_face = restored_face.astype("uint8")
                    self.face_helper.add_restored_face(restored_face)

                self.face_helper.get_inverse_affine(None)

                restored_img = self.face_helper.paste_faces_to_input_image()
                restored_img = restored_img[:, :, ::-1]

                if original_resolution != restored_img.shape[0:2]:
                    restored_img = cv2.resize(restored_img, (0, 0), fx=original_resolution[1]/restored_img.shape[1], fy=original_resolution[0]/restored_img.shape[0], interpolation=cv2.INTER_AREA)

                self.face_helper.clean_all()

                out_images.append(restored_img)

                if state.interrupted or model_management.processing_interrupted():
                    logger.status("Interrupted by User")
                    return image
                
                pbar.update(1)

            restored_img_np = np.array(out_images).astype(np.float32) / 255.0
            restored_img_tensor = torch.from_numpy(restored_img_np)

            result = restored_img_tensor

            progress_bar_reset(pbar)

        return (result,)


class ReFactorImageDuplicator:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "count": ("INT", {"default": 1, "min": 0}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("IMAGES",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "execute"
    CATEGORY = "ReFactor"

    def execute(self, image, count):
        images = [image for i in range(count)]
        return (images,)


class ReFactorImageRGBA2RGB:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "execute"
    CATEGORY = "ReFactor"

    def execute(self, image):
        out = rgba2rgb_tensor(image)
        return (out,)


class ReFactorMakeFaceModelBatch:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "face_model1": ("FACE_MODEL",),
            },
            "optional": {
                "face_model2": ("FACE_MODEL",),
                "face_model3": ("FACE_MODEL",),
                "face_model4": ("FACE_MODEL",),
                "face_model5": ("FACE_MODEL",),
                "face_model6": ("FACE_MODEL",),
                "face_model7": ("FACE_MODEL",),
                "face_model8": ("FACE_MODEL",),
                "face_model9": ("FACE_MODEL",),
                "face_model10": ("FACE_MODEL",),
            },
        }

    RETURN_TYPES = ("FACE_MODEL",)
    RETURN_NAMES = ("FACE_MODELS",)
    FUNCTION = "execute"

    CATEGORY = "ReFactor"

    def execute(self, **kwargs):
        if len(kwargs) > 0:
            face_models = [value for value in kwargs.values()]
            return (face_models,)
        else:
            logger.error("Please provide at least 1 `face_model`")
            return (None,)


class ReFactorOptions:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "input_faces_order": (
                    ["left-right","right-left","top-bottom","bottom-top","small-large","large-small"], {"default": "large-small"}
                ),
                "input_faces_index": ("STRING", {"default": "0"}),
                "detect_gender_input": (["no","female","male"], {"default": "no"}),
                "source_faces_order": (
                    ["left-right","right-left","top-bottom","bottom-top","small-large","large-small"], {"default": "large-small"}
                ),
                "source_faces_index": ("STRING", {"default": "0"}),
                "detect_gender_source": (["no","female","male"], {"default": "no"}),
                "console_log_level": ([0, 1, 2], {"default": 1}),
                "restore_swapped_only": ("BOOLEAN", {"default": True, "label_off": "no", "label_on": "yes"})
            }
        }

    RETURN_TYPES = ("OPTIONS",)
    FUNCTION = "execute"
    CATEGORY = "ReFactor"

    def execute(self,input_faces_order, input_faces_index, detect_gender_input, source_faces_order, source_faces_index, detect_gender_source, console_log_level, restore_swapped_only):
        options: dict = {
            "input_faces_order": input_faces_order,
            "input_faces_index": input_faces_index,
            "detect_gender_input": detect_gender_input,
            "source_faces_order": source_faces_order,
            "source_faces_index": source_faces_index,
            "detect_gender_source": detect_gender_source,
            "console_log_level": console_log_level,
            "restore_swapped_only": restore_swapped_only,
        }
        return (options, )


class ReFactorFaceBoost:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "enabled": ("BOOLEAN", {"default": True, "label_off": "OFF", "label_on": "ON"}),
                "interpolation": (["Nearest","Bilinear","Bicubic","Lanczos"], {"default": "Bicubic"}),
                "visibility": ("FLOAT", {"default": 1, "min": 0.1, "max": 1, "step": 0.05}),
                "codeformer_fidelity": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1, "step": 0.05,
                                                  "tooltip": "CodeFormer fidelity weight (0 = better quality, 1 = better identity)"}),
                "restore_with_main_after": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "FaceRestore_model": ("FACE_RESTORE_MODEL",),
            }
        }

    RETURN_TYPES = ("FACE_BOOST",)
    FUNCTION = "execute"
    CATEGORY = "ReFactor"

    def execute(self, enabled, interpolation, visibility, codeformer_fidelity, restore_with_main_after, FaceRestore_model=None):
        face_boost: dict = {
            "enabled": enabled,
            "face_restore_model": FaceRestore_model,
            "interpolation": interpolation,
            "visibility": visibility,
            "codeformer_fidelity": codeformer_fidelity,
            "restore_with_main_after": restore_with_main_after,
        }
        return (face_boost, )


class ReFactorUnload:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "trigger": ("IMAGE", ),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "execute"
    CATEGORY = "ReFactor"

    def execute(self, trigger):
        unload_all_models()
        return (trigger,)


class ReFactorFaceSimilarity:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image1": ("IMAGE",),
                "image2": ("IMAGE",),
            }
        }

    RETURN_TYPES = ("FLOAT", "STRING")
    RETURN_NAMES = ("similarity_float", "similarity_text")
    FUNCTION = "compare_faces"
    CATEGORY = "ReFactor"

    def compare_faces(self, image1, image2):
        
        set_console_level(0)

        # 1. Конвертируем тензоры ComfyUI в формат OpenCV (BGR)
        img1_cv = 255. * image1[0].cpu().numpy()
        img1_cv = cv2.cvtColor(img1_cv.astype(np.uint8), cv2.COLOR_RGB2BGR)

        img2_cv = 255. * image2[0].cpu().numpy()
        img2_cv = cv2.cvtColor(img2_cv.astype(np.uint8), cv2.COLOR_RGB2BGR)

        # 2. Ищем лица через
        faces1 = analyze_faces(img1_cv, det_size=(640, 640))
        faces2 = analyze_faces(img2_cv, det_size=(640, 640))

        # 3. Защита от отсутствия лиц
        if not faces1 or not faces2:
            return (0.0, "Face not found in one or both images")

        # Берем первые найденные лица
        face1 = faces1[0]
        face2 = faces2[0]

        # 4. Вычисляем косинусное сходство (Cosine Similarity)
        emb1 = face1.normed_embedding
        emb2 = face2.normed_embedding
        
        # Скалярное произведение нормализованных векторов
        similarity = np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2))

        # 5. Форматируем результат
        sim_float = float(similarity)
        sim_float = max(0.0, min(1.0, sim_float)) 
        sim_text = f"{sim_float * 100:.2f}%"

        return (sim_float, sim_text)


NODE_CLASS_MAPPINGS = {
    # --- MAIN NODES ---
    "ReFactorFaceSwap": ReFactorFaceSwap,
    "ReFactorFaceSwapOpt": ReFactorFaceSwapOpt,
    "ReFactorOptions": ReFactorOptions,
    "ReFactorFaceBoost": ReFactorFaceBoost,
    "ReFactorMaskBuilder": ReFactorMaskBuilder,
    "ReFactorSetWeight": ReFactorSetWeight,
    # --- Operations with Face Models ---
    "ReFactorSaveFaceModel": ReFactorSaveFaceModel,
    "ReFactorLoadFaceModel": ReFactorLoadFaceModel,
    "ReFactorBuildFaceModel": ReFactorBuildFaceModel,
    "ReFactorMakeFaceModelBatch": ReFactorMakeFaceModelBatch,
    # --- Additional Nodes ---
    "ReFactorRestoreFace": ReFactorRestoreFace,
    "ReFactorRestoreFaceAdvanced": ReFactorRestoreFaceAdvanced,
    "ReFactorFaceSimilarity": ReFactorFaceSimilarity,
    "ReFactorImageDuplicator": ReFactorImageDuplicator,
    "ReFactorImageRGBA2RGB": ReFactorImageRGBA2RGB,
    "ReFactorUnload": ReFactorUnload,
    "ReFactorDLSS5Enhancer": ReFactorDLSS5Enhancer,
    # --- Model Loaders ---
    "ReFactorFaceSwapModelLoader": ReFactorFaceSwapModelLoader,
    "ReFactorFaceRestoreModelLoader": ReFactorFaceRestoreModelLoader,
    "ReFactorFaceDetectionModelLoader": ReFactorFaceDetectionModelLoader,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    # --- MAIN NODES ---
    "ReFactorFaceSwap": "ReFactor ⚡ Fast Face Swap",
    "ReFactorFaceSwapOpt": "ReFactor ⚡ Fast Face Swap [OPTIONS]",
    "ReFactorOptions": "ReFactor ⚡ Options",
    "ReFactorFaceBoost": "ReFactor ⚡ Face Booster",
    "ReFactorMaskBuilder": "ReFactor ⚡ Mask Builder",
    "ReFactorSetWeight": "ReFactor ⚡ Set Face Swap Weight",
    # --- Operations with Face Models ---
    "ReFactorSaveFaceModel": "Save Face Model ⚡ ReFactor",
    "ReFactorLoadFaceModel": "Load Face Model ⚡ ReFactor",
    "ReFactorBuildFaceModel": "Build Blended Face Model ⚡ ReFactor",
    "ReFactorMakeFaceModelBatch": "Make Face Model Batch ⚡ ReFactor",
    # --- Additional Nodes ---
    "ReFactorRestoreFace": "Restore Face ⚡ ReFactor",
    "ReFactorRestoreFaceAdvanced": "Restore Face Advanced ⚡ ReFactor",
    "ReFactorFaceSimilarity": "Face Similarity ⚡ ReFactor",
    "ReFactorImageDuplicator": "Image Duplicator (List) ⚡ ReFactor",
    "ReFactorImageRGBA2RGB": "Convert RGBA to RGB ⚡ ReFactor",
    "ReFactorUnload": "Unload ReFactor Models ⚡ ReFactor",
    "ReFactorDLSS5Enhancer": "DLSS5 Frame Enhancer ⚡ ReFactor",
    # --- Model Loaders ---
    "ReFactorFaceSwapModelLoader": "FaceSwap Model Loader ⚡ ReFactor",
    "ReFactorFaceRestoreModelLoader": "FaceRestore Model Loader ⚡ ReFactor",
    "ReFactorFaceDetectionModelLoader": "FaceDetection Model Loader ⚡ ReFactor",
}
