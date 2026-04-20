import random
import re
import sqlite3
from typing import Callable, Dict, List

from services.ai_contracts import default_query_answer_bundle, format_query_answer_text
from services.db_helpers import escape_like


QUERY_PATTERNS = [
    (r"what.*(driving|moving|causing).*oil", "explain_asset"),
    (r"what.*(driving|moving|causing).*gold", "explain_asset"),
    (r"what.*(driving|moving|causing).*dollar", "explain_asset"),
    (r"what.*(driving|moving|causing).*market", "explain_market"),
    (r"why.*confidence.*(high|rising|up)", "explain_high_confidence"),
    (r"why.*confidence.*(low|falling|down)", "explain_low_confidence"),
    (r"what.*happening.*iran", "explain_country"),
    (r"what.*happening.*china", "explain_country"),
    (r"what.*happening.*russia", "explain_country"),
    (r"what.*risk.*right now", "explain_current_risk"),
    (r"what.*watch.*tomorrow", "explain_watchlist"),
    (r"show.*top.*thesis|best.*thesis", "show_top_theses"),
    (r"show.*confirmed", "show_confirmed_theses"),
    (r"any.*contradiction", "show_contradictions"),
    (r"what.*regime|market.*regime", "show_regime"),
    (r"summary|brief|overview", "show_summary"),
    (r"what.*action.*pending", "show_pending_actions"),
    (r"how.*accurate|accuracy", "show_calibration"),
    (r"what.*article.*recent|latest.*news", "show_recent_articles"),
]

SUGGESTIONS = [
    "What is driving oil right now?",
    "What is the current market regime?",
    "Show me the top confirmed theses",
    "Any active contradictions?",
    "What happened to gold today?",
    "What actions are pending review?",
    "How accurate has the agent been?",
    "What is the risk level right now?",
    "What is happening in Iran?",
    "What should I watch tomorrow?",
    "Why is confidence rising on sanctions?",
    "Show me the latest news summary",
]


