"""
Schema migration — run after each pull that changes the data model.
Supports both SQLite (local dev) and PostgreSQL (production).

Step 1: create_all() — creates any tables that don't exist yet (safe on existing DBs)
Step 2: ALTER TABLE   — adds any columns added after initial table creation
"""
import os
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./qci_pms.db")
IS_POSTGRES = DATABASE_URL.startswith("postgresql") or DATABASE_URL.startswith("postgres")


def _create_all_tables():
    """Use SQLAlchemy to create all tables defined in models (idempotent — skips existing)."""
    from app.database import engine, Base
    import app.models.auth   # noqa: F401 — registers User model
    import app.models.board  # noqa: F401 — registers all board models
    import app.models.program  # noqa: F401 — registers ServiceLine, Program
    Base.metadata.create_all(bind=engine)
    print("Tables created / verified via SQLAlchemy.")


def migrate():
    _create_all_tables()
    if IS_POSTGRES:
        _migrate_postgres()
    else:
        _migrate_sqlite()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pg_columns(cur, table):
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,)
    )
    return {row[0] for row in cur.fetchall()}


def _sqlite_columns(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# PostgreSQL migration
# ---------------------------------------------------------------------------

def _migrate_postgres():
    import psycopg2

    print(f"Connecting to PostgreSQL …")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    def columns(table):
        return _pg_columns(cur, table)

    NOW = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # --- board_roles ---
    cols = columns("board_roles")
    if "created_at" not in cols:
        cur.execute("ALTER TABLE board_roles ADD COLUMN created_at TIMESTAMP")
        cur.execute(f"UPDATE board_roles SET created_at = '{NOW}' WHERE created_at IS NULL")
        print("board_roles.created_at added")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE board_roles ADD COLUMN updated_at TIMESTAMP")
        cur.execute(f"UPDATE board_roles SET updated_at = '{NOW}' WHERE updated_at IS NULL")
        print("board_roles.updated_at added")

    # --- essential_criteria ---
    cols = columns("essential_criteria")
    if "created_at" not in cols:
        cur.execute("ALTER TABLE essential_criteria ADD COLUMN created_at TIMESTAMP")
        cur.execute(f"UPDATE essential_criteria SET created_at = '{NOW}' WHERE created_at IS NULL")
        print("essential_criteria.created_at added")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE essential_criteria ADD COLUMN updated_at TIMESTAMP")
        cur.execute(f"UPDATE essential_criteria SET updated_at = '{NOW}' WHERE updated_at IS NULL")
        print("essential_criteria.updated_at added")

    # --- frequency_rules ---
    cols = columns("frequency_rules")
    if "created_at" not in cols:
        cur.execute("ALTER TABLE frequency_rules ADD COLUMN created_at TIMESTAMP")
        cur.execute(f"UPDATE frequency_rules SET created_at = '{NOW}' WHERE created_at IS NULL")
        print("frequency_rules.created_at added")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE frequency_rules ADD COLUMN updated_at TIMESTAMP")
        cur.execute(f"UPDATE frequency_rules SET updated_at = '{NOW}' WHERE updated_at IS NULL")
        print("frequency_rules.updated_at added")

    # --- webhooks ---
    cols = columns("webhooks")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE webhooks ADD COLUMN updated_at TIMESTAMP")
        print("webhooks.updated_at added")

    # --- assessors ---
    cols = columns("assessors")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE assessors ADD COLUMN updated_at TIMESTAMP")
        print("assessors.updated_at added")

    # --- users: external_id ---
    cols = columns("users")
    if "external_id" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN external_id VARCHAR(100)")
        print("users.external_id added")

    # --- form_submissions: make FK columns nullable ---
    # In PostgreSQL we can ALTER COLUMN to drop NOT NULL safely.
    nullable_cols = {
        "assessment_id": "VARCHAR(36)",
        "evaluator_id":  "VARCHAR(36)",
        "evaluee_id":    "VARCHAR(36)",
    }
    for col, _ in nullable_cols.items():
        cur.execute(f"""
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_name = 'form_submissions' AND column_name = %s
        """, (col,))
        row = cur.fetchone()
        if row and row[0] == "NO":
            cur.execute(f"ALTER TABLE form_submissions ALTER COLUMN {col} DROP NOT NULL")
            print(f"form_submissions.{col} made nullable")

    # --- form_submissions: Fix 1 (token expiry) + Fix 2 (form snapshot) ---
    cols = columns("form_submissions")
    if "token_expires_at" not in cols:
        cur.execute("ALTER TABLE form_submissions ADD COLUMN token_expires_at TIMESTAMP")
        print("form_submissions.token_expires_at added")
    if "evaluator_email" not in cols:
        cur.execute("ALTER TABLE form_submissions ADD COLUMN evaluator_email VARCHAR(300)")
        print("form_submissions.evaluator_email added")
    if "form_snapshot" not in cols:
        cur.execute("ALTER TABLE form_submissions ADD COLUMN form_snapshot JSON")
        print("form_submissions.form_snapshot added")

    # --- webhooks: Fix 3 (dispatch observability) ---
    cols = columns("webhooks")
    if "last_fired_at" not in cols:
        cur.execute("ALTER TABLE webhooks ADD COLUMN last_fired_at TIMESTAMP")
        print("webhooks.last_fired_at added")
    if "last_response_status" not in cols:
        cur.execute("ALTER TABLE webhooks ADD COLUMN last_response_status INTEGER")
        print("webhooks.last_response_status added")

    # --- assessors: Fix 6 (composite unique constraint per board) ---
    # Pre-check: abort if duplicates exist that would violate the new constraint
    cur.execute("""
        SELECT employee_id, board_id, COUNT(*)
        FROM assessors
        GROUP BY employee_id, board_id
        HAVING COUNT(*) > 1
    """)
    dupes = cur.fetchall()
    if dupes:
        print(f"WARNING: Cannot apply assessor deduplication constraint — "
              f"{len(dupes)} duplicate (employee_id, board_id) pairs found. "
              f"Resolve duplicates before re-running migration.")
    else:
        # Drop old global unique constraint on employee_id
        cur.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'assessors_employee_id_key'
                      AND conrelid = 'assessors'::regclass
                ) THEN
                    ALTER TABLE assessors DROP CONSTRAINT assessors_employee_id_key;
                END IF;
            END$$;
        """)
        # Create composite unique index (idempotent)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_assessor_board_employee
            ON assessors(board_id, employee_id)
        """)
        print("assessors: employee_id unique → composite (board_id, employee_id)")

    conn.commit()
    cur.close()
    conn.close()
    print("Migration complete (PostgreSQL).")


