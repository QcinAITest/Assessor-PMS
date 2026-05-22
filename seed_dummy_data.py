"""
Seed dummy assessors + historical audit performance data across all 4 boards.
Safe to re-run — skips rows that already exist (upsert by employee_id / stable IDs).
"""

import sqlite3, uuid, json, random
from datetime import datetime, timedelta

DB_PATH = "qci_pms.db"

# ── Board metadata ──────────────────────────────────────────────────────────
BOARDS = {
    "NABL":  {"id": "ce7aa67e-722b-4f04-9dc5-9a32130b46b3", "engine": "numeric"},
    "NABH":  {"id": "6b7eba7c-6bc4-4213-a8e8-5ef9017c9bae", "engine": "percentage"},
    "NABCB": {"id": "6bc42ea1-cc75-4ccf-bdc3-c2096d113c2d", "engine": "numeric"},
    "NABET": {"id": "ab28aa14-85e6-4da2-9cc1-81a48eefac0f", "engine": "numeric"},
}

# Forms per board with stakeholder weights
BOARD_FORMS = {
    "NABL": [
        ("1078b766-06bd-4da2-bcc2-050fc42f103c", 0.15),
        ("187b4eb6-60d5-4e1f-aea5-106f075d36d6", 0.25),
        ("7f837272-b11c-4382-99ea-25d9c83942c4", 0.15),
        ("ac3ef266-7018-4706-81eb-03a582f88697", 0.20),
        ("d6c31bce-c503-44a7-8654-fa032c937c91", 0.25),
    ],
    "NABH": [
        ("995611bf-a6a6-429b-b67e-639f8d1f8fe7", 0.30),
        ("077e3733-e2ad-4e75-a06f-35dd991f12db", 0.20),
        ("1464af16-2a09-403e-886c-e4733f29ad21", 0.20),
        ("0afa9a81-a803-4acc-bcf5-b5ec9f92a4a8", 0.30),
    ],
    "NABCB": [
        ("b0639c7d-346f-4a6a-ba80-7c2c7e2ed375", 0.25),
    ],
    "NABET": [
        ("fae480d9-0110-4387-bed6-09bb1c3464fc", 0.30),
    ],
}

# Assessors per board: (employee_id, name, email, role_id, profile)
#   profile: "star" | "steady" | "improving" | "flagged"
ASSESSORS = {
    "NABL": [
        ("NABL-001", "Dr. Priya Sharma",    "p.sharma@nabl-assessor.in",   "ROLE_LEAD",     "star"),
        ("NABL-002", "Rajiv Menon",         "r.menon@nabl-assessor.in",    "ROLE_LEAD",     "steady"),
        ("NABL-003", "Sunita Kapoor",       "s.kapoor@nabl-assessor.in",   "ROLE_PEER",     "improving"),
        ("NABL-004", "Amir Khan",           "a.khan@nabl-assessor.in",     "ROLE_TE",       "flagged"),
        ("NABL-005", "Deepa Nair",          "d.nair@nabl-assessor.in",     "ROLE_PEER",     "steady"),
    ],
    "NABH": [
        ("NABH-001", "Dr. Anand Pillai",    "a.pillai@nabh-assessor.in",   "ROLE_PA",       "star"),
        ("NABH-002", "Meera Krishnan",      "m.krishnan@nabh-assessor.in", "ROLE_PA",       "improving"),
        ("NABH-003", "Suresh Rajan",        "s.rajan@nabh-assessor.in",    "ROLE_COASS",    "steady"),
        ("NABH-004", "Fatima Siddiqui",    "f.siddiqui@nabh-assessor.in", "ROLE_COASS",    "flagged"),
    ],
    "NABCB": [
        ("NABCB-001", "Vikram Singh",       "v.singh@nabcb-assessor.in",   "ROLE_TL",       "star"),
        ("NABCB-002", "Pooja Iyer",         "p.iyer@nabcb-assessor.in",    "ROLE_ASSESSOR", "steady"),
        ("NABCB-003", "Ramesh Gupta",       "r.gupta@nabcb-assessor.in",   "ROLE_TE",       "improving"),
    ],
    "NABET": [
        ("NABET-001", "Dr. Kavita Reddy",   "k.reddy@nabet-assessor.in",   "ROLE_LEAD",     "star"),
        ("NABET-002", "Manoj Tiwari",       "m.tiwari@nabet-assessor.in",  "ROLE_ASSESSOR", "steady"),
        ("NABET-003", "Geeta Bose",         "g.bose@nabet-assessor.in",    "ROLE_ASSESSOR", "improving"),
        ("NABET-004", "Sanjay Verma",       "s.verma@nabet-assessor.in",   "ROLE_TE",       "flagged"),
    ],
}

