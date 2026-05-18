"""
Seeds the database with 4 board profiles (NABL, NABH, NABCB, NABET)
derived from actual QCI feedback documents.
"""
import uuid
from app.database import SessionLocal, engine, Base
from app.models.board import (
    Board, BoardRole, FormTemplate, Parameter, EssentialCriterion, FrequencyRule
)
from app.models.auth import User
from app.models.program import ServiceLine, Program
from app.services.auth_service import hash_password


def uid():
    return str(uuid.uuid4())


ESSENTIALS = [
    ("ESS_ETHICS", "Professional Ethics (Must be YES)"),
    ("ESS_CONFIDENTIALITY", "Confidentiality Respected (Must be YES)"),
    ("ESS_IMPARTIALITY", "Impartiality Demonstrated (Must be YES)"),
    ("ESS_INTEGRITY", "Integrity Maintained (Must be YES)"),
    ("ESS_CONDUCT", "Full Professional Conduct (Must be YES)"),
]


def add_essentials(db, form_id):
    for i, (code, label) in enumerate(ESSENTIALS):
        db.add(EssentialCriterion(id=uid(), form_template_id=form_id, code=code, label=label, sort_order=i))


def add_competency_params(db, form_id, competencies):
    """
    competencies: list of (code, label, weight, [sub_labels])
    """
    for sort_i, (code, label, weight, subs) in enumerate(competencies):
        parent_id = uid()
        db.add(Parameter(
            id=parent_id, form_template_id=form_id, code=code, label=label,
            weight=weight, data_type="CALCULATED", sort_order=sort_i
        ))
        for j, sub_label in enumerate(subs):
            db.add(Parameter(
                id=uid(), form_template_id=form_id, parent_id=parent_id,
                code=f"{code}_Sub{j+1}", label=sub_label,
                weight=0, data_type="RATING_1_5", sort_order=j
            ))


