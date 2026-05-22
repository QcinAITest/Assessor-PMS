from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import uuid

from app.database import get_db
from app.models.board import (
    Board, BoardRole, FormTemplate, Parameter, EssentialCriterion,
    FrequencyRule, Webhook, FormSubmission, Assessor, Assessment, log_config_change
)
from app.models.auth import User
from app.schemas.requests import (
    BoardCreate, BoardUpdate, RoleMapping, FormTemplateCreate,
    ParameterCreate, EssentialCriterionCreate, EssentialCriterionUpdate,
    FrequencyRuleCreate, FrequencyRuleUpdate,
    WebhookCreate, WebhookUpdate
)
from app.services.scoring_engine import normalize_weights
from app.api.auth import get_current_user, require_board_access, require_system_admin

router = APIRouter(prefix="/api/v1/boards", tags=["Board Configuration"])


@router.get("")
def list_boards(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """System admins see all boards; board admins see only their own board."""
    if current_user.role == "super_admin":
        boards = db.query(Board).order_by(Board.code).all()
    else:
        boards = db.query(Board).filter(Board.id == current_user.board_id).order_by(Board.code).all()
    return [_board_summary(b) for b in boards]


@router.post("")
def create_board(
    data: BoardCreate,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    """Only system admins can create new boards."""
    if db.query(Board).filter(Board.code == data.code).first():
        raise HTTPException(400, f"Board '{data.code}' already exists")
    board = Board(id=str(uuid.uuid4()), **data.dict())
    db.add(board)
    db.commit()
    return _board_detail(db, board)


@router.get("/{board_id}")
def get_board(
    board_id: str,
    _: User = Depends(require_board_access),
    db: Session = Depends(get_db),
):
    board = _get_board(db, board_id)
    return _board_detail(db, board)


@router.delete("/{board_id}")
def delete_board(
    board_id: str,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    """Soft-delete a board (sets is_active=False). Blocked if the board has active assessors
    or open assessments to prevent orphaning live data."""
    board = _get_board(db, board_id)
    active_assessors = (
        db.query(Assessor)
        .filter(Assessor.board_id == board.id, Assessor.is_active == True)  # noqa: E712
        .count()
    )
    if active_assessors > 0:
        raise HTTPException(
            409,
            f"Cannot deactivate '{board.code}' — {active_assessors} active assessor(s) still assigned. "
            "Deactivate all assessors first, or reassign them."
        )
    open_assessments = (
        db.query(Assessment)
        .filter(
            Assessment.board_id == board.id,
            Assessment.status.in_(["IN_PROGRESS", "PENDING_FEEDBACK"]),
        )
        .count()
    )
    if open_assessments > 0:
        raise HTTPException(
            409,
            f"Cannot deactivate '{board.code}' — {open_assessments} assessment(s) are still open. "
            "Close or cancel them first."
        )
    board.is_active = False
    log_config_change(db, board.id, "BOARD_DEACTIVATED", "board", board.id,
                      {"code": board.code, "name": board.name})
    db.commit()
    return {"deactivated": True, "id": board.id, "code": board.code}


@router.put("/{board_id}")
def update_board(
    board_id: str,
    data: BoardUpdate,
    _: User = Depends(require_board_access),
    db: Session = Depends(get_db),
):
    board = _get_board(db, board_id)
    changes = data.dict(exclude_none=True)
    for k, v in changes.items():
        setattr(board, k, v)
    log_config_change(db, board.id, "BOARD_UPDATED", "board", board.id, changes)
    db.commit()
    return _board_detail(db, board)


# --- Role Mappings ---
@router.get("/{board_id}/roles")
def list_roles(board_id: str, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    board = _get_board(db, board_id)
    return [{"id": r.id, "system_role_id": r.system_role_id, "display_label": r.display_label,
             "can_be_evaluator": r.can_be_evaluator, "can_be_evaluee": r.can_be_evaluee}
            for r in board.roles]


@router.post("/{board_id}/roles")
def add_role(board_id: str, data: RoleMapping, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    board = _get_board(db, board_id)
    role = BoardRole(board_id=board.id, **data.dict())
    db.add(role)
    log_config_change(db, board.id, "ROLE_CREATED", "board_role", "new", data.dict())
    db.commit()
    return {"id": role.id, "system_role_id": role.system_role_id, "display_label": role.display_label}


@router.put("/{board_id}/roles/{role_id}")
def update_role(board_id: str, role_id: int, data: RoleMapping, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    role = db.query(BoardRole).filter(BoardRole.id == role_id, BoardRole.board_id == board_id).first()
    if not role:
        raise HTTPException(404, "Role not found")
    changes = data.dict()
    for k, v in changes.items():
        setattr(role, k, v)
    log_config_change(db, board_id, "ROLE_UPDATED", "board_role", role_id, changes)
    db.commit()
    return {"id": role.id, "system_role_id": role.system_role_id, "display_label": role.display_label}


@router.delete("/{board_id}/roles/{role_id}")
def delete_role(board_id: str, role_id: int, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    role = db.query(BoardRole).filter(BoardRole.id == role_id, BoardRole.board_id == board_id).first()
    if not role:
        raise HTTPException(404, "Role not found")
    log_config_change(db, board_id, "ROLE_DELETED", "board_role", role_id,
                      {"system_role_id": role.system_role_id, "display_label": role.display_label})
    db.delete(role)
    db.commit()
    return {"deleted": True}


# --- Form Templates ---
@router.get("/{board_id}/forms")
def list_forms(board_id: str, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    board = _get_board(db, board_id)
    return [_form_summary(f) for f in board.form_templates]


@router.post("/{board_id}/forms")
def create_form(board_id: str, data: FormTemplateCreate, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    board = _get_board(db, board_id)
    existing_total = sum(f.stakeholder_weight or 0 for f in board.form_templates)
    new_total = round(existing_total + data.stakeholder_weight, 10)
    if new_total > 1.0 + 1e-9:
        over_pct = round((new_total - 1.0) * 100, 1)
        raise HTTPException(
            422,
            f"Adding this form would push the total stakeholder weight to "
            f"{round(new_total * 100, 1)}% — over the 100% limit by {over_pct}%. "
            f"Reduce the weight of this form or adjust existing forms first."
        )
    ft = FormTemplate(id=str(uuid.uuid4()), board_id=board.id, **data.dict())
    db.add(ft)
    log_config_change(db, board.id, "FORM_CREATED", "form_template", ft.id, data.dict())
    db.commit()
    return _form_detail(ft)


@router.get("/{board_id}/forms/{form_id}")
def get_form(board_id: str, form_id: str, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    ft = db.query(FormTemplate).filter(FormTemplate.id == form_id, FormTemplate.board_id == board_id).first()
    if not ft:
        raise HTTPException(404, "Form template not found")
    return _form_detail(ft)


@router.put("/{board_id}/forms/{form_id}")
def update_form(board_id: str, form_id: str, data: FormTemplateCreate, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    ft = db.query(FormTemplate).filter(FormTemplate.id == form_id, FormTemplate.board_id == board_id).first()
    if not ft:
        raise HTTPException(404, "Form template not found")
    board = _get_board(db, ft.board_id)
    other_total = sum(f.stakeholder_weight or 0 for f in board.form_templates if f.id != form_id)
    new_total = round(other_total + data.stakeholder_weight, 10)
    if new_total > 1.0 + 1e-9:
        over_pct = round((new_total - 1.0) * 100, 1)
        raise HTTPException(
            422,
            f"This weight would push the total to {round(new_total * 100, 1)}% — "
            f"over the 100% limit by {over_pct}%. Adjust other form weights first."
        )
    changes = data.dict()
    for k, v in changes.items():
        setattr(ft, k, v)
    ft.version = (ft.version or 1) + 1   # Fix 4: increment version on every update
    log_config_change(db, board_id, "FORM_UPDATED", "form_template", form_id,
                      {**changes, "new_version": ft.version})
    db.commit()
    return _form_detail(ft)


@router.delete("/{board_id}/forms/{form_id}")
def delete_form(board_id: str, form_id: str, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    """Fix 1: Delete a form template. Blocked if real submissions exist."""
    ft = db.query(FormTemplate).filter(FormTemplate.id == form_id, FormTemplate.board_id == board_id).first()
    if not ft:
        raise HTTPException(404, "Form template not found")
    # Count submissions that were actually submitted (not just distribute-link stubs)
    submitted_count = (
        db.query(FormSubmission)
        .filter(
            FormSubmission.form_template_id == form_id,
            FormSubmission.assessment_id.isnot(None),
        )
        .count()
    )
    if submitted_count > 0:
        raise HTTPException(
            409,
            f"Cannot delete '{ft.name}' — it has {submitted_count} submission(s) attached. "
            f"Deactivate it instead by setting is_active=false."
        )
    log_config_change(db, board_id, "FORM_DELETED", "form_template", form_id,
                      {"code": ft.code, "name": ft.name})
    db.delete(ft)
    db.commit()
    return {"deleted": True}


# --- Parameters ---
@router.post("/{board_id}/forms/{form_id}/parameters")
def add_parameter(board_id: str, form_id: str, data: ParameterCreate, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    ft = db.query(FormTemplate).filter(FormTemplate.id == form_id, FormTemplate.board_id == board_id).first()
    if not ft:
        raise HTTPException(404, "Form template not found")
    if data.parent_id is None:
        existing_weight = sum(p.weight or 0 for p in ft.parameters if p.parent_id is None)
        new_total = round(existing_weight + (data.weight or 0), 10)
        if new_total > 100 + 1e-9:
            over = round(new_total - 100, 1)
            raise HTTPException(
                422,
                f"Adding this area (weight {data.weight}%) would push the total parameter weight to "
                f"{round(new_total, 1)}% — over the 100% limit by {over}%. "
                f"Reduce the weight of this area or adjust existing areas first."
            )
    param = Parameter(id=str(uuid.uuid4()), form_template_id=form_id, **data.dict())
    db.add(param)
    db.commit()
    db.expire(ft)
    top_weight_total = round(sum(p.weight or 0 for p in ft.parameters if p.parent_id is None), 10)
    return {
        "id": param.id, "code": param.code, "label": param.label, "weight": param.weight,
        "weight_total": top_weight_total,
        "weight_remaining": round(max(0, 100 - top_weight_total), 10),
    }


@router.put("/{board_id}/forms/{form_id}/parameters/{param_id}")
def update_parameter(board_id: str, form_id: str, param_id: str, data: ParameterCreate,
                     _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    param = db.query(Parameter).filter(Parameter.id == param_id, Parameter.form_template_id == form_id).first()
    if not param:
        raise HTTPException(404, "Parameter not found")
    ft = db.query(FormTemplate).filter(FormTemplate.id == form_id).first()
    if data.parent_id is None and param.parent_id is None:
        other_weight = sum(p.weight or 0 for p in ft.parameters if p.parent_id is None and p.id != param_id)
        new_total = round(other_weight + (data.weight or 0), 10)
        if new_total > 100 + 1e-9:
            over = round(new_total - 100, 1)
            raise HTTPException(
                422,
                f"This weight ({data.weight}%) would push the total to {round(new_total, 1)}% — "
                f"over the 100% limit by {over}%. Adjust other area weights first."
            )
    for k, v in data.dict().items():
        setattr(param, k, v)
    db.commit()
    return {"id": param.id, "code": param.code, "label": param.label, "weight": param.weight}


@router.delete("/{board_id}/forms/{form_id}/parameters/{param_id}")
def delete_parameter(board_id: str, form_id: str, param_id: str, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    param = db.query(Parameter).filter(Parameter.id == param_id, Parameter.form_template_id == form_id).first()
    if not param:
        raise HTTPException(404, "Parameter not found")
    db.delete(param)
    db.commit()
    return {"deleted": True}


@router.get("/{board_id}/forms/{form_id}/normalized-weights")
def get_normalized_weights(board_id: str, form_id: str, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    ft = db.query(FormTemplate).filter(FormTemplate.id == form_id, FormTemplate.board_id == board_id).first()
    if not ft:
        raise HTTPException(404, "Form template not found")
    weights = normalize_weights(ft.parameters)
    return {"form_id": form_id, "normalized_weights": weights, "sum_check": round(sum(weights.values()), 4)}


# --- Form Distribution Links ---
@router.post("/{board_id}/forms/{form_id}/generate-link")
def generate_form_link(board_id: str, form_id: str, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    """Generate a shareable public link for a form template (creates a CREATED FormSubmission)."""
    ft = db.query(FormTemplate).filter(FormTemplate.id == form_id, FormTemplate.board_id == board_id).first()
    if not ft:
        raise HTTPException(404, "Form template not found")
    token = str(uuid.uuid4())
    sub = FormSubmission(
        id=str(uuid.uuid4()),
        form_template_id=form_id,
        assessment_id=None,
        evaluator_id=None,
        evaluee_id=None,
        status="CREATED",
        responses={},
        submission_token=token,
    )
    db.add(sub)
    db.commit()
    return {"token": token, "url": f"/forms/{token}"}


# --- Essential Criteria ---
@router.post("/{board_id}/forms/{form_id}/essentials")
def add_essential(board_id: str, form_id: str, data: EssentialCriterionCreate, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    ft = db.query(FormTemplate).filter(FormTemplate.id == form_id, FormTemplate.board_id == board_id).first()
    if not ft:
        raise HTTPException(404, "Form template not found")
    ec = EssentialCriterion(id=str(uuid.uuid4()), form_template_id=form_id, **data.dict())
    db.add(ec)
    log_config_change(db, board_id, "ESSENTIAL_CREATED", "essential_criterion", ec.id, data.dict())
    db.commit()
    return {"id": ec.id, "code": ec.code, "label": ec.label}


@router.put("/{board_id}/forms/{form_id}/essentials/{ec_id}")
def update_essential(board_id: str, form_id: str, ec_id: str,
                     data: EssentialCriterionUpdate, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    """Fix 2a: Update an essential criterion."""
    ft = db.query(FormTemplate).filter(FormTemplate.id == form_id, FormTemplate.board_id == board_id).first()
    if not ft:
        raise HTTPException(404, "Form template not found")
    ec = db.query(EssentialCriterion).filter(
        EssentialCriterion.id == ec_id, EssentialCriterion.form_template_id == form_id
    ).first()
    if not ec:
        raise HTTPException(404, "Essential criterion not found")
    changes = data.dict(exclude_none=True)
    for k, v in changes.items():
        setattr(ec, k, v)
    log_config_change(db, board_id, "ESSENTIAL_UPDATED", "essential_criterion", ec_id, changes)
    db.commit()
    return {"id": ec.id, "code": ec.code, "label": ec.label, "sort_order": ec.sort_order}


@router.delete("/{board_id}/forms/{form_id}/essentials/{ec_id}")
def delete_essential(board_id: str, form_id: str, ec_id: str, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    """Fix 2b: Delete an essential criterion."""
    ft = db.query(FormTemplate).filter(FormTemplate.id == form_id, FormTemplate.board_id == board_id).first()
    if not ft:
        raise HTTPException(404, "Form template not found")
    ec = db.query(EssentialCriterion).filter(
        EssentialCriterion.id == ec_id, EssentialCriterion.form_template_id == form_id
    ).first()
    if not ec:
        raise HTTPException(404, "Essential criterion not found")
    log_config_change(db, board_id, "ESSENTIAL_DELETED", "essential_criterion", ec_id,
                      {"code": ec.code, "label": ec.label})
    db.delete(ec)
    db.commit()
    return {"deleted": True}


# --- Frequency Rules ---
@router.get("/{board_id}/frequency-rules")
def list_frequency_rules(board_id: str, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    rules = db.query(FrequencyRule).filter(FrequencyRule.board_id == board_id).all()
    return [{"id": r.id, "role_id": r.role_id, "form_template_id": r.form_template_id,
             "trigger_type": r.trigger_type, "trigger_value": r.trigger_value, "is_active": r.is_active}
            for r in rules]


@router.post("/{board_id}/frequency-rules")
def add_frequency_rule(board_id: str, data: FrequencyRuleCreate, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    _get_board(db, board_id)
    rule = FrequencyRule(board_id=board_id, **data.dict())
    db.add(rule)
    log_config_change(db, board_id, "FREQ_RULE_CREATED", "frequency_rule", "new", data.dict())
    db.commit()
    return {"id": rule.id, "trigger_type": rule.trigger_type}


@router.put("/{board_id}/frequency-rules/{rule_id}")
def update_frequency_rule(board_id: str, rule_id: int, data: FrequencyRuleUpdate,
                          _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    """Fix 5: Update trigger type / value / active status of a frequency rule."""
    rule = db.query(FrequencyRule).filter(FrequencyRule.id == rule_id, FrequencyRule.board_id == board_id).first()
    if not rule:
        raise HTTPException(404, "Rule not found")
    changes = data.dict(exclude_none=True)
    for k, v in changes.items():
        setattr(rule, k, v)
    log_config_change(db, board_id, "FREQ_RULE_UPDATED", "frequency_rule", rule_id, changes)
    db.commit()
    return {"id": rule.id, "trigger_type": rule.trigger_type,
            "trigger_value": rule.trigger_value, "is_active": rule.is_active}


@router.delete("/{board_id}/frequency-rules/{rule_id}")
def delete_frequency_rule(board_id: str, rule_id: int, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    rule = db.query(FrequencyRule).filter(FrequencyRule.id == rule_id, FrequencyRule.board_id == board_id).first()
    if not rule:
        raise HTTPException(404, "Rule not found")
    log_config_change(db, board_id, "FREQ_RULE_DELETED", "frequency_rule", rule_id,
                      {"role_id": rule.role_id, "trigger_type": rule.trigger_type})
    db.delete(rule)
    db.commit()
    return {"deleted": True}


# --- Webhooks ---
@router.get("/{board_id}/webhooks")
def list_webhooks(board_id: str, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    hooks = db.query(Webhook).filter(Webhook.board_id == board_id).all()
    return [{"id": h.id, "event_type": h.event_type, "target_url": h.target_url, "is_active": h.is_active}
            for h in hooks]


@router.post("/{board_id}/webhooks")
def add_webhook(board_id: str, data: WebhookCreate, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    _get_board(db, board_id)
    hook = Webhook(id=str(uuid.uuid4()), board_id=board_id, **data.dict())
    db.add(hook)
    log_config_change(db, board_id, "WEBHOOK_CREATED", "webhook", hook.id,
                      {"event_type": data.event_type, "target_url": data.target_url})
    db.commit()
    return {"id": hook.id, "event_type": hook.event_type, "target_url": hook.target_url}


@router.put("/{board_id}/webhooks/{hook_id}")
def update_webhook(board_id: str, hook_id: str, data: WebhookUpdate, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    """Fix 3a: Update a webhook URL, event type, or active flag."""
    hook = db.query(Webhook).filter(Webhook.id == hook_id, Webhook.board_id == board_id).first()
    if not hook:
        raise HTTPException(404, "Webhook not found")
    changes = data.dict(exclude_none=True)
    for k, v in changes.items():
        setattr(hook, k, v)
    log_config_change(db, board_id, "WEBHOOK_UPDATED", "webhook", hook_id, changes)
    db.commit()
    return {"id": hook.id, "event_type": hook.event_type, "target_url": hook.target_url,
            "is_active": hook.is_active}


@router.delete("/{board_id}/webhooks/{hook_id}")
def delete_webhook(board_id: str, hook_id: str, _: User = Depends(require_board_access), db: Session = Depends(get_db)):
    """Fix 3b: Delete a webhook."""
    hook = db.query(Webhook).filter(Webhook.id == hook_id, Webhook.board_id == board_id).first()
    if not hook:
        raise HTTPException(404, "Webhook not found")
    log_config_change(db, board_id, "WEBHOOK_DELETED", "webhook", hook_id,
                      {"event_type": hook.event_type, "target_url": hook.target_url})
    db.delete(hook)
    db.commit()
    return {"deleted": True}


# --- Helpers ---
def _get_board(db: Session, board_id: str) -> Board:
    board = db.query(Board).filter((Board.id == board_id) | (Board.code == board_id)).first()
    if not board:
        raise HTTPException(404, f"Board '{board_id}' not found")
    return board


def _board_summary(b: Board):
    return {
        "id": b.id, "code": b.code, "name": b.name, "is_active": b.is_active,
        "rating_engine": b.rating_engine,
        "forms_count": len(b.form_templates),
        "roles_count": len(b.roles),
    }


def _board_detail(db: Session, b: Board):
    return {
        "id": b.id, "code": b.code, "name": b.name, "description": b.description,
        "logo_url": b.logo_url, "is_active": b.is_active, "config": b.config,
        "roles": [{"id": r.id, "system_role_id": r.system_role_id, "display_label": r.display_label,
                    "can_be_evaluator": r.can_be_evaluator, "can_be_evaluee": r.can_be_evaluee}
                   for r in b.roles],
        "form_templates": [_form_summary(f) for f in b.form_templates],
        "frequency_rules": [{"id": r.id, "role_id": r.role_id, "trigger_type": r.trigger_type,
                              "trigger_value": r.trigger_value, "is_active": r.is_active,
                              "form_template_id": r.form_template_id}
                            for r in b.frequency_rules],
    }


def _form_summary(f: FormTemplate):
    return {
        "id": f.id, "code": f.code, "name": f.name,
        "stakeholder_weight": f.stakeholder_weight,
        "is_mandatory": f.is_mandatory, "is_active": f.is_active,
        "parameters_count": len(f.parameters),
        "version": f.version,
    }


def _form_detail(f: FormTemplate):
    return {
        "id": f.id, "code": f.code, "name": f.name, "description": f.description,
        "stakeholder_weight": f.stakeholder_weight,
        "target_evaluator_role": f.target_evaluator_role,
        "target_evaluee_roles": f.target_evaluee_roles,
        "is_mandatory": f.is_mandatory, "is_active": f.is_active,
        "version": f.version,
        "parameters": [_param_tree(p) for p in f.parameters if p.is_top_level],
        "essential_criteria": [{"id": e.id, "code": e.code, "label": e.label,
                                 "sort_order": e.sort_order}
                               for e in f.essential_criteria],
    }


def _param_tree(p: Parameter):
    return {
        "id": p.id, "code": p.code, "label": p.label, "weight": p.weight,
        "data_type": p.data_type, "is_mandatory": p.is_mandatory,
        "children": [_param_tree(c) for c in (p.children or [])],
    }
