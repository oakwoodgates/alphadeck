from __future__ import annotations

import argparse
from datetime import date
from uuid import UUID

from db.session import connect
from domain.market_time import serve_view
from pipeline.call_for_thesis import call_for_thesis


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble (and log) the CallCard for a thesis as-of a date."
    )
    parser.add_argument("--thesis", required=True, help="thesis id (uuid)")
    parser.add_argument("--asof", required=True, help="as-of date, YYYY-MM-DD")
    args = parser.parse_args()

    asof = date.fromisoformat(args.asof)
    # a PAST asof (before the current night) is a history view, judged on the PUBLIC clock (what was public by
    # the end of that night, EDGAR's next open — ``domain.market_time.serve_view``), never today's knowledge of
    # that date; the
    # current night and later are the live read (F1). The decisions log reads at the as-of here: this writes a
    # call of record, so it never takes the serve path's action-day read (``serve_decisions_asof``).
    known_at, clock = serve_view(asof)
    conn = connect()
    try:
        card = call_for_thesis(conn, UUID(args.thesis), asof, known_at=known_at, clock=clock)
        conn.commit()
        print(card.model_dump_json(indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
