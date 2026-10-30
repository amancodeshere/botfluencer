"""Strict real-holdout attestation; synthetic or legacy reports cannot opt in."""

import math
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)


def wilson(successes: int, total: int) -> tuple[float, float]:
    if total == 0:
        return 0.0, 1.0
    z = 1.96
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    radius = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return max(0.0, center - radius), min(1.0, center + radius)


class RealValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal["blue-real-v2"]
    model_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    threshold: float = Field(strict=True, ge=0.5, le=1)
    training_provenance_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    real_labels_only: StrictBool
    author_disjoint: StrictBool
    own_positive_sources_excluded: StrictBool
    label_source_counts: dict[str, StrictInt]
    positive_author_count: StrictInt = Field(ge=1)
    negative_author_count: StrictInt = Field(ge=1)
    positive_count: StrictInt = Field(ge=0)
    negative_count: StrictInt = Field(ge=0)
    true_positives: StrictInt = Field(ge=0)
    false_positives: StrictInt = Field(ge=0)
    precision: float = Field(strict=True, ge=0, le=1)
    recall: float = Field(strict=True, ge=0, le=1)
    precision_lower_95: float = Field(strict=True, ge=0, le=1)
    fpr_upper_95: float = Field(strict=True, ge=0, le=1)

    @field_validator("real_labels_only", "author_disjoint", "own_positive_sources_excluded")
    @classmethod
    def independently_reviewed(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Independent real labels and own-source exclusions are required")
        return value

    @model_validator(mode="after")
    def consistent_evaluation(self) -> "RealValidation":
        if (
            self.true_positives > self.positive_count
            or self.false_positives > self.negative_count
            or self.positive_author_count > self.positive_count
            or self.negative_author_count > self.negative_count
            or not self.label_source_counts
            or not self.label_source_counts.keys() <= {"team_review", "moderator"}
            or any(count < 0 for count in self.label_source_counts.values())
            or sum(self.label_source_counts.values()) != self.positive_count + self.negative_count
        ):
            raise ValueError("Real evaluation counts or provenance are inconsistent")
        predicted = self.true_positives + self.false_positives
        expected = (
            self.true_positives / predicted if predicted else 0.0,
            self.true_positives / self.positive_count if self.positive_count else 0.0,
            wilson(self.true_positives, predicted)[0],
            wilson(self.false_positives, self.negative_count)[1],
        )
        observed = (self.precision, self.recall, self.precision_lower_95, self.fpr_upper_95)
        if any(
            not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
            for a, b in zip(observed, expected, strict=True)
        ):
            raise ValueError("Real evaluation metrics do not match their confusion counts")
        return self

    def permits_automatic_reporting(self) -> bool:
        return (
            self.threshold < 1
            and self.positive_count >= 100
            and self.negative_count >= 400
            and self.precision_lower_95 >= 0.95
            and self.fpr_upper_95 <= 0.01
        )
