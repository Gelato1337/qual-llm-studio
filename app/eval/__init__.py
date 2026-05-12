"""Eval package — devset/testset, sampling, judges, and DSPy optimizer."""

from .session import EvalSession, AgreementStats
from .storage import JudgmentStore, Judgment
from .sampling import (
    random_sample, stratified_sample, targeted_sample,
    auto_sample, assign_partitions,
)
from . import judges, optimizer

__all__ = [
    "EvalSession", "AgreementStats",
    "JudgmentStore", "Judgment",
    "random_sample", "stratified_sample", "targeted_sample",
    "auto_sample", "assign_partitions",
    "judges", "optimizer",
]
