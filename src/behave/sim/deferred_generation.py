"""Closed, source-bound recipes for preparation inside the search budget.

Recipes are created only by the production provider builder. Command identity
freezes the recipe; generation additionally freezes the manifest it actually used.
"""

from .core.frozen import Frozen, FrozenMap

_RECIPE = object()


class DeferredGenerationProvider(Frozen):
    __slots__ = ("_bound_image", "_factory", "_prepared", "binding", "name", "policy")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("deferred generation is a closed production capability")

    def __init__(self, name, policy, binding, factory, *, _token=None):
        if _token is not _RECIPE:
            raise TypeError("use handle.generation_providers()")
        self.name, self.policy = name, policy
        self.binding = FrozenMap(binding)
        self._factory = factory
        self._bound_image = None
        self._prepared = None
        self._freeze()

    def command_image(self):
        return {"name": self.name, "policy": self.policy,
                "deferred_source": self.binding}

    def prepare(self, execution=None, baseline=()):
        return self._factory(execution=execution, baseline=baseline)

    def bind(self, provider):
        from .command import _provider_image

        image = _provider_image(provider)
        if image["name"] != self.name or image["policy"] != self.policy:
            raise ValueError("prepared guidance changed its source identity")
        object.__setattr__(self, "_bound_image", FrozenMap(image))
        object.__setattr__(self, "_prepared", provider)

    def prepared_provider(self):
        """Read a completed preparation; never start work to satisfy a lookup."""
        return self._prepared

    def prepared_image(self):
        if self._bound_image is None:
            raise ValueError("generation has not bound this source recipe")
        return self._bound_image


def deferred_provider(name, policy, binding, factory):
    return DeferredGenerationProvider(name, policy, binding, factory, _token=_RECIPE)