def seed_nabl(db):
    board_id = uid()
    db.add(Board(
        id=board_id, code="NABL", name="National Accreditation Board for Testing and Calibration Laboratories",
        description="Accreditation of testing, calibration, medical and proficiency testing laboratories",
        config={
            "rating_engine": "numeric",
            "star_bands": [
                {"min": 4.5, "max": 5.0, "stars": 5},
                {"min": 4.0, "max": 4.49, "stars": 4},
                {"min": 3.5, "max": 3.99, "stars": 3},
                {"min": 3.0, "max": 3.49, "stars": 2},
                {"min": 0, "max": 2.99, "stars": 1},
            ],
            "stakeholder_weights": {
                "CLIENT_CAB": 0.15, "LEAD_ASSESSOR": 0.25,
                "PEER_ASSESSOR": 0.15, "DEALING_OFFICER": 0.20,
                "ACCREDITATION_COMMITTEE": 0.25,
            },
            "cumulative_window": 10,
            "terminology": {
                "evaluator": "Peer Assessor",
                "assessment": "Assessment",
                "organization": "Laboratory / CAB",
            },
            "vocabulary_map": {
                "assessor": "Technical Expert / Assessor",
                "audit": "Assessment",
                "finding": "Non-Conformity (NC)",
                "certificate": "Accreditation Certificate",
                "client": "CAB (Conformity Assessment Body)",
            },
        }
    ))

    # NABL Roles (from NABL response — merged Lead/Peer into "Peer Assessor")
    for rid, label, evaluator, evaluee in [
        ("ROLE_LEAD", "Lead Assessor", True, True),
        ("ROLE_PEER", "Peer Assessor", True, True),
        ("ROLE_TE", "Technical Expert", False, True),
        ("ROLE_OBSERVER", "Observer", False, True),
        ("ROLE_OFFICER", "Dealing Officer", True, False),
        ("ROLE_COMMITTEE", "Accreditation Committee Member", True, False),
    ]:
        db.add(BoardRole(board_id=board_id, system_role_id=rid, display_label=label,
                         can_be_evaluator=evaluator, can_be_evaluee=evaluee))

    # NABL competency structure from NABL response spreadsheet
    nabl_competencies = [
        ("C1", "Knowledge of Accreditation Standard", 25, [
            "Understanding of ISO/IEC 17025 / 15189 requirements",
            "Knowledge of NABL policies and specific criteria",
            "Awareness of sector-specific standards and test methods",
            "Knowledge of regulatory requirements applicable to the scope",
        ]),
        ("C2", "Knowledge of NABL Policies/Standards", 25, [
            "Familiarity with NABL accreditation process and procedures",
            "Understanding of NABL specific criteria documents",
            "Knowledge of mandatory documents and guidance notes",
            "Awareness of policy updates and revisions",
        ]),
        ("C3", "Communication and Interview Skill", 15, [
            "Clarity in verbal communication during assessment",
            "Effective interviewing techniques with laboratory staff",
            "Written communication quality in reports and findings",
            "Active listening and comprehension during discussions",
        ]),
        ("C4", "Overall Time Management", 10, [
            "Adherence to assessment schedule and timelines",
            "Efficient use of assessment time across areas",
            "Timely submission of assessment reports",
            "Punctuality at meetings and scheduled activities",
        ]),
        ("C5", "Capability to Handle Difficult Situations", 10, [
            "Managing conflicts during assessment professionally",
            "Handling non-cooperative auditees diplomatically",
            "Adapting assessment approach when unexpected issues arise",
            "Maintaining composure under pressure",
        ]),
        ("C6", "Behaviour", 15, [
            "Professional demeanour throughout assessment",
            "Respect for laboratory staff and their processes",
            "Adherence to confidentiality requirements",
            "Ethical conduct and impartiality",
        ]),
    ]

    # 5 NABL Forms
    forms_config = [
        ("F1_CAB", "Form 1: Client/CAB Feedback", 0.15, "ROLE_PEER",
         ["ROLE_LEAD", "ROLE_PEER", "ROLE_TE"]),
        ("F2_LEAD", "Form 2: Lead Assessor Feedback", 0.25, "ROLE_LEAD",
         ["ROLE_PEER", "ROLE_TE", "ROLE_OBSERVER"]),
        ("F3_PEER", "Form 3: Peer Assessor Feedback", 0.15, "ROLE_PEER",
         ["ROLE_LEAD", "ROLE_PEER"]),
        ("F4_OFFICER", "Form 4: Dealing Officer Feedback", 0.20, "ROLE_OFFICER",
         ["ROLE_LEAD", "ROLE_PEER", "ROLE_TE"]),
        ("F5_COMMITTEE", "Form 5: Accreditation Committee Feedback", 0.25, "ROLE_COMMITTEE",
         ["ROLE_LEAD"]),
    ]

    # NABL-specific weight overrides per form (from NABL response)
    nabl_weight_overrides = {
        "F1_CAB": {"C1": 0, "C2": 20, "C3": 20, "C4": 20, "C5": 0, "C6": 20},
        "F2_LEAD": {"C1": 20, "C2": 20, "C3": 15, "C4": 10, "C5": 10, "C6": 15},
        "F3_PEER": {"C1": 20, "C2": 20, "C3": 15, "C4": 10, "C5": 10, "C6": 15},
        "F4_OFFICER": {"C1": 25, "C2": 25, "C3": 0, "C4": 0, "C5": 0, "C6": 0},
        "F5_COMMITTEE": {"C1": 25, "C2": 25, "C3": 0, "C4": 0, "C5": 0, "C6": 0},
    }

    for code, name, weight, eval_role, evaluee_roles in forms_config:
        fid = uid()
        db.add(FormTemplate(
            id=fid, board_id=board_id, code=code, name=name,
            stakeholder_weight=weight, target_evaluator_role=eval_role,
            target_evaluee_roles=evaluee_roles, is_mandatory=True
        ))
        overrides = nabl_weight_overrides.get(code, {})
        comps = []
        for c_code, c_label, c_weight, c_subs in nabl_competencies:
            w = overrides.get(c_code, c_weight)
            if w > 0:
                comps.append((c_code, c_label, w, c_subs))
        add_competency_params(db, fid, comps)
        add_essentials(db, fid)

    # Frequency rules
    for role in ["ROLE_LEAD", "ROLE_PEER", "ROLE_TE"]:
        for code, _, _, _, _ in forms_config:
            ft = db.query(FormTemplate).filter(FormTemplate.code == code, FormTemplate.board_id == board_id).first()
            if ft:
                db.add(FrequencyRule(
                    board_id=board_id, role_id=role, form_template_id=ft.id,
                    trigger_type="EVERY_AUDIT", is_active=True
                ))

    return board_id