# ---------------------------------------------------------------------------
# SQLite migration
# ---------------------------------------------------------------------------

def _migrate_sqlite():
    import sqlite3

    db_path = DATABASE_URL.replace("sqlite:///./", "").replace("sqlite:///", "")
    print(f"Connecting to SQLite: {db_path} …")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    cur = conn.cursor()

    def columns(table):
        return _sqlite_columns(cur, table)

    NOW = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # --- board_roles ---
    cols = columns("board_roles")
    if "created_at" not in cols:
        cur.execute("ALTER TABLE board_roles ADD COLUMN created_at DATETIME")
        cur.execute(f"UPDATE board_roles SET created_at = '{NOW}' WHERE created_at IS NULL")
        print("board_roles.created_at added")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE board_roles ADD COLUMN updated_at DATETIME")
        cur.execute(f"UPDATE board_roles SET updated_at = '{NOW}' WHERE updated_at IS NULL")
        print("board_roles.updated_at added")

    # --- essential_criteria ---
    cols = columns("essential_criteria")
    if "created_at" not in cols:
        cur.execute("ALTER TABLE essential_criteria ADD COLUMN created_at DATETIME")
        cur.execute(f"UPDATE essential_criteria SET created_at = '{NOW}' WHERE created_at IS NULL")
        print("essential_criteria.created_at added")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE essential_criteria ADD COLUMN updated_at DATETIME")
        cur.execute(f"UPDATE essential_criteria SET updated_at = '{NOW}' WHERE updated_at IS NULL")
        print("essential_criteria.updated_at added")

    # --- frequency_rules ---
    cols = columns("frequency_rules")
    if "created_at" not in cols:
        cur.execute("ALTER TABLE frequency_rules ADD COLUMN created_at DATETIME")
        cur.execute(f"UPDATE frequency_rules SET created_at = '{NOW}' WHERE created_at IS NULL")
        print("frequency_rules.created_at added")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE frequency_rules ADD COLUMN updated_at DATETIME")
        cur.execute(f"UPDATE frequency_rules SET updated_at = '{NOW}' WHERE updated_at IS NULL")
        print("frequency_rules.updated_at added")

    # --- webhooks ---
    cols = columns("webhooks")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE webhooks ADD COLUMN updated_at DATETIME")
        print("webhooks.updated_at added")

    # --- assessors ---
    cols = columns("assessors")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE assessors ADD COLUMN updated_at DATETIME")
        print("assessors.updated_at added")

    # --- users: external_id ---
    cols = columns("users")
    if "external_id" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN external_id VARCHAR(100)")
        print("users.external_id added")

    # --- form_submissions: recreate to make FK columns nullable (original migration) ---
    cur.execute("SELECT COUNT(*) FROM form_submissions")
    sub_count = cur.fetchone()[0]
    # Check if the table already has the new columns before deciding to recreate
    fs_cols = columns("form_submissions")
    needs_recreation = (
        "assessment_id" in fs_cols and  # table exists
        "token_expires_at" not in fs_cols  # new columns missing
    )
    if sub_count == 0 and needs_recreation:
        cur.executescript("""
            DROP TABLE IF EXISTS form_submissions_new;
            CREATE TABLE form_submissions_new (
                id                VARCHAR(36)  NOT NULL PRIMARY KEY,
                assessment_id     VARCHAR(36),
                form_template_id  VARCHAR(36)  NOT NULL,
                evaluator_id      VARCHAR(36),
                evaluee_id        VARCHAR(36),
                status            VARCHAR(20),
                responses         JSON,
                form_score        FLOAT,
                essential_flag    BOOLEAN,
                comments          TEXT,
                submission_token  VARCHAR(36)  UNIQUE,
                token_expires_at  DATETIME,
                evaluator_email   VARCHAR(300),
                form_snapshot     JSON,
                submitted_at      DATETIME,
                created_at        DATETIME     DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (assessment_id)    REFERENCES assessments(id),
                FOREIGN KEY (form_template_id) REFERENCES form_templates(id),
                FOREIGN KEY (evaluator_id)     REFERENCES assessors(id),
                FOREIGN KEY (evaluee_id)       REFERENCES assessors(id)
            );
            INSERT INTO form_submissions_new SELECT id, assessment_id, form_template_id, evaluator_id, evaluee_id, status, responses, form_score, essential_flag, comments, submission_token, NULL, NULL, NULL, submitted_at, created_at FROM form_submissions;
            DROP TABLE form_submissions;
            ALTER TABLE form_submissions_new RENAME TO form_submissions;
        """)
        print("form_submissions recreated with nullable FK columns + new columns")
    else:
        # Add new columns incrementally (safe for tables with existing data)
        fs_cols = columns("form_submissions")
        if "token_expires_at" not in fs_cols:
            cur.execute("ALTER TABLE form_submissions ADD COLUMN token_expires_at DATETIME")
            print("form_submissions.token_expires_at added")
        if "evaluator_email" not in fs_cols:
            cur.execute("ALTER TABLE form_submissions ADD COLUMN evaluator_email VARCHAR(300)")
            print("form_submissions.evaluator_email added")
        if "form_snapshot" not in fs_cols:
            cur.execute("ALTER TABLE form_submissions ADD COLUMN form_snapshot JSON")
            print("form_submissions.form_snapshot added")
        if sub_count > 0:
            print(f"form_submissions: {sub_count} existing rows — added new columns in place")

    # --- webhooks: Fix 3 (dispatch observability) ---
    cols = columns("webhooks")
    if "last_fired_at" not in cols:
        cur.execute("ALTER TABLE webhooks ADD COLUMN last_fired_at DATETIME")
        print("webhooks.last_fired_at added")
    if "last_response_status" not in cols:
        cur.execute("ALTER TABLE webhooks ADD COLUMN last_response_status INTEGER")
        print("webhooks.last_response_status added")

    # --- assessors: Fix 6 (composite unique constraint per board) ---
    # Pre-check: abort if duplicates exist that would violate the new constraint
    cur.execute("""
        SELECT COUNT(*) FROM (
            SELECT employee_id, board_id
            FROM assessors
            GROUP BY employee_id, board_id
            HAVING COUNT(*) > 1
        )
    """)
    dupe_count = cur.fetchone()[0]
    if dupe_count > 0:
        print(f"WARNING: Cannot apply assessor deduplication constraint — "
              f"{dupe_count} duplicate (employee_id, board_id) pairs found. "
              f"Resolve duplicates before re-running migration.")
    else:
        # Check if the unique constraint already exists by checking if we can recreate safely
        # SQLite requires table recreation to change constraints
        a_cols = columns("assessors")
        cur.executescript("""
            DROP TABLE IF EXISTS assessors_new;
            CREATE TABLE assessors_new (
                id           VARCHAR(36)  NOT NULL PRIMARY KEY,
                employee_id  VARCHAR(50)  NOT NULL,
                name         VARCHAR(300) NOT NULL,
                email        VARCHAR(300),
                phone        VARCHAR(30),
                board_id     VARCHAR(36)  NOT NULL REFERENCES boards(id),
                role_id      VARCHAR(50)  NOT NULL,
                is_active    BOOLEAN      DEFAULT 1,
                audit_count  INTEGER      DEFAULT 0,
                metadata     JSON         DEFAULT '{}',
                created_at   DATETIME,
                updated_at   DATETIME,
                UNIQUE(board_id, employee_id)
            );
            INSERT INTO assessors_new SELECT * FROM assessors;
            DROP TABLE assessors;
            ALTER TABLE assessors_new RENAME TO assessors;
        """)
        print("assessors: recreated with composite UNIQUE(board_id, employee_id)")

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()
    print("Migration complete (SQLite).")


if __name__ == "__main__":
    migrate()
