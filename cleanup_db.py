from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intelligence.db import ensure_intelligence_schema, get_connection, query_one


def main() -> None:
    ensure_intelligence_schema()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM geoclaw_signals;")
        before = int(cur.fetchone()[0] or 0)

        cur.execute("DELETE FROM geoclaw_signals WHERE direction = 'NEUTRAL';")

        cur.execute(
            """
            DELETE FROM geoclaw_signals g
            USING geoclaw_signals d
            WHERE g.signal_name = d.signal_name
              AND g.id < d.id;
            """
        )

        cur.execute("SELECT COUNT(*) FROM geoclaw_signals;")
        after = int(cur.fetchone()[0] or 0)
        cur.close()

    print(f"before_count={before}")
    print(f"after_count={after}")
    print(f"removed={before - after}")


if __name__ == "__main__":
    main()

