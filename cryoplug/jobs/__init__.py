"""Job type registry."""
from __future__ import annotations

from cryoplug.jobs.base import CATEGORIES, DATA_TYPES, JobType

REGISTRY: dict[str, type[JobType]] = {}


def register(cls: type[JobType]) -> type[JobType]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} has no name")
    if cls.name in REGISTRY:
        raise ValueError(f"Duplicate job type {cls.name}")
    if cls.category not in CATEGORIES:
        raise ValueError(f"{cls.name}: unknown category {cls.category}")
    REGISTRY[cls.name] = cls
    return cls


def get_job_type(name: str) -> type[JobType]:
    _load()
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(f"Unknown job type '{name}'") from None


def all_job_types() -> list[type[JobType]]:
    _load()
    order = {c: i for i, c in enumerate(CATEGORIES)}
    return sorted(REGISTRY.values(), key=lambda c: (order[c.category], c.title))


_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    # Importing the modules registers their job types.
    from cryoplug.jobs import (  # noqa: F401
        building,
        cryodrgn,
        deposition,
        heterogeneity,
        imports,
        interactive,
        ligands,
        localmaps,
        maptools,
        masking,
        refinement,
        series,
        sharpening,
        utility,
        validation,
    )


__all__ = ["REGISTRY", "register", "get_job_type", "all_job_types", "CATEGORIES", "DATA_TYPES", "JobType"]