def seed_nabh(db):
    board_id = uid()
    db.add(Board(
        id=board_id, code="NABH", name="National Accreditation Board for Hospitals & Healthcare Providers",
        description="Accreditation of hospitals, SHCO, blood banks, dental, AYUSH facilities",
        config={
            "rating_engine": "percentage",
            "star_bands": [
                {"min_pct": 80, "stars": 5},
                {"min_pct": 65, "stars": 4},
                {"min_pct": 50, "stars": 3},
                {"min_pct": 30, "stars": 2},
                {"min_pct": 0, "stars": 1},
            ],
            "stakeholder_weights": {
                "HOSPITAL": 0.30, "PEER": 0.20,
                "SECRETARIAT": 0.20, "COMMITTEE": 0.30,
            },
            "cumulative_window": 5,
            "terminology": {
                "evaluator": "Peer",
                "assessment": "Assessment",
                "organization": "Healthcare Organization (HCO)",
            },
            "vocabulary_map": {
                "assessor": "Assessor",
                "audit": "Assessment Visit",
                "finding": "Non-Compliance (NC)",
                "certificate": "Accreditation Letter",
                "client": "HCO (Healthcare Organization)",
            },
        }
    ))

    for rid, label, evaluator, evaluee in [
        ("ROLE_PA", "Principal Assessor", True, True),
        ("ROLE_COASS", "Co-assessor", True, True),
        ("ROLE_HCO", "HCO Representative", True, False),
        ("ROLE_SECRETARIAT", "NABH Secretariat", True, False),
        ("ROLE_COMMITTEE", "Accreditation Committee", True, False),
    ]:
        db.add(BoardRole(board_id=board_id, system_role_id=rid, display_label=label,
                         can_be_evaluator=evaluator, can_be_evaluee=evaluee))

    # NABH forms from the NABH Feedback Forms spreadsheet
    nabh_forms = [
        ("F_HCO_PA", "HCO Feedback for Principal Assessor", 0.30, "ROLE_HCO", ["ROLE_PA"], [
            ("C1", "Knowledge of HCO Practice and NABH Requirements", 10, [
                "In-depth knowledge of Standards and Objective Elements (OEs)",
                "Competent in required assessment skills",
                "Exhibits ability to learn and apply new skills",
                "Keeps abreast of current developments",
            ]),
            ("C2", "Assessment Skills", 10, [
                "Interpret and apply appropriate objective elements",
                "Flexible and open to accept HCO methods within constraints",
                "Gathers and analyses information without bias",
                "Adheres to ethical principles",
            ]),
            ("C3", "Adaptability", 10, [
                "Adapts to changes during assessment",
                "Manages conflicting demands within timeframe",
                "Changes approach or method to best fit the situation",
            ]),
            ("C4", "Time Management", 10, [
                "Arrives at HCO and meetings on time",
                "Begins working on time and uses time effectively",
                "Reviews documents and prepares points for observations",
                "Communicates any delays",
            ]),
            ("C5", "Communication and Information Collection", 20, [
                "Expresses findings and observations well verbally",
                "Expresses findings well in written form",
                "Exhibits good listening and comprehension skills",
                "NCs/observations are clearly documented for CAPA",
            ]),
            ("C6", "Integrity", 20, [
                "Honesty - sticking to facts, not judgmental",
                "Ethical and moral principles upheld",
                "Present during entire assessment",
                "Reporting facts without fear or favour",
            ]),
            ("C7", "Planning and Organization", 10, [
                "Prioritizes and plans work activities",
                "Plans for additional resources",
                "Works in an organized manner",
                "Exercises tact while dealing with peers and HCO",
            ]),
            ("C8", "General Conduct", 10, [
                "Professional turnout/dressing",
                "Prepared adequately for assessment",
                "Actively participates in team discussions",
                "Does not indulge in unnecessary arguments",
            ]),
        ]),
        ("F_PEER", "Peer/Assessor Feedback", 0.20, "ROLE_PA", ["ROLE_PA", "ROLE_COASS"], [
            ("C1", "Knowledge of HCO Practice and NABH Requirements", 10, [
                "In-depth knowledge of standards and OEs",
                "Understanding intent of each chapter",
                "Understanding interpretation nuances",
                "Awareness of environmental dynamics in healthcare",
            ]),
            ("C2", "Assessment Skills", 10, [
                "Interpreting and correctly applying standards and OEs",
                "Flexibility and openness to HCO methods",
                "Gathering and analysing information without bias",
                "Completing all assessment points in a single area visit",
            ]),
            ("C3", "Communication and Information Collection", 15, [
                "Expressing findings and observations verbally",
                "Expressing findings in written form",
                "Good listening and comprehension skills",
                "NCs/observations clearly documented for remedial measures",
            ]),
            ("C4", "Team Player and Interpersonal Skills", 10, [
                "Working cooperatively in group situations",
                "Exhibiting diplomacy and consideration",
                "Offering assistance and support to co-assessors",
                "Mentoring and developing trainee assessors",
            ]),
            ("C5", "Behaviour", 20, [
                "Courteousness and respect to HCO staff",
                "Respecting peers and co-assessors",
                "Speaking in a non-threatening manner",
                "Performing assessment per standard, not comparing HCOs",
            ]),
            ("C6", "Integrity", 20, [
                "Honesty - sticking to facts",
                "Ethical and moral principles upheld",
                "Present during entire assessment",
                "Reporting facts without fear or favour",
            ]),
        ]),
        ("F_SECRETARIAT", "NABH Secretariat Feedback", 0.20, "ROLE_SECRETARIAT", ["ROLE_PA", "ROLE_COASS"], [
            ("C1", "Communication with Staff", 15, [
                "Is courteous and professional",
                "Expresses views clearly",
                "Responds promptly to information requests",
                "Seeks prompt clarification when in doubt",
            ]),
            ("C2", "Adherence to Assessment Timelines", 25, [
                "Sends/uploads assessment plan 24+ hours prior",
                "Provides clear comments on proposed action plan",
                "Meets all scheduled deadlines",
            ]),
            ("C3", "Promptness in Document Submission", 15, [
                "Submits and uploads all reports promptly",
                "Submits all required documents within stipulated time",
                "Submits complete documents without omissions",
            ]),
            ("C4", "Participation in Training Activities", 10, [
                "Attends training programmes",
                "Attends conclaves organized by NABH",
            ]),
            ("C5", "Availability of Assessor", 20, [
                "Backs out from assessments after stating availability",
                "Backs out without genuine reason after accepting",
            ]),
            ("C6", "Adverse Remarks by Committee", 15, [
                "Frequency of adverse remarks by Accreditation Committee",
                "Frequency of adverse remarks by Assessor Management Committee",
            ]),
        ]),
        ("F_COMMITTEE", "Accreditation Committee Feedback", 0.30, "ROLE_COMMITTEE", ["ROLE_PA"], [
            ("C1", "Assessment Document Submission", 20, [
                "Submits and uploads all reports within stipulated time",
                "Provides additional documents for committee review",
            ]),
            ("C2", "Non-compliance Report Writing", 30, [
                "Identification and detailing of non-compliances",
                "Non-compliance adequately supported with evidence",
                "Correlation of NC reporting with objective elements",
                "Review of NC closures with comprehensive comments",
            ]),
            ("C3", "Assessment Summary Writing", 30, [
                "Submits clear and comprehensive summary of findings",
                "Assessment summary is well structured",
                "Adequate review of statutory compliances",
            ]),
            ("C4", "Scope of Services", 20, [
                "Scope recommended as per guidelines",
                "Scope based on infrastructure, facilities and HR",
                "Scope recommendations supported by clear comments",
            ]),
        ]),
    ]

    for code, name, weight, eval_role, evaluee_roles, competencies in nabh_forms:
        fid = uid()
        db.add(FormTemplate(
            id=fid, board_id=board_id, code=code, name=name,
            stakeholder_weight=weight, target_evaluator_role=eval_role,
            target_evaluee_roles=evaluee_roles, is_mandatory=True
        ))
        add_competency_params(db, fid, competencies)
        add_essentials(db, fid)

    for role in ["ROLE_PA", "ROLE_COASS"]:
        for code, _, _, _, _, _ in nabh_forms:
            ft = db.query(FormTemplate).filter(FormTemplate.code == code, FormTemplate.board_id == board_id).first()
            if ft:
                db.add(FrequencyRule(
                    board_id=board_id, role_id=role, form_template_id=ft.id,
                    trigger_type="EVERY_AUDIT", is_active=True
                ))

    return board_id


