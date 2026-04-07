"""
Board-agnostic scoring engine.

Handles both Numeric (1-5 → Star) and Percentage-based (>80% → 5★) rating logic
by reading the board's `config.rating_engine` and `config.star_bands`.

No board-specific if/else — everything is data-driven from the Board Profile JSON.
"""
from typing import Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from app.models.board import (
    Board, FormTemplate, Parameter, EssentialCriterion,
    FormSubmission, AuditScore, CumulativeRating, Assessment
)
import uuid
from datetime import datetime


def normalize_weights(parameters: List[Parameter]) -> Dict[str, float]:
    """
    Auto-normalise: ensures weights of all top-level parameters sum to 100.
    Returns { param_code: normalised_weight_as_fraction }.
    """
    top_level = [p for p in parameters if p.is_top_level and p.data_type == "CALCULATED"]
    raw_sum = sum(p.weight for p in top_level)
    if raw_sum == 0:
        n = len(top_level) or 1
        return {p.code: 1.0 / n for p in top_level}
    return {p.code: p.weight / raw_sum for p in top_level}


def calculate_parameter_score(parameter: Parameter, responses: dict) -> Optional[float]:
    """
    Calculate score for a single parameter.
    - Top-level (CALCULATED): average of child sub-parameter ratings.
    - Sub-parameter (RATING_1_5): direct numeric value from response.
    - Sub-parameter (YES_NO): 5.0 for YES, 1.0 for NO.
    - Sub-parameter (PERCENTAGE): value / 100 * 5 (normalised to 1-5 scale).
    """
    if parameter.data_type == "CALCULATED":
        child_scores = []
        for child in parameter.children:
            score = calculate_parameter_score(child, responses)
            if score is not None:
                child_scores.append(score)
        return sum(child_scores) / len(child_scores) if child_scores else None

    value = responses.get(parameter.code)
    if value is None:
        return None

    if parameter.data_type == "RATING_1_5":
        return float(value)
    elif parameter.data_type == "YES_NO":
        return 5.0 if str(value).upper() == "YES" else 1.0
    elif parameter.data_type == "PERCENTAGE":
        return min(5.0, max(1.0, float(value) / 20.0))
    elif parameter.data_type == "DROPDOWN":
        options = parameter.options or []
        if isinstance(options, list) and value in options:
            return float(options.index(value) + 1) * (5.0 / max(len(options), 1))
        return None
    return None


def calculate_form_score(form_template: FormTemplate, responses: dict) -> Tuple[float, bool]:
    """
    Compute individual form score = Σ(parameter_score × normalised_weight).
    Returns (score, essential_flag).
    """
    weights = normalize_weights(form_template.parameters)
    weighted_sum = 0.0
    total_weight_used = 0.0

    for param in form_template.parameters:
        if not param.is_top_level or param.data_type != "CALCULATED":
            continue
        score = calculate_parameter_score(param, responses)
        if score is not None:
            w = weights.get(param.code, 0)
            weighted_sum += score * w
            total_weight_used += w

    form_score = weighted_sum / total_weight_used if total_weight_used > 0 else 0.0

    essential_flag = False
    for ec in form_template.essential_criteria:
        val = responses.get(ec.code, "YES")
        if str(val).upper() == "NO":
            essential_flag = True
            break

    return round(form_score, 4), essential_flag


def score_to_star_numeric(score: float, star_bands: list) -> int:
    """
    Numeric rating engine: score is 1.0-5.0, map to star via bands.
    star_bands example: [
        {"min": 4.5, "max": 5.0, "stars": 5},
        {"min": 4.0, "max": 4.49, "stars": 4}, ...
    ]
    """
    for band in sorted(star_bands, key=lambda b: -b["min"]):
        if score >= band["min"]:
            return band["stars"]
    return 1


def score_to_star_percentage(score: float, star_bands: list) -> int:
    """
    Percentage rating engine: score is 0-100%, map to star via bands.
    star_bands example: [
        {"min_pct": 80, "stars": 5},
        {"min_pct": 65, "stars": 4}, ...
    ]
    """
    pct = (score / 5.0) * 100 if score <= 5.0 else score
    for band in sorted(star_bands, key=lambda b: -b.get("min_pct", b.get("min", 0))):
        threshold = band.get("min_pct", band.get("min", 0))
        if pct >= threshold:
            return band["stars"]
    return 1


