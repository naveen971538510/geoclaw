import logging
import math
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List


logger = logging.getLogger("geoclaw.dedup")


class ThesisDeduplicator:
    SIMILARITY_THRESHOLD = 0.72

    def _tokenize(self, text: str) -> List[str]:
        cleaned = re.sub(r"[^\w\s]", " ", str(text or "").lower())
        tokens = cleaned.split()
        stopwords = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "will",
            "may",
            "might",
            "this",
            "that",
            "these",
            "those",
            "and",
            "or",
            "but",
            "for",
            "in",
            "on",
            "at",
            "to",
            "of",
            "with",
            "by",
            "from",
            "up",
            "if",
            "as",
            "be",
            "been",
        }
        return [token for token in tokens if token not in stopwords and len(token) > 2]

    def _tfidf_vector(self, text: str, all_docs: List[str]) -> Dict[str, float]:
        tokens = self._tokenize(text)
        tf = Counter(tokens)
        total = len(tokens) or 1
        tf_norm = {term: count / total for term, count in tf.items()}
        doc_count = len(all_docs) or 1
        idf = {}
        for term in tf_norm:
            df = sum(1 for doc in all_docs if term in self._tokenize(doc))
            idf[term] = math.log((doc_count + 1) / (df + 1)) + 1
        return {term: tf_norm[term] * idf[term] for term in tf_norm}

    def _cosine_similarity(self, left: Dict[str, float], right: Dict[str, float]) -> float:
        common = set(left) & set(right)
        if not common:
            return 0.0
        dot = sum(left[token] * right[token] for token in common)
        left_mag = math.sqrt(sum(value ** 2 for value in left.values()))
        right_mag = math.sqrt(sum(value ** 2 for value in right.values()))
        return dot / (left_mag * right_mag) if left_mag and right_mag else 0.0

    def find_duplicates(self, db_path: str) -> List[Dict]:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, thesis_key, confidence, evidence_count, status
            FROM agent_theses
            WHERE COALESCE(status, 'active') != 'superseded'
            ORDER BY confidence DESC, evidence_count DESC
            LIMIT 100
            """
        ).fetchall()
        conn.close()

        theses = [dict(row) for row in rows]
        keys = [str(item.get("thesis_key", "") or "") for item in theses]
        vectors = {key: self._tfidf_vector(key, keys) for key in keys}

        pairs = []
        for idx, left in enumerate(theses):
            for right in theses[idx + 1 :]:
                left_key = str(left.get("thesis_key", "") or "")
                right_key = str(right.get("thesis_key", "") or "")
                similarity = self._cosine_similarity(vectors.get(left_key, {}), vectors.get(right_key, {}))
                if similarity >= self.SIMILARITY_THRESHOLD:
                    pairs.append(
                        {
                            "thesis_a": left_key,
                            "conf_a": float(left.get("confidence", 0.0) or 0.0),
                            "evidence_a": int(left.get("evidence_count", 0) or 0),
                            "thesis_b": right_key,
                            "conf_b": float(right.get("confidence", 0.0) or 0.0),
                            "evidence_b": int(right.get("evidence_count", 0) or 0),
                            "similarity": round(similarity, 3),
                        }
                    )

        return sorted(pairs, key=lambda item: item["similarity"], reverse=True)

    def merge_duplicates(self, db_path: str, dry_run: bool = False) -> Dict:
        pairs = self.find_duplicates(db_path)
        if not pairs:
            return {"pairs_found": 0, "merged": 0, "superseded": [], "dry_run": dry_run}

        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        now = datetime.now(timezone.utc).isoformat()
        merged = 0
        superseded = []
        dropped = set()

        for pair in pairs:
            if pair["conf_a"] >= pair["conf_b"]:
                keep = pair["thesis_a"]
                drop = pair["thesis_b"]
                drop_evidence = pair["evidence_b"]
            else:
                keep = pair["thesis_b"]
                drop = pair["thesis_a"]
                drop_evidence = pair["evidence_a"]

            if drop in dropped or keep in dropped:
                continue

            logger.info("Merging duplicate thesis '%s' into '%s' (sim=%s)", drop[:60], keep[:60], pair["similarity"])
            if not dry_run:
                conn.execute(
                    """
                    UPDATE agent_theses
                    SET evidence_count = COALESCE(evidence_count, 0) + ?,
                        last_updated_at = ?
                    WHERE thesis_key = ?
                    """,
                    (max(1, int(drop_evidence or 1)), now, keep),
                )
                conn.execute(
                    """
                    UPDATE agent_theses
                    SET status = 'superseded',
                        last_update_reason = ?,
                        last_updated_at = ?
                    WHERE thesis_key = ?
                    """,
                    (f"Merged into: {keep[:100]}", now, drop),
                )
                conn.execute("UPDATE reasoning_chains SET thesis_key = ? WHERE thesis_key = ?", (keep, drop))
            dropped.add(drop)
            superseded.append(drop)
            merged += 1

        if not dry_run:
            conn.commit()
        conn.close()
        return {
            "pairs_found": len(pairs),
            "merged": merged,
            "superseded": superseded[:10],
            "dry_run": dry_run,
        }
