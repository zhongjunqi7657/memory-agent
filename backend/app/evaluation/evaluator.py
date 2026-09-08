"""Deterministic metrics over recorded extraction output and production ranking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.evaluation.schemas import EvaluationDataset, PredictionSnapshot
from app.memory.policy import MemoryCandidate, MemoryDecision, assess_candidate
from app.memory.retrieval import rank_memories


def _round_ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _matches(content: str, kind: str, decision: str, expected) -> bool:
    normalized = content.casefold()
    return (
        kind == expected.kind.value
        and decision == expected.decision
        and all(keyword.casefold() in normalized for keyword in expected.keywords)
    )


def evaluate_extraction(
    dataset: EvaluationDataset, snapshot: PredictionSnapshot
) -> tuple[dict[str, float | int | None], list[dict[str, object]]]:
    true_positive = 0
    predicted_total = 0
    expected_total = 0
    correct_cases = 0
    failures: list[dict[str, object]] = []
    latencies: list[float] = []
    input_tokens = 0
    output_tokens = 0

    for case in dataset.extraction_cases:
        prediction = snapshot.predictions.get(case.id)
        raw_predictions = prediction.memories if prediction else []
        predicted = []
        for item in raw_predictions:
            assessment = assess_candidate(
                MemoryCandidate(
                    content=item.content,
                    kind=item.kind,
                    confidence=item.confidence,
                    sensitivity=item.sensitivity,
                    explicit=item.explicit,
                )
            )
            if assessment.decision is not MemoryDecision.REJECT:
                predicted.append(
                    SimpleNamespace(
                        content=assessment.content,
                        kind=item.kind,
                        decision=assessment.decision.value,
                        source=item,
                    )
                )
        unmatched = set(range(len(predicted)))
        matched = 0
        for expected in case.expected:
            prediction_index = next(
                (
                    index
                    for index in unmatched
                    if _matches(
                        predicted[index].content,
                        predicted[index].kind.value,
                        predicted[index].decision,
                        expected,
                    )
                ),
                None,
            )
            if prediction_index is not None:
                unmatched.remove(prediction_index)
                matched += 1

        true_positive += matched
        predicted_total += len(predicted)
        expected_total += len(case.expected)
        is_correct = matched == len(case.expected) and len(predicted) == len(case.expected)
        correct_cases += int(is_correct)
        if not is_correct:
            failures.append(
                {
                    "case_id": case.id,
                    "category": case.category,
                    "expected": [item.model_dump(mode="json") for item in case.expected],
                    "actual": [
                        {
                            **item.source.model_dump(mode="json"),
                            "decision": item.decision,
                        }
                        for item in predicted
                    ],
                }
            )
        if prediction and prediction.latency_ms is not None:
            latencies.append(prediction.latency_ms)
        if prediction:
            input_tokens += prediction.input_tokens or 0
            output_tokens += prediction.output_tokens or 0

    precision = _round_ratio(true_positive, predicted_total)
    recall = _round_ratio(true_positive, expected_total)
    return (
        {
            "case_accuracy": _round_ratio(correct_cases, len(dataset.extraction_cases)),
            "precision": precision,
            "recall": recall,
            "f1": round(2 * precision * recall / (precision + recall), 4)
            if precision + recall
            else 0.0,
            "average_latency_ms": round(sum(latencies) / len(latencies), 2)
            if latencies
            else None,
            "input_tokens": input_tokens or None,
            "output_tokens": output_tokens or None,
        },
        failures,
    )


def evaluate_retrieval(
    dataset: EvaluationDataset,
) -> tuple[dict[str, float | int], list[dict[str, object]]]:
    recall_true_positive = 0
    recall_predicted = 0
    recall_expected = 0
    conflict_hits = 0
    conflict_expected = 0
    deleted_retrieved = 0
    deleted_relevant = 0
    isolation_leaks = 0
    isolation_results = 0
    embedding_fallback_cases = 0
    failures: list[dict[str, object]] = []
    now = datetime.now(timezone.utc)

    for case in dataset.retrieval_cases:
        embedding_fallback_cases += int(case.query_embedding is None)
        eligible = [
            item
            for item in case.memories
            if item.owner == "target" and item.status == "active"
        ]
        ranked = rank_memories(
            [
                SimpleNamespace(
                    id=item.id,
                    content=item.content,
                    embedding=item.embedding,
                    updated_at=now - timedelta(seconds=index),
                )
                for index, item in enumerate(eligible)
            ],
            case.query,
            query_embedding=case.query_embedding,
            limit=case.limit,
        )
        actual_ids = [str(item.memory.id) for item in ranked]
        expected = set(case.expected_ids)
        actual = set(actual_ids)

        if case.category in {"recall", "isolation"}:
            recall_true_positive += len(expected & actual)
            recall_predicted += len(actual)
            recall_expected += len(expected)
        if case.category == "conflict":
            conflict_hits += len(expected & actual)
            conflict_expected += len(expected)
        if case.category == "deletion":
            deleted_ids = set(case.deleted_ids)
            deleted_relevant += len(deleted_ids)
            deleted_retrieved += len(deleted_ids & actual)
        if case.category == "isolation":
            foreign_ids = {item.id for item in case.memories if item.owner != "target"}
            isolation_leaks += len(foreign_ids & actual)
            isolation_results += len(actual)

        if actual_ids != case.expected_ids:
            failures.append(
                {
                    "case_id": case.id,
                    "category": case.category,
                    "expected": case.expected_ids,
                    "actual": actual_ids,
                }
            )

    return (
        {
            "precision": _round_ratio(recall_true_positive, recall_predicted),
            "recall": _round_ratio(recall_true_positive, recall_expected),
            "conflict_interception_rate": _round_ratio(
                conflict_hits, conflict_expected
            ),
            "deletion_residual_rate": _round_ratio(
                deleted_retrieved, deleted_relevant
            ),
            "cross_user_leak_rate": _round_ratio(isolation_leaks, isolation_results),
            "embedding_fallback_ratio": _round_ratio(
                embedding_fallback_cases, len(dataset.retrieval_cases)
            ),
        },
        failures,
    )


def evaluate(
    dataset: EvaluationDataset, snapshot: PredictionSnapshot
) -> dict[str, object]:
    if snapshot.dataset_version != dataset.version:
        raise ValueError("预测快照与评测集版本不一致")
    extraction_metrics, extraction_failures = evaluate_extraction(dataset, snapshot)
    retrieval_metrics, retrieval_failures = evaluate_retrieval(dataset)
    failures = extraction_failures + retrieval_failures
    return {
        "dataset_version": dataset.version,
        "sample_count": len(dataset.extraction_cases) + len(dataset.retrieval_cases),
        "extraction": extraction_metrics,
        "retrieval": retrieval_metrics,
        "known_failure_count": len(failures),
        "known_failures": failures,
    }
