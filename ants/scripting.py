"""Minimal scripting shims.

Replaces the upstream ``r_modules`` sd-webui compatibility layer (which the
nodepack carried for its vestigial script integration). Only the pieces that
are actually used are kept, with zero imports of their own.
"""


class State:
    interrupted = False

    def begin(self):
        pass

    def end(self):
        pass


state = State()


class Processing:
    def __init__(self, init_imgs):
        self.init_images = init_imgs
        self.width = init_imgs[0].width
        self.height = init_imgs[0].height
        self.extra_generation_params = {}
        self.bbox = []
        self.swapped_indexes = []


class ProcessingImg2Img(Processing):
    def __init__(self, init_img):
        super().__init__(init_img)