# Assessment organisations per board
ORGS = {
    "NABL":  ["Accurate Testing Lab", "BioAnalytics Pvt Ltd", "Central Food Labs",
               "Delta Calibration", "EnviroCheck Labs", "Frontier Materials Testing"],
    "NABH":  ["Apollo Clinic Hyderabad", "Batra Hospital Delhi", "Care Hospitals Pune",
               "Divine Nursing Home", "Excel Medical Centre"],
    "NABCB": ["TÜV Rheinland India", "Bureau Veritas Inspection", "Intertek India",
               "SGS India Pvt Ltd"],
    "NABET": ["Green Earth Consultants", "EnviroAssure India", "Eco Impact Advisory",
               "Natural Resource Experts"],
}

ASSESSMENT_TYPES = ["Initial", "Surveillance", "Re-assessment", "Extension"]


def score_for_profile(profile: str, audit_num: int, total: int, engine: str) -> float:
    """Generate a realistic score based on assessor profile arc."""
    rng = random.Random(profile + str(audit_num))  # deterministic

    if profile == "star":
        base = 4.5 if engine == "numeric" else 85
        jitter = rng.uniform(-0.25, 0.3) if engine == "numeric" else rng.uniform(-5, 8)
    elif profile == "steady":
        base = 3.8 if engine == "numeric" else 70
        jitter = rng.uniform(-0.3, 0.3) if engine == "numeric" else rng.uniform(-8, 8)
    elif profile == "improving":
        # Start low, trend upward
        progress = audit_num / max(total - 1, 1)
        base = (2.8 + progress * 1.5) if engine == "numeric" else (45 + progress * 35)
        jitter = rng.uniform(-0.2, 0.2) if engine == "numeric" else rng.uniform(-5, 5)
    elif profile == "flagged":
        # Generally mediocre with one or two bad audits
        base = 3.2 if engine == "numeric" else 58
        jitter = rng.uniform(-0.5, 0.4) if engine == "numeric" else rng.uniform(-15, 10)
    else:
        base = 3.5
        jitter = 0.0

    raw = base + jitter
    if engine == "numeric":
        return round(min(5.0, max(1.0, raw)), 2)
    else:
        return round(min(100.0, max(0.0, raw)), 2)


def star_rating(score: float, engine: str) -> int:
    if engine == "numeric":
        if score >= 4.5: return 5
        if score >= 4.0: return 4
        if score >= 3.5: return 3
        if score >= 3.0: return 2
        return 1
    else:  # percentage
        if score >= 80: return 5
        if score >= 65: return 4
        if score >= 50: return 3
        if score >= 30: return 2
        return 1


def norm_100(score: float, engine: str) -> float:
    if engine == "numeric":
        return round((score - 1) / 4 * 100, 2)
    return round(score, 2)


