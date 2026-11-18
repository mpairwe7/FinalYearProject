"""URA Tax Assistant — production data augmentation pipeline (2026).

Four-stage pipeline:
    ingest → normalize → quality → format

Every stage is pure, testable, and emits a manifest fragment so the final
dataset is reproducible from a single config + commit SHA.

Public entry points:
    from ml.scripts.data_aug.pipeline import run_pipeline, PipelineConfig
    from ml.scripts.data_aug.schema import TrainingExample, Message
"""

from ml.scripts.data_aug.schema import (
    URA_SYSTEM_PROMPT,
    Message,
    SourceType,
    TrainingExample,
)

__all__ = [
    "URA_SYSTEM_PROMPT",
    "Message",
    "SourceType",
    "TrainingExample",
]