def normalize_to_base100(score: float, rating_engine: str = "numeric") -> float:
    """
    Unified normalization step: converts any internal score to a 0-100 base.
    - numeric engine: input is 1.0-5.0  → (score - 1) / 4 * 100
    - percentage engine: input is 0-100 → returned as-is
    This base-100 value is used for cross-board comparison and display.
    The raw 1-5 score is preserved separately for board-specific star mapping.
    """
    if rating_engine == "percentage":
        return round(min(100.0, max(0.0, float(score))), 2)
    # numeric: map [1, 5] → [0, 100]
    return round(min(100.0, max(0.0, (float(score) - 1.0) / 4.0 * 100.0)), 2)


def get_star_rating(board: Board, score: float) -> int:
    """Dispatch to the correct star-mapping logic based on board config."""
    engine = board.rating_engine
    bands = board.star_bands
    if not bands:
        bands = [
            {"min": 4.5, "stars": 5}, {"min": 4.0, "stars": 4},
            {"min": 3.5, "stars": 3}, {"min": 3.0, "stars": 2}, {"min": 0, "stars": 1}
        ]
    if engine == "percentage":
        return score_to_star_percentage(score, bands)
    return score_to_star_numeric(score, bands)


def calculate_final_audit_score(
    db: Session,
    assessment_id: str,
    evaluee_id: str,
    board: Board,
) -> AuditScore:
    """
    Aggregate all form submissions for one evaluee in one assessment.
    Final Audit Score = Σ(form_score × stakeholder_weight).
    """
    submissions = (
        db.query(FormSubmission)
        .filter(
            FormSubmission.assessment_id == assessment_id,
            FormSubmission.evaluee_id == evaluee_id,
            FormSubmission.status == "SUBMITTED",
        )
        .all()
    )

    form_scores_map = {}
    total_weighted = 0.0
    total_weight = 0.0
    any_essential_flag = False

    for sub in submissions:
        ft = sub.form_template
        w = ft.stakeholder_weight
        if sub.form_score is not None:
            form_scores_map[ft.code] = {"score": sub.form_score, "weight": w}
            total_weighted += sub.form_score * w
            total_weight += w
        if sub.essential_flag:
            any_essential_flag = True

    final = total_weighted / total_weight if total_weight > 0 else 0.0
    base_100 = normalize_to_base100(final, board.rating_engine)
    stars = get_star_rating(board, final)

    existing = (
        db.query(AuditScore)
        .filter(AuditScore.assessment_id == assessment_id, AuditScore.evaluee_id == evaluee_id)
        .first()
    )
    if existing:
        existing.form_scores = form_scores_map
        existing.final_score = round(final, 4)
        existing.base_100_score = base_100
        existing.star_rating = stars
        existing.essential_flag = any_essential_flag
        existing.calculated_at = datetime.utcnow()
        db.flush()
        return existing

    audit_score = AuditScore(
        id=str(uuid.uuid4()),
        assessment_id=assessment_id,
        evaluee_id=evaluee_id,
        board_id=board.id,
        form_scores=form_scores_map,
        final_score=round(final, 4),
        base_100_score=base_100,
        star_rating=stars,
        essential_flag=any_essential_flag,
    )
    db.add(audit_score)
    db.flush()
    return audit_score


def calculate_cumulative_rating(
    db: Session,
    evaluee_id: str,
    board: Board,
) -> CumulativeRating:
    """
    Cumulative rating = average of the last N audit scores,
    where N comes from board config (default 10).
    """
    window = board.config.get("cumulative_window", 10)

    recent_scores = (
        db.query(AuditScore)
        .filter(AuditScore.evaluee_id == evaluee_id, AuditScore.board_id == board.id)
        .order_by(AuditScore.calculated_at.desc())
        .limit(window)
        .all()
    )

    if not recent_scores:
        return None

    avg = sum(s.final_score for s in recent_scores) / len(recent_scores)
    stars = get_star_rating(board, avg)
    has_flags = any(s.essential_flag for s in recent_scores)

    existing = (
        db.query(CumulativeRating)
        .filter(CumulativeRating.evaluee_id == evaluee_id, CumulativeRating.board_id == board.id)
        .first()
    )
    if existing:
        existing.window_size = len(recent_scores)
        existing.audit_scores_used = [s.id for s in recent_scores]
        existing.cumulative_score = round(avg, 4)
        existing.star_rating = stars
        existing.has_essential_flags = has_flags
        existing.updated_at = datetime.utcnow()
        db.flush()
        return existing

    cr = CumulativeRating(
        id=str(uuid.uuid4()),
        evaluee_id=evaluee_id,
        board_id=board.id,
        window_size=len(recent_scores),
        audit_scores_used=[s.id for s in recent_scores],
        cumulative_score=round(avg, 4),
        star_rating=stars,
        has_essential_flags=has_flags,
    )
    db.add(cr)
    db.flush()
    return cr