def seed_nabcb(db):
    board_id = uid()
    db.add(Board(
        id=board_id, code="NABCB",
        name="National Accreditation Board for Certification Bodies",
        description="Accreditation of certification, inspection and validation/verification bodies",
        config={
            "rating_engine": "numeric",
            "star_bands": [
                {"min": 4.5, "stars": 5}, {"min": 4.0, "stars": 4},
                {"min": 3.5, "stars": 3}, {"min": 3.0, "stars": 2}, {"min": 0, "stars": 1},
            ],
            "stakeholder_weights": {
                "CAB": 0.25, "LEAD_EVALUATOR": 0.25,
                "PEER": 0.20, "OFFICER": 0.15, "COMMITTEE": 0.15,
            },
            "cumulative_window": 10,
            "terminology": {
                "evaluator": "Evaluator",
                "assessment": "Assessment",
                "organization": "Conformity Assessment Body (CAB)",
            },
            "vocabulary_map": {
                "assessor": "Evaluator / Team Member",
                "audit": "Assessment",
                "finding": "Non-Conformity",
                "certificate": "Accreditation Certificate",
                "client": "CAB (Certification / Inspection Body)",
            },
        }
    ))

    for rid, label, evaluator, evaluee in [
        ("ROLE_TL", "Team Leader", True, True),
        ("ROLE_ASSESSOR", "Assessor", True, True),
        ("ROLE_TE", "Technical Expert", False, True),
        ("ROLE_TRAINEE", "Trainee Assessor", False, True),
        ("ROLE_OFFICER", "Program Officer", True, False),
        ("ROLE_COMMITTEE", "AMC Member", True, False),
    ]:
        db.add(BoardRole(board_id=board_id, system_role_id=rid, display_label=label,
                         can_be_evaluator=evaluator, can_be_evaluee=evaluee))

    # NABCB forms derived from NABCB feedback document
    nabcb_forms = [
        ("F_CAB", "CAB Feedback", 0.25, "ROLE_TL", ["ROLE_TL", "ROLE_ASSESSOR", "ROLE_TE"], [
            ("C1", "Planning the Assessment", 15, [
                "Preparing assessment plans and assigning roles/responsibilities",
                "Conducting pre-assessment meetings",
                "Identifying criteria and confirming scope",
            ]),
            ("C2", "Conducting Opening/Closing Meetings", 15, [
                "Conducting opening meetings and confirming plans",
                "Presenting assessment team and confirming scope/objectives",
                "Explaining methodology and clarifying queries",
                "Presenting and reviewing findings (NCs, OFIs)",
            ]),
            ("C3", "Assessment Execution", 25, [
                "Assessing management systems and technical requirements",
                "Assessing CAB against accreditation requirements",
                "Gathering and documenting objective evidence",
                "Extending sampling in case of NC",
            ]),
            ("C4", "Reporting and Documentation", 20, [
                "Producing clear and concise reports",
                "Preparing report reflecting performance and conformance",
                "Reporting conclusions and recommendations",
            ]),
            ("C5", "Professional Conduct", 15, [
                "Maintaining confidentiality of the process",
                "Relationship with team and auditee CB",
                "Full attention despite distractions",
                "Ethical conduct, maturity and objectivity",
            ]),
            ("C6", "Communication Skills", 10, [
                "Conducting effective interviews and observations",
                "Clear communication at all stages",
                "Time management and responsiveness",
            ]),
        ]),
    ]

    for code, name, weight, eval_role, evaluee_roles, competencies in nabcb_forms:
        fid = uid()
        db.add(FormTemplate(
            id=fid, board_id=board_id, code=code, name=name,
            stakeholder_weight=weight, target_evaluator_role=eval_role,
            target_evaluee_roles=evaluee_roles, is_mandatory=True
        ))
        add_competency_params(db, fid, competencies)
        add_essentials(db, fid)

    # Frequency rules for NABCB
    for role in ["ROLE_TL", "ROLE_ASSESSOR"]:
        for code, _, _, _, _, _ in nabcb_forms:
            ft = db.query(FormTemplate).filter(FormTemplate.code == code, FormTemplate.board_id == board_id).first()
            if ft:
                db.add(FrequencyRule(
                    board_id=board_id, role_id=role, form_template_id=ft.id,
                    trigger_type="EVERY_AUDIT", is_active=True
                ))

    return board_id


