from fetcher import fetch_live_articles
from services.db_helpers import escape_like, get_conn as shared_get_conn

DB_NAME = "geoclaw.db"

def get_connection():
    return shared_get_conn(DB_NAME)

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        headline TEXT NOT NULL,
        source TEXT NOT NULL,
        url TEXT NOT NULL UNIQUE,
        published_at TEXT
    )
    """)

    conn.commit()
    conn.close()

def save_live_articles():
    data = fetch_live_articles()

    if "error" in data:
        return data

    articles = data.get("articles", [])

    conn = get_connection()
    cur = conn.cursor()

    inserted = 0

    for article in articles:
        cur.execute("""
        INSERT OR IGNORE INTO articles (headline, source, url, published_at)
        VALUES (?, ?, ?, ?)
        """, (
            article.get("headline", ""),
            article.get("source", ""),
            article.get("url", ""),
            article.get("published_at", "")
        ))

        if cur.rowcount > 0:
            inserted += 1

    conn.commit()
    conn.close()

    return {
        "fetched": len(articles),
        "inserted": inserted
    }

def count_saved_articles():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM articles")
    count = cur.fetchone()[0]

    conn.close()
    return count


def get_saved_articles(limit: int = 20):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, headline, source, url, published_at FROM articles ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = cur.fetchall()
    conn.close()

    return [
        {
            "id": row[0],
            "headline": row[1],
            "source": row[2],
            "url": row[3],
            "published_at": row[4],
        }
        for row in rows
    ]


def search_saved_articles(word: str, limit: int = 20):
    conn = get_connection()
    cur = conn.cursor()
    # Escape ``%`` and ``_`` in the user-supplied fragment so a caller can't
    # pin a DB worker with a pathological wildcard (e.g. ``%%%%``).  Matched
    # against the column with ``ESCAPE '\'``.
    like_word = f"%{escape_like(word)}%"
    cur.execute(
        "SELECT id, headline, source, url, published_at FROM articles WHERE headline LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
        (like_word, limit)
    )
    rows = cur.fetchall()
    conn.close()

    return [
        {
            "id": row[0],
            "headline": row[1],
            "source": row[2],
            "url": row[3],
            "published_at": row[4],
        }
        for row in rows
    ]
