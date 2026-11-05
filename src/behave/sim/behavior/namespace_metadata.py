"""Trusted annotation implementation objects, captured before candidate loading."""

import abc
import dataclasses
import functools
import types
import typing


def public_module_exports():
    """Freeze the registered source API, not recursively imported host modules."""
    return {module: frozenset((*module.__all__, '__all__'))
            for module in (dataclasses, typing)}


def abc_registry(klass, value):
    """ABC registration affects semantics; derived lookup caches do not."""
    if not isinstance(klass, abc.ABCMeta):
        return None
    import _abc

    registered, _positive, _negative, _version = _abc._get_dump(klass)
    return ('abc-registry', id(value), frozenset(id(reference()) for reference in registered))


def annotation_caches():
    """The standard typing library's memoizers, not candidate-defined caches.

    Their internal memo tables belong to Python's annotation implementation;
    their exposed function, defaults, closure and attributes remain imaged.
    A user callable's hidden native cache is not an admitted source-state image.
    """
    pending = list(vars(typing).values())
    seen = set()
    caches = set()
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if type(value) is functools._lru_cache_wrapper:
            caches.add(value)
            pending.append(value.__wrapped__)
        elif type(value) is types.FunctionType:
            pending.extend(cell.cell_contents for cell in (value.__closure__ or ()))
        elif isinstance(value, type) and value.__module__ == 'typing':
            pending.extend(vars(value).values())
        elif type(value) in (staticmethod, classmethod):
            pending.append(value.__func__)
        elif type(value).__module__ == 'typing':
            # Special forms such as Literal retain their memoizer in instance
            # slots, rather than in a class method's closure.
            for base in type(value).__mro__:
                for name, descriptor in vars(base).items():
                    if type(descriptor) is types.MemberDescriptorType:
                        try:
                            pending.append(descriptor.__get__(value, type(value)))
                        except AttributeError:
                            pass
                    elif name == '__dict__' and type(descriptor) is types.GetSetDescriptorType:
                        pending.extend(descriptor.__get__(value, type(value)).values())
    return frozenset(caches)