def seed_nabet(db):
    board_id = uid()
    db.add(Board(
        id=board_id, code="NABET",
        name="National Accreditation Board for Education and Training",
        description="Accreditation of education and training institutions, EIA consultants",
        config={
            "rating_engine": "numeric",
            "star_bands": [
                {"min": 4.5, "stars": 5}, {"min": 4.0, "stars": 4},
                {"min": 3.5, "stars": 3}, {"min": 3.0, "stars": 2}, {"min": 0, "stars": 1},
            ],
            "stakeholder_weights": {
                "CLIENT": 0.30, "LEAD": 0.25,
                "PEER": 0.20, "OFFICER": 0.15, "COMMITTEE": 0.10,
            },
            "cumulative_window": 10,
            "terminology": {
                "evaluator": "Assessor",
                "assessment": "Evaluation",
                "organization": "Institution / EIA Consultant Organization",
            },
            "vocabulary_map": {
                "assessor": "Assessor / Evaluator",
                "audit": "Evaluation Visit",
                "finding": "Observation / Non-Conformity",
                "certificate": "Accreditation Certificate",
                "client": "Institution / Training Organization",
            },
        }
    ))

    for rid, label, evaluator, evaluee in [
        ("ROLE_LEAD", "Lead Assessor", True, True),
        ("ROLE_ASSESSOR", "Assessor", True, True),
        ("ROLE_TE", "Technical Expert", False, True),
        ("ROLE_OFFICER", "Program Officer", True, False),
        ("ROLE_COMMITTEE", "Accreditation Committee", True, False),
    ]:
        db.add(BoardRole(board_id=board_id, system_role_id=rid, display_label=label,
                         can_be_evaluator=evaluator, can_be_evaluee=evaluee))

    # NABET — generic competency framework (can be configured via UI)
    fid = uid()
    db.add(FormTemplate(
        id=fid, board_id=board_id, code="F_CLIENT",
        name="Client Feedback Form", stakeholder_weight=0.30,
        target_evaluator_role="ROLE_LEAD",
        target_evaluee_roles=["ROLE_LEAD", "ROLE_ASSESSOR", "ROLE_TE"],
        is_mandatory=True
    ))
    add_competency_params(db, fid, [
        ("C1", "Technical Competence", 25, [
            "Knowledge of applicable standards and requirements",
            "Understanding of sector-specific technical areas",
            "Ability to assess conformity effectively",
            "Awareness of regulatory and statutory requirements",
        ]),
        ("C2", "Assessment Skills", 20, [
            "Effective planning and preparation",
            "Systematic assessment approach",
            "Evidence-based findings and conclusions",
            "Proper sampling techniques",
        ]),
        ("C3", "Communication and Interpersonal", 20, [
            "Clear verbal and written communication",
            "Active listening and comprehension",
            "Professional interview techniques",
            "Constructive feedback delivery",
        ]),
        ("C4", "Professional Management", 15, [
            "Time management and punctuality",
            "Document management and organization",
            "Follow-through on commitments",
            "Resource management",
        ]),
        ("C5", "Team Leadership and Collaboration", 10, [
            "Team coordination and delegation",
            "Conflict resolution abilities",
            "Mentoring of junior assessors",
            "Consensus building",
        ]),
        ("C6", "Ethical Standards and Integrity", 10, [
            "Impartiality and objectivity",
            "Confidentiality maintenance",
            "Ethical decision making",
            "Professional conduct",
        ]),
    ])
    add_essentials(db, fid)

    # Frequency rules for NABET
    for role in ["ROLE_LEAD", "ROLE_ASSESSOR"]:
        ft = db.query(FormTemplate).filter(FormTemplate.code == "F_CLIENT", FormTemplate.board_id == board_id).first()
        if ft:
            db.add(FrequencyRule(
                board_id=board_id, role_id=role, form_template_id=ft.id,
                trigger_type="EVERY_AUDIT", is_active=True
            ))

    return board_id


