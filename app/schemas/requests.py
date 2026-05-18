from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class BoardCreate(BaseModel):
    code: str = Field(..., max_length=20)
    name: str = Field(..., max_length=200)
    description: Optional[str] = None
    logo_url: Optional[str] = None
    config: dict


class BoardUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    config: Optional[dict] = None
    is_active: Optional[bool] = None


class RoleMapping(BaseModel):
    system_role_id: str
    display_label: str
    description: Optional[str] = None
    can_be_evaluator: bool = False
    can_be_evaluee: bool = True


class FormTemplateCreate(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    stakeholder_weight: float = Field(..., ge=0, le=1)
    target_evaluator_role: Optional[str] = None
    target_evaluee_roles: Optional[List[str]] = None
    is_mandatory: bool = True


class ParameterCreate(BaseModel):
    code: str
    label: str
    description: Optional[str] = None
    weight: float = Field(0, ge=0)
    data_type: str = "RATING_1_5"
    options: Optional[Any] = None
    is_mandatory: bool = True
    parent_id: Optional[str] = None
    sort_order: int = 0


class EssentialCriterionCreate(BaseModel):
    code: str
    label: str
    sort_order: int = 0


class FrequencyRuleCreate(BaseModel):
    role_id: str
    form_template_id: str
    trigger_type: str
    trigger_value: Optional[int] = None


class AssessorCreate(BaseModel):
    employee_id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role_id: str


class AssessorUpdate(BaseModel):
    name: Optional[str] = None
    employee_id: Optional[str] = None
    email: Optional[str] = None
    role_id: Optional[str] = None
    is_active: Optional[bool] = None


class AssessmentCreate(BaseModel):
    assessment_type: str
    organization_name: Optional[str] = None
    scheme: Optional[str] = None
    standard_version: Optional[str] = None
    assessment_date: datetime


class SubmissionCreate(BaseModel):
    form_template_id: str
    evaluator_id: str
    evaluee_id: str
    responses: Dict[str, Any]
    comments: Optional[str] = None


class TriggerAssessmentComplete(BaseModel):
    assessment_id: str
    evaluee_ids: List[str]
    source_portal: Optional[str] = None


class WebhookCreate(BaseModel):
    event_type: str
    target_url: str
    secret: Optional[str] = None


class WebhookUpdate(BaseModel):
    event_type: Optional[str] = None
    target_url: Optional[str] = None
    secret: Optional[str] = None
    is_active: Optional[bool] = None


class EssentialCriterionUpdate(BaseModel):
    label: Optional[str] = None
    code: Optional[str] = None
    sort_order: Optional[int] = None


class FrequencyRuleUpdate(BaseModel):
    trigger_type: Optional[str] = None
    trigger_value: Optional[int] = None
    is_active: Optional[bool] = None


# --- Sync schemas ---

class AssessorSyncItem(BaseModel):
    employee_id: str
    name: str
    email: Optional[str] = None
    role_id: str
    is_active: bool = True


class AssessorSyncRequest(BaseModel):
    portal_id: Optional[str] = None          # used to look up PortalAdapter.role_map
    deactivate_missing: bool = False          # soft-delete assessors absent from payload
    assessors: List[AssessorSyncItem]


class UserSyncItem(BaseModel):
    email: str
    full_name: str
    role: str = "board_admin"
    board_code: Optional[str] = None
    external_id: Optional[str] = None


class UserSyncRequest(BaseModel):
    users: List[UserSyncItem]
