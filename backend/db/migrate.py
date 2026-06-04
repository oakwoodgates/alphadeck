from __future__ import annotations

from pathlib import Path

import psycopg

from db.session import connect

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def apply_migrations(conn: psycopg.Connection | None = None) -> list[str]:
    """Apply any unapplied ``*.sql`` migrations in filename order. Returns those applied this run.

    Tracks applied files in ``schema_migrations`` so re-runs are no-ops. The migration SQL is also
    written idempotently (CREATE ... IF NOT EXISTS / OR REPLACE) as a belt-and-braces second line.
    """
    own = conn is None
    conn = conn or connect()
    applied: list[str] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            cur.execute("SELECT filename FROM schema_migrations")
            done = {row["filename"] for row in cur.fetchall()}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            with conn.cursor() as cur:
                cur.execute(path.read_text(encoding="utf-8"))  # multi-statement (simple protocol)
                cur.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            applied.append(path.name)

        conn.commit()
        return applied
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    names = apply_migrations()
    print(f"applied: {', '.join(names)}" if names else "schema up to date")