def seed_service_lines(db, board_id, board_code):
    """Seed sample service lines and programs for each board."""
    data = {
        "NABL": [
            ("SL_TEST", "Testing Laboratories", [
                ("P_ISO17025T", "ISO/IEC 17025 – Testing", "ISO/IEC 17025:2017"),
                ("P_ISO17025C", "ISO/IEC 17025 – Calibration", "ISO/IEC 17025:2017"),
                ("P_ISO15189", "ISO 15189 – Medical Labs", "ISO 15189:2022"),
            ]),
            ("SL_PT", "Proficiency Testing Providers", [
                ("P_ISO17043", "ISO/IEC 17043 – PT Providers", "ISO/IEC 17043:2023"),
            ]),
        ],
        "NABH": [
            ("SL_HOSP", "Hospitals", [
                ("P_HOSP_FULL", "Hospital Accreditation (Full)", None),
                ("P_HOSP_ENTRY", "Hospital Accreditation (Entry Level)", None),
            ]),
            ("SL_SHCO", "Small Healthcare Organizations", [
                ("P_SHCO", "SHCO Accreditation", None),
            ]),
            ("SL_BLOOD", "Blood Banks & Transfusion Services", [
                ("P_BLOOD", "Blood Bank Accreditation", None),
            ]),
        ],
        "NABCB": [
            ("SL_CB", "Certification Bodies", [
                ("P_ISO17021", "ISO/IEC 17021 – MS Certification", "ISO/IEC 17021-1:2015"),
                ("P_ISO17065", "ISO/IEC 17065 – Product Certification", "ISO/IEC 17065:2012"),
            ]),
            ("SL_IB", "Inspection Bodies", [
                ("P_ISO17020", "ISO/IEC 17020 – Inspection Bodies", "ISO/IEC 17020:2012"),
            ]),
        ],
        "NABET": [
            ("SL_EIA", "EIA Consultants", [
                ("P_EIA_A", "EIA Consultants – Category A", None),
                ("P_EIA_B", "EIA Consultants – Category B", None),
            ]),
            ("SL_SCHOOL", "Schools", [
                ("P_SCHOOL", "School Quality Certification", None),
            ]),
        ],
    }

    for sl_code, sl_name, programs in data.get(board_code, []):
        sl_id = uid()
        db.add(ServiceLine(
            id=sl_id, board_id=board_id, code=sl_code, name=sl_name, sort_order=0
        ))
        for i, (p_code, p_name, std) in enumerate(programs):
            db.add(Program(
                id=uid(), service_line_id=sl_id, board_id=board_id,
                code=p_code, name=p_name, standard_version=std, sort_order=i
            ))


