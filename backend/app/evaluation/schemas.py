"""Typed contracts for the versioned offline evaluation dataset."""

from typing import Literal

from pydantic import BaseModel, Field

from app.persistence.models import MemoryKind, MemorySensitivity


class ExpectedMemory(BaseModel):
    keywords: list[str] = Field(min_length=1)
    kind: MemoryKind
    decision: Literal["active", "pending"] = "active"


class ExtractionCase(BaseModel):
    id: str
    category: Literal["fact", "irrelevant", "inference", "sensitive"]
    message: str
    expected: list[ExpectedMemory] = Field(default_factory=list)


class RetrievalMemory(BaseModel):
    id: str
    content: str
    embedding: list[float] | None = None
    status: Literal["active", "pending", "superseded", "deleted", "rejected"] = "active"
    owner: str = "target"


class RetrievalCase(BaseModel):
    id: str
    category: Literal["recall", "conflict", "deletion", "isolation"]
    query: str
    memories: list[RetrievalMemory]
    expected_ids: list[str] = Field(default_factory=list)
    deleted_ids: list[str] = Field(default_factory=list)
    query_embedding: list[float] | None = None
    limit: int = Field(default=3, ge=1, le=10)


class EvaluationDataset(BaseModel):
    version: str
    extraction_cases: list[ExtractionCase]
    retrieval_cases: list[RetrievalCase]


class PredictedMemory(BaseModel):
    content: str
    kind: MemoryKind
    confidence: float
    sensitivity: MemorySensitivity
    explicit: bool


class ExtractionPrediction(BaseModel):
    memories: list[PredictedMemory] = Field(default_factory=list)
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class PredictionSnapshot(BaseModel):
    dataset_version: str
    model: str
    generated_at: str
    predictions: dict[str, ExtractionPrediction]
