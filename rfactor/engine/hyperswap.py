import cv2
import numpy as np
from .face_objects import BaseONNXModel

class HyperSwapper(BaseONNXModel):
    """Hyperswap-family model wrapper"""

    def __init__(self, model_file, providers=None):
        super().__init__(model_file, providers)

    # get 5 keypoints from a Face object
    def get_landmarks_5(self, face):
        if hasattr(face, 'landmark_5') and face.landmark_5 is not None:
            return face.landmark_5
        elif hasattr(face, 'kps') and face.kps is not None:
            return face.kps
        elif hasattr(face, 'landmark') and face.landmark is not None:
            if face.landmark.shape[0] >= 68:
                idxs = [36, 45, 30, 48, 54]
                return face.landmark[idxs]
        return None

    # compute the affine transform
    def get_affine_transform(self, src_pts, dst_pts):
        M, _ = cv2.estimateAffinePartial2D(src_pts, dst_pts)
        return M
    
    # build an oval gradient mask (unclipped)
    def create_gradient_mask(self, crop_size=256):
        # 1. empty mask (all zeros)
        mask = np.zeros((crop_size, crop_size), dtype=np.float32)
        
        # 2. ellipse center and axes
        center = (crop_size // 2, crop_size // 2)
        axes = (int(crop_size * 0.35), int(crop_size * 0.4))
        
        # 3. draw the filled ellipse (value 1.0)
        cv2.ellipse(
            mask,          # array to draw on
            center,        # ellipse center
            axes,          # axes (width, height)
            angle=0,       # rotation angle
            startAngle=0,  # arc start angle
            endAngle=360,  # arc end angle (360 = full ellipse)
            color=1.0,     # fill value (white = 1.0)
            thickness=-1   # -1 = fill the whole ellipse   
        )
        
        # 4. blur for smooth edges
        blur_ksize = 15  # odd size so the kernel is symmetric
        mask = cv2.GaussianBlur(mask, (blur_ksize, blur_ksize), 0)
        
        # 5. clamp to [0, 1]
        mask = np.clip(mask, 0, 1)
        
        return mask

    def paste_back(self, target_img, swapped_face, M, crop_size=256):
        
        # 1. soft mask (erode + blur)
        mask = self.create_gradient_mask(crop_size)

        # convert to a 3-channel mask
        mask_3c = np.stack([mask] * 3, axis=2)

        # 2. target image size
        h, w = target_img.shape[:2]

        # 3. normalize swapped_face to float32 [0,1] for warping
        swapped_face_norm = swapped_face.astype(np.float32) / 255.0
        mask_norm = mask_3c.astype(np.float32)  # mask is already [0,1]

        # 4. inverse warp (WARP_INVERSE_MAP) for both face and mask
        # BORDER_CONSTANT with borderValue=0.5 (gray; avoids blue/green edge artifacts)
        warped_face = cv2.warpAffine(
            swapped_face_norm,
            M,
            (w, h),
            flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0.5
        )
        
        # for the mask (INTER_CUBIC for smooth edges)
        warped_mask = cv2.warpAffine(
            mask_norm,
            M,
            (w, h),
            flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0.0  # mask: 0 outside
        )
        
        # 5. post-warp: clip + NaN fix
        warped_face = np.clip(warped_face, 0, 1)  # drop negatives
        warped_face = np.nan_to_num(warped_face, nan=0.5)  # NaN -> gray
        
        warped_mask = np.clip(warped_mask, 0, 1)
        warped_mask = np.nan_to_num(warped_mask, nan=0.0)
        
        # 6. extra blur to suppress artifacts
        warped_mask = cv2.GaussianBlur(warped_mask, (3, 3), 0)

        # 7. smooth blend in float32
        target_float = target_img.astype(np.float32) / 255.0
        result_float = target_float * (1.0 - warped_mask) + warped_face * warped_mask
        
        # 8. back to uint8
        result = (result_float * 255).clip(0, 255).astype(np.uint8)

        return result

    def visualize_points(self, img, points, color=(0, 255, 0)):
        img = img.copy()
        for p in points:
            cv2.circle(img, tuple(p.astype(int)), 3, color, -1)

    # final entry point (get) with the affine transform
    def get(self, img, target_face, source_face, paste_back=True):
        # 1. prepare the embedding
        source_embedding = source_face.normed_embedding.reshape(1, -1).astype(np.float32)

        # 2. target 5-point landmarks
        target_landmarks_5 = self.get_landmarks_5(target_face)
        # self.visualize_points(img, target_landmarks_5, (0, 255, 0)) # not for production
        
        if target_landmarks_5 is None:
            return img if paste_back else (None, None)

        # 3. reference points for 256x256 alignment (FFHQ alignment)
        std_landmarks_256 = np.array([
            [ 84.87, 105.94],  # left eye
            [171.13, 105.94],  # right eye
            [128.00, 146.66],  # nose tip
            [ 96.95, 188.64],  # left mouth corner
            [159.05, 188.64]   # right mouth corner
        ], dtype=np.float32)

        # compute the affine matrix
        M = self.get_affine_transform(target_landmarks_5.astype(np.float32), std_landmarks_256)
        
        # warp with the new matrix M
        crop = cv2.warpAffine(img, M, (256, 256), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)

        # 4. prepare the crop for the model
        crop_input = crop[:, :, ::-1].astype(np.float32) / 255.0  # RGB -> [0,1]
        crop_input = (crop_input - 0.5) / 0.5  # normalize
        crop_input = crop_input.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)

        # 5. inference
        try:
            output = self.session.run(None, {'source': source_embedding, 'target': crop_input})[0][0]
        except:
            return img if paste_back else (None, None)

        if isinstance(output, np.ndarray):
            # fix NaN/inf
            output = np.nan_to_num(output, nan=0.0, posinf=255.0, neginf=0.0)

            # range looks like [-1,1] -> rescale to [0,255]
            if output.min() < 0.0 or output.max() <= 1.5:
                output = ((output + 1.0) / 2.0 * 255.0)
            # hard clamp + uint8 for OpenCV
            output = np.clip(output, 0, 255).astype(np.uint8).copy()

            # guard against buffer reuse (inplace CPU bug)
            try:
                output.setflags(write=True)
            except Exception:
                pass
        
        # 6. denormalize
        output = output.transpose(1, 2, 0)  # CHW -> HWC
        output = output[:, :, ::-1]  # BGR -> RGB
        
        # 7. return per the paste_back flag
        if not paste_back:
            return output, M # return only the face crop (256x256) and matrix M
        
        # full paste-back into the source image:
        return self.paste_back(img, output, M, crop_size=256)