def seed_users(db, board_ids: dict):
    """Seed one system admin + one board admin per board."""
    db.add(User(
        id=uid(),
        email="admin@qci.org.in",
        full_name="QCI System Administrator",
        password_hash=hash_password("Admin@123"),
        role="super_admin",
        board_id=None,
    ))
    admin_accounts = [
        ("nabl.admin@qci.org.in", "NABL Board Admin", "NABL"),
        ("nabh.admin@qci.org.in", "NABH Board Admin", "NABH"),
        ("nabcb.admin@qci.org.in", "NABCB Board Admin", "NABCB"),
        ("nabet.admin@qci.org.in", "NABET Board Admin", "NABET"),
    ]
    for email, name, code in admin_accounts:
        db.add(User(
            id=uid(),
            email=email,
            full_name=name,
            password_hash=hash_password("BoardAdmin@123"),
            role="board_admin",
            board_id=board_ids[code],
        ))


def seed_all():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        nabl_id = seed_nabl(db)
        nabh_id = seed_nabh(db)
        nabcb_id = seed_nabcb(db)
        nabet_id = seed_nabet(db)

        board_ids = {"NABL": nabl_id, "NABH": nabh_id, "NABCB": nabcb_id, "NABET": nabet_id}
        for code, bid in board_ids.items():
            seed_service_lines(db, bid, code)

        seed_users(db, board_ids)
        db.commit()
        print(f"Seeded: NABL={nabl_id}, NABH={nabh_id}, NABCB={nabcb_id}, NABET={nabet_id}")
        print("Default accounts:")
        print("  System Admin: admin@qci.org.in / Admin@123")
        print("  Board Admins: nabl.admin@qci.org.in (and nabh/nabcb/nabet) / BoardAdmin@123")
    finally:
        db.close()


if __name__ == "__main__":
    seed_all()
