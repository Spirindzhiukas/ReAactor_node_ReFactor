import os

import numpy as np


class Face(dict):
    """
    Face data container.

    Inherits from dict for full compatibility with code that expects key
    access (e.g. ``face['bbox']``), while also exposing attributes.
    """

    def __init__(self, d=None, **kwargs):
        if d is None:
            d = {}
        if kwargs:
            d.update(**kwargs)
        for k, v in d.items():
            setattr(self, k, v)
        super().__init__(d)

    def __setattr__(self, name, value):
        # Copy arrays/lists to avoid accidental mutation-by-reference bugs.
        if isinstance(value, (list, tuple)):
            value = [x for x in value]
        elif isinstance(value, np.ndarray):
            value = value.copy()

        super().__setattr__(name, value)
        super().__setitem__(name, value)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        super().__setattr__(key, value)

    @property
    def sex(self):
        """'M' or 'F' based on the numeric gender attribute."""
        gender = self.get("gender", None)
        if gender is None:
            return None
        return "M" if gender == 1 else "F"

    @property
    def normed_embedding(self):
        """L2-normalized embedding, computed on demand (ArcFace / INSwapper)."""
        embedding = self.get("embedding", None)
        if embedding is None:
            return None
        norm = np.linalg.norm(embedding)
        if norm == 0:
            return embedding
        return embedding / norm


class BaseONNXModel:
    """Base class for all ONNX face models (detector, embedder, swapper...).

    onnxruntime is imported lazily here: the nodepack imports cleanly in
    environments where ORT is missing, and only the nodes that actually run
    models surface a clear installation hint.
    """

    def __init__(self, model_file, providers=None):
        from ..ort_utils import create_session

        self.model_file = model_file
        self.providers = providers
        if not os.path.exists(self.model_file):
            raise FileNotFoundError(f"Model file not found: {self.model_file}")

        self.session = create_session(self.model_file, providers=providers)

        self.inputs = self.session.get_inputs()
        self.input_names = [inp.name for inp in self.inputs]
        self.input_shape = self.inputs[0].shape
        self.outputs = self.session.get_outputs()
        self.output_names = [out.name for out in self.outputs]

    def forward(self, *args, **kwargs):
        raise NotImplementedError("Forward method must be implemented by subclasses.")