class QueryEngine:
    def __init__(self, db_path, llm_analyst=None):
        self.db_path = str(db_path)
        self.llm = llm_analyst
        self._re = re

    def ask(self, question: str) -> Dict:
        clean_question = str(question or "").strip()
        q = clean_question.lower()
        handler = self._match_pattern(q)
        try:
            result = handler(q)
        except Exception as exc:
            result = {
                "answer": f"I couldn't process that question: {exc}",
                "data": {},
                "sources": [],
                "confidence": 0.0,
            }

        result["question"] = clean_question
        result["answer"] = str(result.get("answer", "") or "")
        result["data"] = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
        result["sources"] = [str(item) for item in (result.get("sources") or [])]
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.0) or 0.0)))
        result.setdefault("follow_up", self._suggest_followups(q))
        answer_card = self._build_answer_card(clean_question, result)
        if self.llm and result.get("data"):
            answer_card = self._enhance_with_llm(clean_question, result, answer_card)
        result["follow_up"] = list(answer_card.get("follow_up") or result.get("follow_up") or [])
        result["grounding_points"] = list(answer_card.get("supporting_points") or [])
        result["answer_card"] = {
            **default_query_answer_bundle(answer_card),
            "confidence_pct": round(result["confidence"] * 100),
            "sources": result["sources"],
        }
        result["answer"] = format_query_answer_text(answer_card, result["confidence"], result["sources"])

        return result

    def _match_pattern(self, q: str) -> Callable[[str], Dict]:
        for pattern, handler_name in QUERY_PATTERNS:
            if self._re.search(pattern, q):
                return getattr(self, f"_handle_{handler_name}", self._handle_generic)
        return self._handle_generic

    def _db(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _table_exists(self, conn, table_name: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (str(table_name or "").strip(),),
        ).fetchone()
        return bool(row)

    def _columns(self, conn, table_name: str) -> List[str]:
        try:
            rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            return [str(row[1]) for row in rows]
        except Exception:
            return []

    def _like_term(self, table: str, column: str, term: str, limit: int = 5) -> List[Dict]:
        conn = self._db()
        try:
            if not self._table_exists(conn, table) or column not in self._columns(conn, table):
                return []
            # Escape LIKE wildcards on the untrusted side so callers can't
            # pin a worker with ``%%%%`` (see services/db_helpers.escape_like).
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE {column} LIKE ? ESCAPE '\\' ORDER BY ROWID DESC LIMIT ?",
                (f"%{escape_like(term)}%", int(limit)),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _handle_explain_asset(self, q: str) -> Dict:
        assets = {
            "oil": ["oil", "crude", "brent", "opec", "energy"],
            "gold": ["gold", "bullion", "safe haven", "precious"],
            "dollar": ["dollar", "usd", "dxy", "fx"],
        }
        matched_asset = next((name for name in assets if name in q), "market")
        terms = assets.get(matched_asset, [matched_asset])

        conn = self._db()
        try:
            thesis_rows = []
            article_rows = []
            for term in terms:
                thesis_rows.extend(
                    conn.execute(
                        """
                        SELECT thesis_key, current_claim, confidence, status, last_update_reason,
                               terminal_risk, watchlist_suggestion, evidence_count, confidence_velocity
                        FROM agent_theses
                        WHERE status != 'superseded'
                          AND (thesis_key LIKE ? OR current_claim LIKE ? OR title LIKE ?)
                        ORDER BY confidence DESC, evidence_count DESC
                        LIMIT 5
                        """,
                        (f"%{term}%", f"%{term}%", f"%{term}%"),
                    ).fetchall()
                )
                article_rows.extend(
                    conn.execute(
                        """
                        SELECT headline, source_name, published_at, fetched_at, url
                        FROM ingested_articles
                        WHERE headline LIKE ? OR summary LIKE ?
                        ORDER BY COALESCE(published_at, fetched_at, '') DESC, id DESC
                        LIMIT 5
                        """,
                        (f"%{term}%", f"%{term}%"),
                    ).fetchall()
                )
        finally:
            conn.close()

        thesis_list = self._dedupe_rows(thesis_rows, "thesis_key")
        article_list = self._dedupe_rows(article_rows, "headline")
        if thesis_list:
            top = thesis_list[0]
            conf = round(float(top.get("confidence", 0.0) or 0.0) * 100)
            answer = (
                f"The clearest active driver for {matched_asset} is {top.get('thesis_key', '')[:170]} "
                f"at {conf}% confidence and {top.get('status', 'active')} status. "
                f"{str(top.get('last_update_reason', '') or '').strip()[:140]} "
                f"Watch {top.get('watchlist_suggestion', 'the related price strip')} next."
            ).strip()
            confidence = float(top.get("confidence", 0.0) or 0.0)
        else:
            answer = f"I do not see an active thesis specifically tagged to {matched_asset} yet. Run the agent again for fresher evidence."
            confidence = 0.3

        return {
            "answer": answer,
            "data": {"theses": thesis_list[:5], "articles": article_list[:5], "asset": matched_asset},
            "sources": ["agent_theses", "ingested_articles"],
            "confidence": confidence,
        }

    def _handle_explain_market(self, q: str) -> Dict:
        regime_result = self._handle_show_regime(q)
        top_theses_result = self._handle_show_top_theses(q)
        top_theses = (top_theses_result.get("data") or {}).get("theses", [])
        narrative = top_theses[0]["thesis_key"] if top_theses else "No dominant thesis yet."
        answer = (
            f"{regime_result.get('answer', 'Market regime unavailable.')} "
            f"Top live thesis: {str(narrative)[:160]}"
        ).strip()
        return {
            "answer": answer,
            "data": {
                "regime": regime_result.get("data", {}),
                "theses": top_theses[:3],
            },
            "sources": ["agent_theses"],
            "confidence": max(float(regime_result.get("confidence", 0.0) or 0.0), 0.75),
        }

    def _handle_explain_current_risk(self, q: str) -> Dict:
        conn = self._db()
        try:
            high_risk = conn.execute(
                """
                SELECT thesis_key, confidence, terminal_risk, watchlist_suggestion, status
                FROM agent_theses
                WHERE status != 'superseded'
                  AND UPPER(COALESCE(terminal_risk, '')) LIKE '%HIGH%'
                ORDER BY confidence DESC, evidence_count DESC
                LIMIT 5
                """
            ).fetchall()
            pending_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM agent_actions WHERE status IN ('pending', 'proposed')"
            ).fetchone()
        finally:
            conn.close()

        risks = [dict(row) for row in high_risk]
        pending_count = int((pending_row["cnt"] if pending_row else 0) or 0)
        if risks:
            bullet_lines = [
                f"- {item['thesis_key'][:110]} ({round(float(item.get('confidence', 0.0) or 0.0) * 100)}%)"
                for item in risks
            ]
            answer = "Current elevated risks:\n" + "\n".join(bullet_lines)
            answer += f"\n\nPending actions needing review: {pending_count}."
        else:
            answer = "No HIGH-risk theses are active right now. Current conditions look comparatively calm."

        return {
            "answer": answer,
            "data": {"high_risk_theses": risks, "pending_actions": pending_count},
            "sources": ["agent_theses", "agent_actions"],
            "confidence": 0.9,
        }

    def _handle_show_top_theses(self, q: str) -> Dict:
        conn = self._db()
        try:
            rows = conn.execute(
                """
                SELECT thesis_key, current_claim, confidence, status, terminal_risk,
                       timeframe, evidence_count, confidence_velocity, watchlist_suggestion
                FROM agent_theses
                WHERE status != 'superseded'
                ORDER BY confidence DESC, evidence_count DESC, last_updated_at DESC
                LIMIT 8
                """
            ).fetchall()
        finally:
            conn.close()

        theses = [dict(row) for row in rows]
        lines = [
            f"{idx + 1}. [{round(float(item.get('confidence', 0.0) or 0.0) * 100)}%] {item.get('thesis_key', '')[:120]}"
            for idx, item in enumerate(theses)
        ]
        answer = "Top theses by confidence:\n" + "\n".join(lines) if lines else "No theses available yet."
        return {
            "answer": answer,
            "data": {"theses": theses},
            "sources": ["agent_theses"],
            "confidence": 0.95 if theses else 0.1,
        }

    def _handle_show_summary(self, q: str) -> Dict:
        conn = self._db()
        try:
            thesis_count = int(
                conn.execute("SELECT COUNT(*) FROM agent_theses WHERE status != 'superseded'").fetchone()[0] or 0
            )
            article_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM ingested_articles WHERE COALESCE(fetched_at, '') >= datetime('now', '-24 hours')"
                ).fetchone()[0]
                or 0
            )
            pending_actions = int(
                conn.execute("SELECT COUNT(*) FROM agent_actions WHERE status IN ('pending', 'proposed')").fetchone()[0] or 0
            )
            last_run = conn.execute(
                "SELECT created_at FROM agent_journal ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()

        last_run_at = str(last_run[0] if last_run else "never")[:19].replace("T", " ")
        return {
            "answer": (
                f"GeoClaw currently tracks {thesis_count} active theses, has processed {article_count} articles in the last 24 hours, "
                f"and has {pending_actions} pending actions. The last recorded agent journal entry was at {last_run_at}."
            ),
            "data": {
                "thesis_count": thesis_count,
                "article_count": article_count,
                "pending_actions": pending_actions,
                "last_run_at": last_run_at,
            },
            "sources": ["agent_theses", "ingested_articles", "agent_actions", "agent_journal"],
            "confidence": 1.0,
        }

    def _handle_show_regime(self, q: str) -> Dict:
        try:
            from services.pattern_detector import PatternDetector
            conn = self._db()
            try:
                theses = [dict(row) for row in conn.execute("SELECT * FROM agent_theses WHERE status != 'superseded'").fetchall()]
            finally:
                conn.close()
            regime = PatternDetector().compute_market_regime(theses)
            return {
                "answer": f"Current market regime: {regime.get('regime', 'UNKNOWN')}. {regime.get('description', '')}".strip(),
                "data": regime,
                "sources": ["agent_theses"],
                "confidence": 0.85,
            }
        except Exception as exc:
            return {
                "answer": f"Could not compute the market regime: {exc}",
                "data": {},
                "sources": [],
                "confidence": 0.1,
            }

    def _handle_generic(self, q: str) -> Dict:
        words = [word for word in re.findall(r"[a-z0-9]{4,}", q) if word not in {"what", "show", "with", "that", "this"}]
        conn = self._db()
        try:
            results = []
            for word in words[:4]:
                # ``word`` comes from a user-supplied question; escape LIKE
                # wildcards on the untrusted side.
                like = f"%{escape_like(word)}%"
                rows = conn.execute(
                    r"""
                    SELECT thesis_key, current_claim, confidence, status, terminal_risk
                    FROM agent_theses
                    WHERE status != 'superseded'
                      AND (thesis_key LIKE ? ESCAPE '\' OR current_claim LIKE ? ESCAPE '\' OR title LIKE ? ESCAPE '\')
                    ORDER BY confidence DESC, evidence_count DESC
                    LIMIT 3
                    """,
                    (like, like, like),
                ).fetchall()
                results.extend(dict(row) for row in rows)
        finally:
            conn.close()

        if results:
            deduped = self._dedupe_dicts(results, "thesis_key")
            top = max(deduped, key=lambda item: float(item.get("confidence", 0.0) or 0.0))
            answer = (
                f"The strongest matching thesis is {top.get('thesis_key', '')[:170]} "
                f"at {round(float(top.get('confidence', 0.0) or 0.0) * 100)}% confidence."
            )
            return {
                "answer": answer,
                "data": {"results": deduped[:5]},
                "sources": ["agent_theses"],
                "confidence": 0.55,
            }

        return {
            "answer": "No strong matching thesis was found. Try asking about oil, market regime, contradictions, or top theses.",
            "data": {},
            "sources": [],
            "confidence": 0.1,
        }

    def _handle_explain_country(self, q: str) -> Dict:
        countries = [
            "iran",
            "china",
            "russia",
            "ukraine",
            "israel",
            "saudi",
            "turkey",
            "india",
            "japan",
            "germany",
            "france",
            "uk",
            "usa",
        ]
        country = next((item for item in countries if item in q), "unknown")
        conn = self._db()
        try:
            theses = conn.execute(
                """
                SELECT thesis_key, current_claim, confidence, status, terminal_risk, watchlist_suggestion
                FROM agent_theses
                WHERE status != 'superseded'
                  AND (thesis_key LIKE ? OR current_claim LIKE ? OR title LIKE ?)
                ORDER BY confidence DESC, evidence_count DESC
                LIMIT 5
                """,
                (f"%{country}%", f"%{country}%", f"%{country}%"),
            ).fetchall()
            articles = conn.execute(
                """
                SELECT headline, source_name, published_at, fetched_at, url
                FROM ingested_articles
                WHERE headline LIKE ? OR summary LIKE ?
                ORDER BY COALESCE(published_at, fetched_at, '') DESC, id DESC
                LIMIT 5
                """,
                (f"%{country}%", f"%{country}%"),
            ).fetchall()
        finally:
            conn.close()

        thesis_list = [dict(row) for row in theses]
        article_list = [dict(row) for row in articles]
        if thesis_list:
            top = thesis_list[0]
            answer = (
                f"On {country.title()}, the main active thesis is {top.get('thesis_key', '')[:170]} "
                f"at {round(float(top.get('confidence', 0.0) or 0.0) * 100)}% confidence "
                f"with {top.get('terminal_risk', 'unknown')} terminal risk."
            )
            confidence = float(top.get("confidence", 0.0) or 0.0)
        else:
            answer = f"I do not currently see an active thesis mentioning {country.title()}."
            confidence = 0.3
        return {
            "answer": answer,
            "data": {"theses": thesis_list, "articles": article_list, "country": country},
            "sources": ["agent_theses", "ingested_articles"],
            "confidence": confidence,
        }

    def _handle_show_contradictions(self, q: str) -> Dict:
        conn = self._db()
        try:
            if self._table_exists(conn, "contradictions"):
                rows = conn.execute(
                    """
                    SELECT thesis_key, explanation, severity, created_at
                    FROM contradictions
                    WHERE COALESCE(resolved, 0) = 0
                    ORDER BY created_at DESC, id DESC
                    LIMIT 5
                    """
                ).fetchall()
                sources = ["contradictions"]
            else:
                rows = conn.execute(
                    """
                    SELECT title AS thesis_key, body AS explanation, alert_type AS severity, created_at
                    FROM alert_events
                    WHERE COALESCE(resolved, 0) = 0
                      AND UPPER(COALESCE(status, '')) LIKE '%CONTRADICTION%'
                    ORDER BY created_at DESC, id DESC
                    LIMIT 5
                    """
                ).fetchall()
                sources = ["alert_events"]
        finally:
            conn.close()

        items = [dict(row) for row in rows]
        if items:
            lines = [
                f"- [{item.get('severity', 'unknown')}] {str(item.get('thesis_key', '') or '')[:90]}: {str(item.get('explanation', '') or '')[:100]}"
                for item in items
            ]
            answer = f"{len(items)} active contradictions:\n" + "\n".join(lines)
        else:
            answer = "No active contradictions are currently recorded."
        return {
            "answer": answer,
            "data": {"contradictions": items},
            "sources": sources,
            "confidence": 0.95,
        }

    def _handle_explain_watchlist(self, q: str) -> Dict:
        return self._handle_show_watchlist(q)

    def _handle_show_watchlist(self, q: str) -> Dict:
        conn = self._db()
        try:
            rows = conn.execute(
                """
                SELECT thesis_key, watchlist_suggestion, confidence, terminal_risk
                FROM agent_theses
                WHERE status != 'superseded'
                  AND COALESCE(TRIM(watchlist_suggestion), '') != ''
                ORDER BY confidence DESC, evidence_count DESC
                LIMIT 8
                """
            ).fetchall()
        finally:
            conn.close()

        items = [dict(row) for row in rows]
        lines = [
            f"- {item.get('watchlist_suggestion', '')} ← {str(item.get('thesis_key', '') or '')[:80]}"
            for item in items
        ]
        return {
            "answer": "What to watch next:\n" + "\n".join(lines) if lines else "No watchlist suggestions have been generated yet.",
            "data": {"items": items},
            "sources": ["agent_theses"],
            "confidence": 0.9 if items else 0.4,
        }

    def _handle_show_calibration(self, q: str) -> Dict:
        try:
            from services.self_calibrator import SelfCalibrator

            result = SelfCalibrator().evaluate_past_theses(self.db_path)
            accuracy = float(result.get("accuracy_pct", 0.0) or 0.0)
            answer = (
                f"Agent calibration over the last 24 hours is {accuracy:.1f}%. "
                f"Verified: {int(result.get('verified', 0) or 0)}, "
                f"refuted: {int(result.get('refuted', 0) or 0)}, "
                f"unknown: {int(result.get('unknown', 0) or 0)}."
            )
            return {
                "answer": answer,
                "data": result,
                "sources": ["agent_theses"],
                "confidence": 0.85,
            }
        except Exception as exc:
            return {
                "answer": f"Calibration data is unavailable right now: {exc}",
                "data": {},
                "sources": [],
                "confidence": 0.1,
            }

    def _handle_show_pending_actions(self, q: str) -> Dict:
        conn = self._db()
        try:
            rows = conn.execute(
                """
                SELECT id, action_type, thesis_key, status, created_at, payload_json, audit_note
                FROM agent_actions
                WHERE status IN ('pending', 'proposed')
                ORDER BY created_at DESC, id DESC
                LIMIT 10
                """
            ).fetchall()
        finally:
            conn.close()

        actions = []
        for row in rows:
            item = dict(row)
            reason = str(item.get("audit_note", "") or "")
            if not reason and item.get("payload_json"):
                reason = str(item.get("payload_json", ""))[:160]
            item["reason"] = reason
            actions.append(item)

        if actions:
            lines = [f"- [{item.get('action_type', '')}] {item.get('reason', '')[:110]}" for item in actions]
            answer = f"{len(actions)} pending actions:\n" + "\n".join(lines)
        else:
            answer = "There are no pending actions right now."
        return {
            "answer": answer,
            "data": {"actions": actions},
            "sources": ["agent_actions"],
            "confidence": 0.95,
        }

    def _handle_show_confirmed_theses(self, q: str) -> Dict:
        conn = self._db()
        try:
            rows = conn.execute(
                """
                SELECT thesis_key, confidence, evidence_count, watchlist_suggestion, terminal_risk
                FROM agent_theses
                WHERE status = 'confirmed'
                ORDER BY confidence DESC, evidence_count DESC
                LIMIT 10
                """
            ).fetchall()
        finally:
            conn.close()

        theses = [dict(row) for row in rows]
        if theses:
            lines = [
                f"- [{round(float(item.get('confidence', 0.0) or 0.0) * 100)}%] {item.get('thesis_key', '')[:120]}"
                for item in theses
            ]
            answer = f"{len(theses)} confirmed theses:\n" + "\n".join(lines)
        else:
            answer = "No confirmed theses are active yet."
        return {
            "answer": answer,
            "data": {"theses": theses},
            "sources": ["agent_theses"],
            "confidence": 0.95 if theses else 0.4,
        }

    def _handle_show_recent_articles(self, q: str) -> Dict:
        conn = self._db()
        try:
            article_columns = set(self._columns(conn, "ingested_articles"))
            selected_columns = [
                "headline",
                "source_name",
                "published_at",
                "fetched_at",
                "url",
            ]
            if "sentiment_label" in article_columns:
                selected_columns.append("sentiment_label")
            if "relevance_score" in article_columns:
                selected_columns.append("relevance_score")
            rows = conn.execute(
                f"""
                SELECT {", ".join(selected_columns)}
                FROM ingested_articles
                ORDER BY COALESCE(fetched_at, published_at, '') DESC, id DESC
                LIMIT 10
                """
            ).fetchall()
        finally:
            conn.close()

        articles = [dict(row) for row in rows]
        lines = [f"- [{item.get('source_name', '?')}] {str(item.get('headline', '') or '')[:110]}" for item in articles]
        return {
            "answer": f"Latest {len(articles)} articles:\n" + "\n".join(lines) if articles else "No articles are available yet.",
            "data": {"articles": articles},
            "sources": ["ingested_articles"],
            "confidence": 1.0 if articles else 0.2,
        }

    def _handle_explain_high_confidence(self, q: str) -> Dict:
        conn = self._db()
        try:
            rows = conn.execute(
                """
                SELECT thesis_key, confidence, evidence_count, last_update_reason, terminal_risk
                FROM agent_theses
                WHERE confidence >= 0.75
                  AND status != 'superseded'
                ORDER BY confidence DESC, evidence_count DESC
                LIMIT 5
                """
            ).fetchall()
        finally:
            conn.close()

        theses = [dict(row) for row in rows]
        if theses:
            top = theses[0]
            answer = (
                f"The strongest live thesis is {top.get('thesis_key', '')[:160]} at "
                f"{round(float(top.get('confidence', 0.0) or 0.0) * 100)}% confidence, supported by "
                f"{int(top.get('evidence_count', 0) or 0)} evidence items. "
                f"Latest reason: {str(top.get('last_update_reason', '') or '')[:140]}"
            )
        else:
            answer = "There are no high-confidence theses right now."
        return {
            "answer": answer,
            "data": {"theses": theses},
            "sources": ["agent_theses"],
            "confidence": 0.9 if theses else 0.4,
        }

    def _handle_explain_low_confidence(self, q: str) -> Dict:
        conn = self._db()
        try:
            rows = conn.execute(
                """
                SELECT thesis_key, confidence, status, confidence_velocity
                FROM agent_theses
                WHERE confidence < 0.40
                  AND status IN ('active', 'weakened', 'tracking')
                ORDER BY confidence ASC, last_updated_at DESC
                LIMIT 5
                """
            ).fetchall()
        finally:
            conn.close()

        theses = [dict(row) for row in rows]
        if theses:
            lines = [
                f"- [{round(float(item.get('confidence', 0.0) or 0.0) * 100)}%] {item.get('thesis_key', '')[:100]}"
                for item in theses
            ]
            answer = f"{len(theses)} weakening theses:\n" + "\n".join(lines)
            answer += "\nThese are vulnerable to being weakened further or superseded if no new confirming evidence arrives."
        else:
            answer = "Current active theses are not showing materially weak confidence."
        return {
            "answer": answer,
            "data": {"theses": theses},
            "sources": ["agent_theses"],
            "confidence": 0.9,
        }

    def _suggest_followups(self, q: str) -> List[str]:
        count = min(3, len(SUGGESTIONS))
        return random.sample(SUGGESTIONS, count)

    def _enhance_with_llm(self, question: str, result: Dict, fallback_card: Dict) -> Dict:
        try:
            if not self.llm.available() or not hasattr(self.llm, "polish_query_answer"):
                return fallback_card
            return self.llm.polish_query_answer(
                question,
                fallback_card,
                self._build_polish_context(result),
            )
        except Exception:
            return fallback_card

    def _build_answer_card(self, question: str, result: Dict) -> Dict:
        direct_answer = str(result.get("answer") or "").strip().split("\n")[0].strip() or "No answer available."
        supporting_points = self._build_grounding_points(result)
        if len(supporting_points) < 2 and result.get("sources"):
            supporting_points.append("Grounded in live database records from " + ", ".join(result.get("sources")[:2]) + ".")
        return {
            "direct_answer": direct_answer,
            "supporting_points": supporting_points[:4] or ["The retrieved records do not yet provide enough corroboration."],
            "follow_up": list(result.get("follow_up") or self._suggest_followups(question.lower()))[:3],
            "caveat": "" if result.get("confidence", 0.0) >= 0.45 else "Confidence is limited because the matching evidence is still thin.",
        }

    def _build_grounding_points(self, result: Dict) -> List[str]:
        data = result.get("data", {}) or {}
        points = []

        theses = data.get("theses") or data.get("high_risk_theses") or data.get("results") or []
        for thesis in theses[:2]:
            thesis_key = str(thesis.get("thesis_key") or thesis.get("current_claim") or "").strip()
            if not thesis_key:
                continue
            confidence = round(float(thesis.get("confidence", 0.0) or 0.0) * 100)
            velocity = float(thesis.get("confidence_velocity", 0.0) or 0.0)
            velocity_text = f"velocity {velocity:+.3f}" if velocity else "velocity stable"
            risk = str(thesis.get("terminal_risk") or thesis.get("status") or "active").strip()
            points.append(f"{thesis_key[:140]} ({confidence}% confidence, {risk}, {velocity_text}).")

        articles = data.get("articles") or data.get("recent_articles") or []
        for article in articles[:2]:
            headline = str(article.get("headline") or "").strip()
            if not headline:
                continue
            source = str(article.get("source_name") or article.get("source") or "Unknown source").strip()
            published = str(article.get("published_at") or article.get("fetched_at") or "").replace("T", " ")[:16]
            points.append(f"Recent article: {headline[:120]} ({source}{' · ' + published if published else ''}).")

        actions = data.get("actions") or []
        if actions:
            top_action = actions[0]
            points.append(
                f"Action queue signal: {top_action.get('action_type', 'action')} for {str(top_action.get('thesis_key', '') or '').strip()[:100]}."
            )

        watch_items = data.get("items") or []
        for item in watch_items[:2]:
            points.append(
                f"Watch next: {str(item.get('watchlist_suggestion') or '').strip()[:80]} from {str(item.get('thesis_key') or '').strip()[:100]}."
            )

        contradictions = data.get("contradictions") or []
        if contradictions:
            top_contradiction = contradictions[0]
            points.append(
                f"Contradiction count is active, led by {str(top_contradiction.get('thesis_key', '') or '').strip()[:100]}."
            )

        regime = data.get("regime") if isinstance(data.get("regime"), dict) else data if {"regime", "description"} <= set(data.keys()) else {}
        if regime:
            points.append(
                f"Market regime is {regime.get('regime', 'UNKNOWN')} with {str(regime.get('description', '') or '').strip()[:140]}."
            )

        pending_actions = data.get("pending_actions")
        if pending_actions not in (None, ""):
            points.append(f"{int(pending_actions or 0)} pending actions are currently queued.")

        calibration_keys = {"accuracy_pct", "verified", "refuted", "unknown"}
        if calibration_keys & set(data.keys()):
            points.append(
                f"Observed agent calibration: {float(data.get('accuracy_pct', 0.0) or 0.0):.1f}% over the last 24 hours."
            )

        deduped = []
        seen = set()
        for point in points:
            key = point.lower()
            if key in seen:
                continue
            deduped.append(point)
            seen.add(key)
        return deduped[:4]

    def _build_polish_context(self, result: Dict) -> str:
        data = result.get("data", {}) or {}
        lines = [f"Original answer: {result.get('answer', '')}"]
        theses = data.get("theses") or data.get("high_risk_theses") or data.get("results") or []
        if theses:
            for thesis in theses[:2]:
                thesis_key = str(thesis.get("thesis_key") or thesis.get("current_claim") or "").strip()
                if not thesis_key:
                    continue
                history = self._thesis_history(thesis_key)
                contradiction_count = self._thesis_contradiction_count(thesis_key)
                lines.append(
                    f"Thesis: {thesis_key} | confidence={round(float(thesis.get('confidence', 0.0) or 0.0) * 100)}% "
                    f"| velocity={float(thesis.get('confidence_velocity', 0.0) or 0.0):+.3f} "
                    f"| contradictions={contradiction_count} | history={history}"
                )
        articles = data.get("articles") or data.get("recent_articles") or []
        if articles:
            lines.append("Recent articles:")
            for article in articles[:3]:
                source = str(article.get("source_name") or article.get("source") or "Unknown source")
                reliability = self._source_reliability(source)
                lines.append(
                    f"- {str(article.get('headline', '') or '').strip()[:140]} | source={source} | source_reliability={reliability:.2f}"
                )
        prediction_accuracy = self._prediction_accuracy()
        if prediction_accuracy:
            lines.append(
                f"Prediction accuracy: {prediction_accuracy.get('accuracy_pct', 0)}% "
                f"(verified={prediction_accuracy.get('verified', 0)}, refuted={prediction_accuracy.get('refuted', 0)})"
            )
        return "\n".join(lines)

    def _thesis_history(self, thesis_key: str) -> List[int]:
        conn = self._db()
        try:
            if not self._table_exists(conn, "thesis_confidence_log"):
                return []
            rows = conn.execute(
                """
                SELECT confidence
                FROM thesis_confidence_log
                WHERE thesis_key=?
                ORDER BY recorded_at DESC, id DESC
                LIMIT 5
                """,
                (thesis_key,),
            ).fetchall()
            return [round(float(row["confidence"] or 0.0) * 100) for row in rows]
        finally:
            conn.close()

    def _thesis_contradiction_count(self, thesis_key: str) -> int:
        conn = self._db()
        try:
            if not self._table_exists(conn, "contradictions"):
                return 0
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM contradictions
                WHERE thesis_key=? AND COALESCE(resolved, 0)=0
                """,
                (thesis_key,),
            ).fetchone()
            return int((row["cnt"] if row else 0) or 0)
        finally:
            conn.close()

    def _source_reliability(self, source_name: str) -> float:
        conn = self._db()
        try:
            if not self._table_exists(conn, "source_reliability"):
                return 0.65
            row = conn.execute(
                """
                SELECT reliability_score
                FROM source_reliability
                WHERE source_name LIKE ?
                ORDER BY reliability_score DESC
                LIMIT 1
                """,
                (f"%{str(source_name or '').strip()}%",),
            ).fetchone()
            return float((row["reliability_score"] if row else 0.65) or 0.65)
        finally:
            conn.close()

    def _prediction_accuracy(self) -> Dict:
        conn = self._db()
        try:
            if not self._table_exists(conn, "thesis_predictions"):
                return {}
            rows = conn.execute(
                """
                SELECT outcome, COUNT(*) AS cnt
                FROM thesis_predictions
                WHERE outcome IN ('verified', 'refuted', 'neutral')
                GROUP BY outcome
                """
            ).fetchall()
            counts = {str(row["outcome"]): int(row["cnt"] or 0) for row in rows}
            verified = counts.get("verified", 0)
            refuted = counts.get("refuted", 0)
            neutral = counts.get("neutral", 0)
            accuracy = verified / max(verified + refuted, 1) * 100
            return {
                "verified": verified,
                "refuted": refuted,
                "neutral": neutral,
                "accuracy_pct": round(accuracy, 1),
            }
        finally:
            conn.close()

    def _dedupe_rows(self, rows, key_name: str) -> List[Dict]:
        items = [dict(row) for row in rows]
        return self._dedupe_dicts(items, key_name)

    def _dedupe_dicts(self, items: List[Dict], key_name: str) -> List[Dict]:
        seen = set()
        deduped = []
        for item in items:
            key = str(item.get(key_name, "") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped
