"""Disabled Python-analysis boundary from the future system flow."""

from __future__ import annotations


class PythonGenerationNotConfigured(RuntimeError):
    """Raised until governed Python generation and execution are designed."""


def generate_python(*_args: object, **_kwargs: object) -> None:
    raise PythonGenerationNotConfigured("Python generation is not configured.")
