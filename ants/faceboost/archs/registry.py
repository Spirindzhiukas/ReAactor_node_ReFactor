"""Minimal arch registry (compat subset of Basicsr's).

The nodepack previously vendored all of ``r_basicsr`` (~100 files, mostly
training code) just for this registry and a logger helper. Both live here now.
"""

class Registry:
    def __init__(self, name):
        self._name = name
        self._obj_map = {}

    def _do_register(self, name, obj):
        if name in self._obj_map:
            raise ValueError(f"An object named '{name}' was already registered in '{self._name}' registry")
        self._obj_map[name] = obj

    def register(self, obj=None):
        if obj is None:
            def deco(cls_or_func):
                self._do_register(cls_or_func.__name__, cls_or_func)
                return cls_or_func
            return deco
        self._do_register(obj.__name__, obj)
        return obj

    def get(self, name):
        if name not in self._obj_map:
            raise KeyError(f"'{name}' not found in '{self._name}' registry")
        return self._obj_map[name]

    def __contains__(self, name):
        return name in self._obj_map


ARCH_REGISTRY = Registry("arch")
