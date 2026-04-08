"""
Schema migration — run ONCE after the Fix-7 model changes.
Adds missing created_at/updated_at columns and makes form_submissions
nullable without dropping any existing data.
"""
import sqlite3
import os

DB_PATH = os.getenv("DATABASE_URL", "qci_pms.db").replace("sqlite:///", "")

def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    cur = conn.cursor()

    def columns(table):
        cur.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cur.fetchall()}

    NOW = "2024-01-01 00:00:00"  # sentinel for existing rows; future rows use Python datetime

    # --- board_roles: add created_at, updated_at ---
    cols = columns("board_roles")
    if "created_at" not in cols:
        cur.execute("ALTER TABLE board_roles ADD COLUMN created_at DATETIME")
        cur.execute(f"UPDATE board_roles SET created_at = '{NOW}' WHERE created_at IS NULL")
        print("board_roles.created_at added")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE board_roles ADD COLUMN updated_at DATETIME")
        cur.execute(f"UPDATE board_roles SET updated_at = '{NOW}' WHERE updated_at IS NULL")
        print("board_roles.updated_at added")

    # --- essential_criteria: add created_at, updated_at ---
    cols = columns("essential_criteria")
    if "created_at" not in cols:
        cur.execute("ALTER TABLE essential_criteria ADD COLUMN created_at DATETIME")
        cur.execute(f"UPDATE essential_criteria SET created_at = '{NOW}' WHERE created_at IS NULL")
        print("essential_criteria.created_at added")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE essential_criteria ADD COLUMN updated_at DATETIME")
        cur.execute(f"UPDATE essential_criteria SET updated_at = '{NOW}' WHERE updated_at IS NULL")
        print("essential_criteria.updated_at added")

    # --- frequency_rules: add created_at, updated_at ---
    cols = columns("frequency_rules")
    if "created_at" not in cols:
        cur.execute("ALTER TABLE frequency_rules ADD COLUMN created_at DATETIME")
        cur.execute(f"UPDATE frequency_rules SET created_at = '{NOW}' WHERE created_at IS NULL")
        print("frequency_rules.created_at added")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE frequency_rules ADD COLUMN updated_at DATETIME")
        cur.execute(f"UPDATE frequency_rules SET updated_at = '{NOW}' WHERE updated_at IS NULL")
        print("frequency_rules.updated_at added")

    # --- webhooks: add updated_at (created_at already exists) ---
    cols = columns("webhooks")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE webhooks ADD COLUMN updated_at DATETIME")
        print("webhooks.updated_at added")

    # --- assessors: add updated_at (created_at already exists) ---
    cols = columns("assessors")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE assessors ADD COLUMN updated_at DATETIME")
        print("assessors.updated_at added")

    # --- form_submissions: recreate to make assessment_id/evaluator_id/evaluee_id nullable ---
    # Safe because there are no real submissions in a fresh dev DB.
    cur.execute("SELECT COUNT(*) FROM form_submissions")
    sub_count = cur.fetchone()[0]
    if sub_count == 0:
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
                submitted_at      DATETIME,
                created_at        DATETIME     DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (assessment_id)    REFERENCES assessments(id),
                FOREIGN KEY (form_template_id) REFERENCES form_templates(id),
                FOREIGN KEY (evaluator_id)     REFERENCES assessors(id),
                FOREIGN KEY (evaluee_id)       REFERENCES assessors(id)
            );
            INSERT INTO form_submissions_new SELECT * FROM form_submissions;
            DROP TABLE form_submissions;
            ALTER TABLE form_submissions_new RENAME TO form_submissions;
        """)
        print("form_submissions recreated with nullable FK columns")
    else:
        print(f"Skipped form_submissions recreation — {sub_count} existing rows present. "
              "Recreate manually if distribution links are needed.")

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
