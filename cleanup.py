import sqlite3
from pathlib import Path

from config import DB_PATH


def run_cleanup():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cur.execute("DELETE FROM agent_tasks WHERE status = 'completed'")
    deleted_tasks = int(cur.rowcount or 0)
    print(f"Deleted completed tasks: {deleted_tasks}")

    cur.execute(
        """
        DELETE FROM agent_journal
        WHERE id NOT IN (
            SELECT id
            FROM agent_journal
            ORDER BY id DESC
            LIMIT 20
        )
        """
    )
    deleted_journal = int(cur.rowcount or 0)
    print(f"Deleted old journal rows: {deleted_journal}")

    conn.commit()
    conn.close()

    state_dir = Path(".state")
    deleted_files = 0
    if state_dir.exists():
        for path in state_dir.glob("*.json"):
            path.unlink(missing_ok=True)
            deleted_files += 1
    print(f"Deleted .state JSON files: {deleted_files}")

    return {
        "deleted_completed_tasks": deleted_tasks,
        "deleted_old_journal_rows": deleted_journal,
        "deleted_state_json_files": deleted_files,
    }


if __name__ == "__main__":
    run_cleanup()