def main():
    random.seed(42)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    NOW = datetime.utcnow()

    assessor_id_map = {}  # employee_id → uuid

    # ── 1. Upsert Assessors ─────────────────────────────────────────────────
    print("\n=== Upserting Assessors ===")
    for board_code, people in ASSESSORS.items():
        board_id = BOARDS[board_code]["id"]
        for (emp_id, name, email, role_id, profile) in people:
            cur.execute("SELECT id FROM assessors WHERE employee_id=? AND board_id=?", (emp_id, board_id))
            row = cur.fetchone()
            if row:
                aid = row[0]
                print(f"  SKIP  {name} ({board_code}) — already exists")
            else:
                aid = str(uuid.uuid4())
                cur.execute("""
                    INSERT INTO assessors (id, employee_id, name, email, board_id, role_id,
                                          is_active, audit_count, created_at)
                    VALUES (?,?,?,?,?,?,1,0,?)
                """, (aid, emp_id, name, email, board_id, role_id, NOW.isoformat()))
                print(f"  CREATE {name} ({board_code})")
            assessor_id_map[(board_code, emp_id)] = aid

    conn.commit()

    # ── 2. Create Assessments + Audit Scores ───────────────────────────────
    print("\n=== Creating Assessments & Audit Scores ===")
    NUM_AUDITS = 10  # per assessor

    for board_code, people in ASSESSORS.items():
        board_id   = BOARDS[board_code]["id"]
        engine     = BOARDS[board_code]["engine"]
        forms      = BOARD_FORMS[board_code]
        orgs       = ORGS[board_code]

        for (emp_id, name, _, _, profile) in people:
            aid = assessor_id_map[(board_code, emp_id)]

            # Check how many audit_scores already exist for this assessor
            cur.execute("SELECT COUNT(*) FROM audit_scores WHERE evaluee_id=?", (aid,))
            existing = cur.fetchone()[0]
            if existing >= NUM_AUDITS:
                print(f"  SKIP  {name} ({board_code}) — {existing} audit scores already exist")
                continue

            audits_to_create = NUM_AUDITS - existing
            print(f"  {name} ({board_code}, {profile}) — creating {audits_to_create} audits")

            for i in range(audits_to_create):
                audit_num   = existing + i
                days_ago    = (NUM_AUDITS - audit_num) * 45  # ~45-day spacing
                audit_date  = NOW - timedelta(days=days_ago)
                org         = orgs[audit_num % len(orgs)]
                atype       = ASSESSMENT_TYPES[audit_num % len(ASSESSMENT_TYPES)]

                # Create assessment
                assessment_id = str(uuid.uuid4())
                cur.execute("""
                    INSERT INTO assessments (id, board_id, assessment_type, organization_name,
                                             scheme, assessment_date, status, created_at)
                    VALUES (?,?,?,?,'ISO/IEC 17025',?,?,?)
                """, (assessment_id, board_id, atype, org,
                      audit_date.isoformat(), "SCORED", audit_date.isoformat()))

                # Score for this audit
                final = score_for_profile(profile, audit_num, NUM_AUDITS, engine)
                stars = star_rating(final, engine)
                b100  = norm_100(final, engine)

                # Essential flag: flagged profile gets one flag around audit 3–5
                essential_flag = (profile == "flagged" and 2 <= audit_num <= 4)

                # Build per-form scores (distribute the final score across forms with small variance)
                form_scores = {}
                for (fid, weight) in forms:
                    rng2 = random.Random(fid + str(audit_num))
                    form_score = round(final + rng2.uniform(-0.2, 0.2), 2)
                    if engine == "numeric":
                        form_score = min(5.0, max(1.0, form_score))
                    else:
                        form_score = min(100.0, max(0.0, form_score))
                    form_scores[fid] = {"score": form_score, "weight": weight}

                audit_score_id = str(uuid.uuid4())
                cur.execute("""
                    INSERT INTO audit_scores (id, assessment_id, evaluee_id, board_id,
                                              form_scores, final_score, base_100_score,
                                              star_rating, essential_flag, calculated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (audit_score_id, assessment_id, aid, board_id,
                      json.dumps(form_scores), final, b100, stars,
                      1 if essential_flag else 0, audit_date.isoformat()))

            # Update audit_count on assessor
            cur.execute("UPDATE assessors SET audit_count=? WHERE id=?", (NUM_AUDITS, aid))

    conn.commit()

    # ── 3. Compute Cumulative Ratings ───────────────────────────────────────
    print("\n=== Computing Cumulative Ratings ===")
    for board_code, people in ASSESSORS.items():
        engine = BOARDS[board_code]["engine"]
        for (emp_id, name, _, _, profile) in people:
            aid = assessor_id_map[(board_code, emp_id)]

            cur.execute("""
                SELECT final_score, essential_flag, id FROM audit_scores
                WHERE evaluee_id=?
                ORDER BY calculated_at DESC
                LIMIT 10
            """, (aid,))
            rows = cur.fetchall()
            if not rows:
                continue

            scores       = [r[0] for r in rows]
            flags        = [r[1] for r in rows]
            score_ids    = [r[2] for r in rows]
            cum_score    = round(sum(scores) / len(scores), 2)
            has_flags    = any(f for f in flags)
            stars        = star_rating(cum_score, engine)

            # Upsert cumulative rating
            cur.execute("SELECT id FROM cumulative_ratings WHERE evaluee_id=?", (aid,))
            cr_row = cur.fetchone()
            if cr_row:
                cur.execute("""
                    UPDATE cumulative_ratings
                    SET cumulative_score=?, star_rating=?, window_size=?,
                        audit_scores_used=?, has_essential_flags=?, updated_at=?
                    WHERE evaluee_id=?
                """, (cum_score, stars, len(scores),
                      json.dumps(score_ids), 1 if has_flags else 0,
                      NOW.isoformat(), aid))
            else:
                cur.execute("""
                    INSERT INTO cumulative_ratings (id, evaluee_id, board_id, window_size,
                        audit_scores_used, cumulative_score, star_rating,
                        has_essential_flags, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (str(uuid.uuid4()), aid, BOARDS[board_code]["id"],
                      len(scores), json.dumps(score_ids),
                      cum_score, stars, 1 if has_flags else 0, NOW.isoformat()))
            print(f"  {name} ({board_code}): {cum_score} → {stars}★  flags={has_flags}")

    conn.commit()
    conn.close()
    print("\n✅ Done. Dummy data seeded across all 4 boards.")


if __name__ == "__main__":
    main()
