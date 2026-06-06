from __future__ import annotations

import argparse
from datetime import date
from uuid import UUID

from db.session import connect
from pipeline.call_for_thesis import call_for_thesis


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble (and log) the CallCard for a thesis as-of a date."
    )
    parser.add_argument("--thesis", required=True, help="thesis id (uuid)")
    parser.add_argument("--asof", required=True, help="as-of date, YYYY-MM-DD")
    args = parser.parse_args()

    conn = connect()
    try:
        card = call_for_thesis(conn, UUID(args.thesis), date.fromisoformat(args.asof))
        conn.commit()
        print(card.model_dump_json(indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
