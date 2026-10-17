"""
encounter/annotate/__init__.py

Annotation: tag a pull's events with phases (``phases``), mechanics, clusters, failures (``failures``) and
damage windows (``pipeline``), producing the annotated frame (``frame``), cached on disk (``cache``). The rest
of the code enters through ``annotate_pull`` and reads the ``Annotated`` result; ``unmatched_phases`` checks
the detected phases against WCL's phase transitions.
"""

from __future__ import annotations

from khadbot.encounter.annotate.cache import AnnotationCache, annotate_pull
from khadbot.encounter.annotate.frame import ANNOTATION_COLUMNS, Annotated, aggro_events, is_hit, type_is
from khadbot.encounter.annotate.phases import unmatched_phases
from khadbot.encounter.annotate.pipeline import annotate

__all__ = [
    "ANNOTATION_COLUMNS",
    "Annotated",
    "AnnotationCache",
    "aggro_events",
    "annotate",
    "annotate_pull",
    "is_hit",
    "type_is",
    "unmatched_phases",
]
