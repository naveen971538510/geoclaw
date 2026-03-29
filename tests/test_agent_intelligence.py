import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import migration
import services.action_service as action_service
import services.agent_loop_service as agent_loop_service
import services.agent_state_service as agent_state_service
import services.briefing_service as briefing_service
import services.calibration_service as calibration_service
import services.decision_service as decision_service
import services.db_helpers as db_helpers
import services.goal_service as goal_service
import services.health_service as health_service
import services.llm_service as llm_service
import services.memory_service as memory_service
import services.reasoning_service as reasoning_service
import services.reflection_service as reflection_service
import services.research_agent as research_agent
import services.task_service as task_service
import services.terminal_service as terminal_service
import services.thesis_service as thesis_service
from services.agent_loop_service import _decision_for_card
from services.ingest_service import _cluster_entries


class GeoClawAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_geoclaw.db"

    def tearDown(self):
        self.tmpdir.cleanup()

    def _db_patches(self, extra=None):
        extra = extra or []
        stack = ExitStack()
        modules = [
            (goal_service, "DB_PATH", self.db_path),
            (terminal_service, "DB_PATH", self.db_path),
            (agent_loop_service, "DB_PATH", self.db_path),
            (health_service, "DB_PATH", self.db_path),
        ] + list(extra)
        stack.enter_context(patch.object(migration, "get_conn", lambda: db_helpers.get_conn(self.db_path)))
        for module, attr, value in modules:
            stack.enter_context(patch.object(module, attr, value))
        return stack

    def _insert_article(self, conn, headline="Oil rises", summary="Shipping risk lifts crude.", source_name="reuters", url="https://example.com/a", published_at="2026-03-28T10:00:00+00:00"):
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO ingested_articles (
                source_name, external_id, headline, summary, url, published_at,
                language, country, fetched_at, content_hash, is_duplicate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source_name, "", headline, summary, url, published_at, "en", "", published_at, url[-12:] or "hash", 0),
        )
        return int(cur.lastrowid)

    def _insert_enrichment(
        self,
        conn,
        article_id,
        thesis="",
        cluster_key="",
        why_it_matters="",
        llm_category="other",
        llm_importance="medium",
        impact_score=50,
        confidence_score=0.5,
        created_at="2026-03-28T10:05:00+00:00",
    ):
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO article_enrichment (
                article_id, signal, sentiment_score, impact_score, asset_tags, macro_tags,
                watchlist_hits, alert_tags, thesis, bull_case, bear_case, what_to_watch,
                confidence, created_at, why_it_matters, confidence_score, urgency_level,
                impact_radius, contradicts_narrative, llm_category, llm_importance,
                llm_mode, llm_fallback_reason, cluster_key, cluster_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article_id,
                "Neutral",
                0.0,
                int(impact_score),
                '[]',
                '[]',
                '[]',
                '[]',
                thesis,
                "",
                "",
                "",
                50,
                created_at,
                why_it_matters,
                float(confidence_score),
                "medium",
                "regional",
                0,
                llm_category,
                llm_importance,
                "cluster",
                "",
                cluster_key,
                1,
            ),
        )
        return int(cur.lastrowid)

    def test_migration_idempotency(self):
        with self._db_patches():
            migration.run_migration()
            migration.run_migration()
            conn = sqlite3.connect(str(self.db_path))
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(agent_theses)")
            thesis_columns = {row[1] for row in cur.fetchall()}
            cur.execute("PRAGMA table_info(llm_cache)")
            cache_columns = {row[1] for row in cur.fetchall()}
            cur.execute("PRAGMA table_info(agent_tasks)")
            task_columns = {row[1] for row in cur.fetchall()}
            cur.execute("PRAGMA table_info(agent_actions)")
            action_columns = {row[1] for row in cur.fetchall()}
            conn.close()
        self.assertIn("thesis_key", thesis_columns)
        self.assertIn("analysis_json", cache_columns)
        self.assertIn("closed_reason", task_columns)
        self.assertIn("audit_note", action_columns)

    def test_missing_key_fallback(self):
        run_state = llm_service.new_llm_run_state(per_run_cap=2)
        with patch.object(llm_service, "OPENAI_API_KEY", ""):
            result = llm_service.analyse_cluster_meta(
                [{"headline": "Oil rises on shipping risk", "summary": "Brent climbs as routes tighten.", "source_name": "rss"}],
                cluster_key="cluster:oil|shipping",
                run_state=run_state,
            )
        metrics = llm_service.summarize_llm_run_state(run_state)
        self.assertTrue(result["used_fallback"])
        self.assertEqual(result["fallback_reason"], "missing_key")
        self.assertFalse(result["call_made"])
        self.assertEqual(metrics["llm_calls_made"], 0)
        self.assertEqual(metrics["cache_misses"], 1)

    def test_cluster_dedupe(self):
        entries = [
            {
                "article": {"headline": "Oil jumps after tanker disruption", "summary": "Crude rises as shipping risk grows.", "url": "a"},
                "enrichment": {"asset_tags": ["OIL"], "watchlist_hits": ["oil"], "macro_tags": ["GEOPOLITICS"]},
                "ranking": {},
            },
            {
                "article": {"headline": "Crude rises after tanker disruption", "summary": "Oil market reacts to shipping risk.", "url": "b"},
                "enrichment": {"asset_tags": ["OIL"], "watchlist_hits": ["oil"], "macro_tags": ["GEOPOLITICS"]},
                "ranking": {},
            },
        ]
        clusters = _cluster_entries(entries)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]["items"]), 2)

    def test_decision_dedupe(self):
        with self._db_patches():
            migration.run_migration()
            first = decision_service.create_decision(
                article_id=None,
                decision_type="queue",
                reason="Initial evidence",
                confidence=40,
                priority_score=50,
                state="open",
                cluster_key="cluster:oil",
                thesis_key="oil shipping risk",
            )
            second = decision_service.create_decision(
                article_id=None,
                decision_type="alert",
                reason="Stronger evidence",
                confidence=70,
                priority_score=85,
                state="open",
                cluster_key="cluster:oil",
                thesis_key="oil shipping risk",
            )
            decisions = decision_service.list_decisions(limit=10, open_only=False)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["decision_type"], "alert")

    def test_task_closure(self):
        with self._db_patches():
            migration.run_migration()
            task = task_service.create_task(
                "follow_up",
                "Follow up oil",
                "Check whether oil shock persists.",
                status="open",
                ttl="2000-01-01T00:00:00",
                identity_key="cluster:oil",
            )
            changed = task_service.close_expired_tasks()
            tasks = task_service.list_tasks(limit=5, status=None, task_id=task["id"])
        self.assertEqual(changed, 1)
        self.assertEqual(tasks[0]["status"], "stale")

    def test_contradiction_flow(self):
        card = {
            "article_id": 1,
            "impact_score": 72,
            "confidence": 68,
            "confidence_score": 0.7,
            "watchlist_hits": ["oil"],
            "alert_tags": ["CONTRADICTION"],
            "trust_label": "trusted",
            "is_low_quality": False,
            "asset_tags": ["OIL"],
            "signal": "Bearish",
            "llm_importance": "high",
            "cluster_size": 1,
        }
        thesis_state = {"current_claim": "Oil supply shock is bullish for crude.", "confidence": 0.72}
        result = _decision_for_card(card, ["oil"], thesis_state=thesis_state, contradiction_resolution="contradiction")
        self.assertEqual(result["decision_type"], "downgrade")

    def test_terminal_payload_compatibility(self):
        with self._db_patches(extra=[(terminal_service, "get_latest_market_snapshots", lambda: [])]):
            migration.run_migration()
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO ingested_articles (
                    source_name, external_id, headline, summary, url, published_at,
                    language, country, fetched_at, content_hash, is_duplicate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("rss", "", "Oil rises", "Shipping risk lifts crude.", "https://example.com/oil", "2026-03-28T10:00:00+00:00", "en", "", "2026-03-28T10:05:00+00:00", "abc", 0),
            )
            article_id = int(cur.lastrowid)
            cur.execute(
                """
                INSERT INTO article_enrichment (
                    article_id, signal, sentiment_score, impact_score, asset_tags, macro_tags,
                    watchlist_hits, alert_tags, thesis, bull_case, bear_case, what_to_watch,
                    confidence, why_it_matters, confidence_score, urgency_level, impact_radius,
                    contradicts_narrative, llm_category, llm_importance, llm_mode, llm_fallback_reason,
                    cluster_key, cluster_size, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article_id, "Bullish", 1.0, 64, '["OIL"]', '["GEOPOLITICS"]',
                    '["oil"]', '["OPEC"]', "Oil supply risk is back in focus.", "Bull", "Bear", "Watch shipping",
                    72, "This matters because supply risk is tightening the crude narrative.", 0.81, "high", "global",
                    0, "energy", "high", "cluster", "", "cluster:oil|shipping", 2, "2026-03-28T10:05:00+00:00",
                ),
            )
            conn.commit()
            conn.close()
            payload = terminal_service.get_terminal_payload(limit=5)
        self.assertEqual(len(payload["cards"]), 1)
        card = payload["cards"][0]
        self.assertIn("why_it_matters", card)
        self.assertIn("confidence_score", card)
        self.assertIn("llm_mode", card)
        self.assertIn("cluster_key", card)

    def test_get_thesis_detail_returns_required_fields(self):
        with self._db_patches():
            migration.run_migration()
            conn = goal_service.get_conn()
            article_id = self._insert_article(conn, headline="Oil shock deepens", url="https://example.com/oil-1")
            conn.commit()
            thesis = thesis_service.upsert_thesis(
                thesis_key="Oil supply shock thesis",
                current_claim="Oil supply shock is tightening crude markets.",
                confidence=0.72,
                status="active",
                evidence_delta=2,
                last_article_id=article_id,
                notes="Operator thesis",
                last_update_reason="Initial thesis formation",
            )
            decision = decision_service.create_decision(
                article_id=article_id,
                decision_type="alert",
                reason="Strong corroboration",
                confidence=74,
                priority_score=88,
                thesis_key=thesis["thesis_key"],
            )
            memory_service.write_memory(
                article_id=article_id,
                memory_type="thesis",
                thesis=thesis["current_claim"],
                confidence=74,
                status="active",
                linked_decision_id=decision["id"],
                thesis_key=thesis["thesis_key"],
            )
            task_service.create_task(
                "follow_up",
                "Follow up oil thesis",
                "Check whether the shipping disruption persists.",
                thesis_key=thesis["thesis_key"],
                related_article_id=article_id,
            )
            detail = thesis_service.get_thesis_detail(thesis["thesis_key"])
            conn.close()
        for field in (
            "current_claim",
            "confidence",
            "status",
            "evidence_count",
            "contradiction_count",
            "last_updated_at",
            "last_update_reason",
            "linked_articles",
            "linked_decisions",
            "linked_tasks",
        ):
            self.assertIn(field, detail)
        self.assertEqual(detail["current_claim"], "Oil supply shock is tightening crude markets.")
        self.assertTrue(detail["linked_articles"])
        self.assertTrue(detail["linked_decisions"])
        self.assertTrue(detail["linked_tasks"])
        self.assertIn("title", detail)
        self.assertIn("bull_case", detail)
        self.assertIn("bear_case", detail)
        self.assertIn("key_risk", detail)
        self.assertIn("watch_for_next", detail)

    def test_propose_action_saves_proposed_without_execution(self):
        with self._db_patches():
            migration.run_migration()
            thesis = thesis_service.upsert_thesis(
                thesis_key="Rates drift lower",
                current_claim="Rates are drifting lower as growth softens.",
                confidence=0.79,
                status="tracking",
                evidence_delta=2,
            )
            action = action_service.propose_action(
                action_type="email_summary",
                payload={},
                thesis_key=thesis["thesis_key"],
                confidence=0.79,
                evidence_count=2,
                triggered_by="test",
            )
        self.assertEqual(action["status"], "proposed")
        self.assertEqual(action["executed_at"], "")
        self.assertIn("approval required", action["audit_note"])

    def test_approve_action_sets_status_and_audit_note(self):
        with self._db_patches():
            migration.run_migration()
            thesis = thesis_service.upsert_thesis(
                thesis_key="Gold safe haven bid",
                current_claim="Gold has a safe-haven bid.",
                confidence=0.65,
                status="tracking",
                evidence_delta=2,
            )
            action = action_service.propose_action(
                action_type="slack_payload",
                payload={},
                thesis_key=thesis["thesis_key"],
                confidence=0.65,
                evidence_count=2,
                triggered_by="test",
            )
            approved = action_service.approve_action(action["id"], "operator")
        self.assertEqual(approved["status"], "approved")
        self.assertIn("Approved by operator", approved["audit_note"])
        self.assertTrue(approved["reviewed_at"])

    def test_reject_action_sets_status(self):
        with self._db_patches():
            migration.run_migration()
            thesis = thesis_service.upsert_thesis(
                thesis_key="FX range trade",
                current_claim="FX remains range-bound.",
                confidence=0.61,
                status="tracking",
                evidence_delta=1,
            )
            action = action_service.propose_action(
                action_type="webhook",
                payload={},
                thesis_key=thesis["thesis_key"],
                confidence=0.61,
                evidence_count=1,
                triggered_by="test",
            )
            rejected = action_service.reject_action(action["id"], "Not enough confirmation")
        self.assertEqual(rejected["status"], "rejected")
        self.assertIn("Not enough confirmation", rejected["audit_note"])

    def test_can_auto_approve_threshold(self):
        self.assertFalse(action_service.can_auto_approve(0.79, 3))
        self.assertFalse(action_service.can_auto_approve(0.80, 2))
        self.assertTrue(action_service.can_auto_approve(0.80, 3))
        self.assertTrue(action_service.can_auto_approve(0.95, 6))

    def test_update_thesis_confidence_source_weight_known_vs_unknown(self):
        with self._db_patches():
            migration.run_migration()
            conn = goal_service.get_conn()
            recent_article = self._insert_article(conn, source_name="reuters", url="https://example.com/recent", published_at="2999-03-28T10:00:00+00:00")
            recent_unknown = self._insert_article(conn, source_name="mysterywire", url="https://example.com/unknown", published_at="2999-03-28T10:00:00+00:00")
            conn.commit()
            thesis_service.upsert_thesis("Known source thesis", "Known source claim", confidence=0.50, last_article_id=recent_article)
            thesis_service.upsert_thesis("Unknown source thesis", "Unknown source claim", confidence=0.50, last_article_id=recent_unknown)
            known = thesis_service.update_thesis_confidence("Known source thesis", "reuters", 0.8)
            unknown = thesis_service.update_thesis_confidence("Unknown source thesis", "mysterywire", 0.8)
            conn.close()
        self.assertGreater(known["confidence"], unknown["confidence"])
        self.assertIn("weight 0.88", known["last_update_reason"])
        self.assertIn("weight 0.45", unknown["last_update_reason"])

    def test_duplicate_source_penalty_after_three_contributions(self):
        with self._db_patches():
            migration.run_migration()
            conn = goal_service.get_conn()
            article_ids = []
            for idx in range(3):
                article_ids.append(
                    self._insert_article(
                        conn,
                        headline=f"Oil evidence {idx}",
                        source_name="reuters",
                        url=f"https://example.com/dup-{idx}",
                        published_at="2999-03-28T10:00:00+00:00",
                    )
                )
            conn.commit()
            thesis = thesis_service.upsert_thesis(
                "Duplicate source thesis",
                "Oil disruption continues.",
                confidence=0.50,
                last_article_id=article_ids[-1],
            )
            for article_id in article_ids:
                memory_service.write_memory(
                    article_id=article_id,
                    memory_type="thesis",
                    thesis=thesis["current_claim"],
                    confidence=70,
                    status="active",
                    thesis_key=thesis["thesis_key"],
                )
            updated = thesis_service.update_thesis_confidence(thesis["thesis_key"], "reuters", 0.8)
            conn.close()
        self.assertIn("duplicate penalty 0.40", updated["last_update_reason"])
        self.assertLess(updated["confidence"], 0.50 * 0.6 + (0.8 * 0.88 * 1.0 * 0.4))

    def test_recency_weight_recent_vs_old(self):
        with self._db_patches():
            migration.run_migration()
            conn = goal_service.get_conn()
            recent_article = self._insert_article(conn, headline="Recent rates item", source_name="bbc", url="https://example.com/recent-rates", published_at="2999-03-28T10:00:00+00:00")
            old_article = self._insert_article(conn, headline="Old rates item", source_name="bbc", url="https://example.com/old-rates", published_at="2000-03-28T10:00:00+00:00")
            conn.commit()
            thesis_service.upsert_thesis("Recent rates thesis", "Rates easing", confidence=0.50, last_article_id=recent_article)
            thesis_service.upsert_thesis("Old rates thesis", "Rates easing", confidence=0.50, last_article_id=old_article)
            recent = thesis_service.update_thesis_confidence("Recent rates thesis", "bbc", 0.8)
            old = thesis_service.update_thesis_confidence("Old rates thesis", "bbc", 0.8)
            conn.close()
        self.assertGreater(recent["confidence"], old["confidence"])
        self.assertIn("recency 1.00", recent["last_update_reason"])
        self.assertIn("recency 0.50", old["last_update_reason"])

    def test_build_thesis_claim_missing_key_fallback_has_required_fields(self):
        with patch.object(llm_service, "OPENAI_API_KEY", ""):
            result = thesis_service.build_thesis_claim(
                "OPEC extends supply cuts into summer",
                ["Brent holds gains after OPEC move"],
                "reuters",
                "energy",
            )
        for field in ("title", "current_claim", "bull_case", "bear_case", "key_risk", "watch_for_next"):
            self.assertIn(field, result)
        self.assertEqual(result["current_claim"], "OPEC extends supply cuts into summer")

    def test_build_thesis_claim_fallback_title_derived_from_headline(self):
        headline = "Fed signals higher-for-longer rates into late 2026"
        with patch.object(llm_service, "OPENAI_API_KEY", ""):
            result = thesis_service.build_thesis_claim(headline, [], "bbc", "markets")
        self.assertTrue(result["title"].lower().startswith("fed signals higher-for-longer"))
        self.assertNotEqual(result["title"], "Story thread")

    def test_evaluate_and_propose_creates_alert_proposal(self):
        with self._db_patches():
            migration.run_migration()
            thesis = thesis_service.upsert_thesis(
                thesis_key="Oil convoy risk thesis",
                current_claim="Shipping disruptions are tightening oil supply expectations.",
                confidence=0.76,
                status="active",
                evidence_delta=3,
                category="energy",
            )
            action = action_service.evaluate_and_propose(thesis, [])
        self.assertEqual(action["action_type"], "alert")
        self.assertEqual(action["status"], "proposed")

    def test_evaluate_and_propose_does_not_duplicate_same_thesis_action(self):
        with self._db_patches():
            migration.run_migration()
            thesis = thesis_service.upsert_thesis(
                thesis_key="Rates break lower",
                current_claim="Rates are breaking lower on softer growth expectations.",
                confidence=0.77,
                status="active",
                evidence_delta=4,
                category="markets",
            )
            with patch.object(action_service, "action_on_cooldown", return_value={"blocked": False, "remaining_seconds": 0, "action_id": 0}):
                first = action_service.evaluate_and_propose(thesis, [])
                second = action_service.evaluate_and_propose(thesis, [])
            actions = action_service.list_actions(limit=10)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(actions), 1)

    def test_action_policy_blocks_low_confidence(self):
        with self._db_patches():
            migration.run_migration()
            thesis = thesis_service.upsert_thesis(
                thesis_key="Weak tariff rumor",
                current_claim="A low-confidence tariff rumor is circulating.",
                confidence=0.39,
                status="active",
                evidence_delta=2,
            )
            action = action_service.propose_action(
                action_type="alert",
                payload={},
                thesis_key=thesis["thesis_key"],
                confidence=0.39,
                evidence_count=2,
                triggered_by="test",
            )
        self.assertEqual(action["status"], "draft")
        self.assertIn("blocked by policy: low confidence", action["audit_note"])

    def test_action_policy_auto_approves_high_confidence(self):
        with self._db_patches():
            migration.run_migration()
            thesis = thesis_service.upsert_thesis(
                thesis_key="High confidence oil squeeze",
                current_claim="Oil supply constraints are tightening rapidly.",
                confidence=0.84,
                status="active",
                evidence_delta=4,
            )
            with patch.object(action_service, "ALLOW_AUTO_APPROVED_ACTIONS", True):
                action = action_service.propose_action(
                    action_type="alert",
                    payload={},
                    thesis_key=thesis["thesis_key"],
                    confidence=0.84,
                    evidence_count=4,
                    triggered_by="test",
                )
        self.assertEqual(action["status"], "auto_approved")
        self.assertIn("auto-approved by policy", action["audit_note"])

    def test_record_thesis_event_inserts_row(self):
        with self._db_patches():
            migration.run_migration()
            thesis = thesis_service.upsert_thesis("Oil event thesis", "Oil is rising", confidence=0.6)
            event = thesis_service.record_thesis_event(thesis["thesis_key"], "updated", "Operator note", 0.6, 2)
        self.assertEqual(event["event_type"], "updated")
        self.assertEqual(event["note"], "Operator note")

    def test_get_thesis_timeline_returns_chronological_order(self):
        with self._db_patches():
            migration.run_migration()
            thesis = thesis_service.upsert_thesis("Timeline thesis", "Initial claim", confidence=0.5)
            thesis_service.record_thesis_event(thesis["thesis_key"], "updated", "Second", 0.6, 2)
            timeline = thesis_service.get_thesis_timeline(thesis["thesis_key"])
        self.assertGreaterEqual(len(timeline), 2)
        self.assertEqual(timeline[0]["event_type"], "created")
        self.assertEqual(timeline[-1]["event_type"], "updated")

    def test_action_approved_event_recorded(self):
        with self._db_patches():
            migration.run_migration()
            thesis = thesis_service.upsert_thesis("Approve event thesis", "Claim", confidence=0.65, evidence_delta=2)
            action = action_service.propose_action("slack_payload", {}, thesis["thesis_key"], 0.65, 2, "test")
            action_service.approve_action(action["id"], "operator")
            timeline = thesis_service.get_thesis_timeline(thesis["thesis_key"])
        self.assertTrue(any(item["event_type"] == "action_approved" for item in timeline))

    def test_action_rejected_event_recorded(self):
        with self._db_patches():
            migration.run_migration()
            thesis = thesis_service.upsert_thesis("Reject event thesis", "Claim", confidence=0.65, evidence_delta=2)
            action = action_service.propose_action("slack_payload", {}, thesis["thesis_key"], 0.65, 2, "test")
            action_service.reject_action(action["id"], "No send")
            timeline = thesis_service.get_thesis_timeline(thesis["thesis_key"])
        self.assertTrue(any(item["event_type"] == "action_rejected" for item in timeline))

    def test_research_thesis_generates_three_queries_in_fallback(self):
        with self._db_patches():
            migration.run_migration()
            with patch.object(llm_service, "OPENAI_API_KEY", ""), patch.object(research_agent, "_search_live_articles", return_value=[]):
                result = research_agent.research_thesis("oil thesis", "Oil supply is tightening", "energy")
        self.assertEqual(len(result["queries"]), 3)
        self.assertEqual(result["metrics"]["research_agent_runs"], 1)

    def test_research_thesis_handles_no_articles_found(self):
        with self._db_patches():
            migration.run_migration()
            with patch.object(llm_service, "OPENAI_API_KEY", ""), patch.object(research_agent, "_search_live_articles", return_value=[]), patch.object(research_agent, "_search_local_articles", return_value=[]):
                result = research_agent.research_thesis("empty thesis", "Sparse evidence", "other")
        self.assertEqual(result["articles_found"], 0)
        self.assertEqual(result["support_count"], 0)
        self.assertEqual(result["contradict_count"], 0)

    def test_run_reflection_handles_empty_decisions(self):
        with self._db_patches():
            migration.run_migration()
            metrics = reflection_service.run_reflection()
        self.assertEqual(metrics["decisions_reviewed"], 0)
        self.assertEqual(metrics["lessons_recorded"], 0)

    def test_reflection_records_lesson_for_wrong_decision(self):
        with self._db_patches():
            migration.run_migration()
            conn = goal_service.get_conn()
            article_id = self._insert_article(
                conn,
                headline="Oil downgrade deepens on weak demand",
                summary="Demand weakens sharply.",
                source_name="rss",
                url="https://example.com/reflection-1",
                published_at="2026-03-28T10:00:00+00:00",
            )
            self._insert_enrichment(
                conn,
                article_id,
                thesis="reflection thesis",
                cluster_key="reflection thesis",
                llm_category="energy",
                llm_importance="high",
            )
            conn.commit()
            decision = decision_service.create_decision(
                article_id=article_id,
                decision_type="alert",
                reason="Initial bullish call",
                confidence=90,
                priority_score=90,
                state="open",
                thesis_key="reflection thesis",
            )
            cur = conn.cursor()
            cur.execute(
                "UPDATE agent_decisions SET created_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", int(decision["id"])),
            )
            conn.commit()
            with patch.object(llm_service, "OPENAI_API_KEY", ""):
                metrics = reflection_service.run_reflection(conn)
            cur.execute("SELECT COUNT(*) FROM agent_lessons")
            lesson_count = int(cur.fetchone()[0] or 0)
            conn.close()
        self.assertEqual(metrics["decisions_reviewed"], 1)
        self.assertEqual(lesson_count, 1)

    def test_generate_autonomous_goals_avoids_duplicate_titles(self):
        with self._db_patches():
            migration.run_migration()
            thesis_service.upsert_thesis(
                "Auto goal thesis",
                "Oil shipping risks are rising.",
                confidence=0.75,
                status="active",
                evidence_delta=3,
                title="Oil shipping risk",
            )
            fake_payload = {
                "analysis": {
                    "goals": [
                        {
                            "title": "Track tanker insurance costs",
                            "description": "Monitor whether tanker insurance premiums are rising.",
                            "priority": "high",
                            "thesis_key": "auto goal thesis",
                            "success_criteria": "Find two independent updates on tanker insurance pricing.",
                        }
                    ]
                }
            }
            with patch.object(llm_service, "analyse_custom_json", return_value=fake_payload):
                first = goal_service.generate_autonomous_goals()
                second = goal_service.generate_autonomous_goals()
                goals = goal_service.list_goals(active_only=False)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)
        self.assertEqual(len([goal for goal in goals if goal["name"] == "Track tanker insurance costs"]), 1)

    def test_build_reasoning_chain_returns_required_fields_in_fallback(self):
        with self._db_patches():
            migration.run_migration()
            with patch.object(llm_service, "OPENAI_API_KEY", ""):
                result = reasoning_service.build_reasoning_chain("Fed raises rates", "markets")
        self.assertIn("chain", result)
        self.assertIn("terminal_risk", result)
        self.assertIn("watchlist_suggestion", result)
        self.assertTrue(result["chain"])

    def test_generate_daily_briefing_runs_when_tables_empty(self):
        with self._db_patches():
            migration.run_migration()
            with patch.object(llm_service, "OPENAI_API_KEY", ""):
                briefing = briefing_service.generate_daily_briefing()
        self.assertIn("briefing_text", briefing)
        self.assertTrue(briefing["briefing_text"])

    def test_record_prediction_stores_row_correctly(self):
        with self._db_patches():
            migration.run_migration()
            row = calibration_service.record_prediction("oil thesis", 0.72, "Bullish", "reuters", "energy")
        self.assertEqual(row["thesis_key"], "oil thesis")
        self.assertEqual(row["row_type"], "prediction")
        self.assertEqual(row["source_name"], "reuters")

    def test_get_calibration_score_returns_f_when_accuracy_below_point_four(self):
        with self._db_patches():
            migration.run_migration()
            for index in range(5):
                calibration_service.record_prediction("fx thesis", 0.8, "Bullish", "rss", "markets")
                calibration_service.record_outcome("fx thesis", "confirmed" if index == 0 else "contradicted")
            score = calibration_service.get_calibration_score("rss", "markets")
        self.assertEqual(score["calibration_grade"], "F")
        self.assertLess(score["accuracy"], 0.40)

    def test_calibration_penalty_fires_after_three_wrong_predictions(self):
        with self._db_patches():
            migration.run_migration()
            conn = goal_service.get_conn()
            cur = conn.cursor()
            for _ in range(4):
                cur.execute(
                    """
                    INSERT INTO agent_calibration (
                        source_name, category, over_confident, count, created_at, thesis_key,
                        decision_id, verdict, lesson, confidence_delta, row_type
                    )
                    VALUES (?, ?, 1, 1, ?, ?, ?, ?, ?, ?, 'reflection')
                    """,
                    ("rss", "energy", "2026-03-28T10:00:00+00:00", "oil thesis", 1, "wrong", "Too confident", 0.25),
                )
            conn.commit()
            conn.close()
            card = {
                "impact_score": 70,
                "confidence": 70,
                "confidence_score": 0.7,
                "watchlist_hits": ["oil"],
                "alert_tags": [],
                "trust_label": "trusted",
                "signal": "Bullish",
                "llm_importance": "high",
                "cluster_size": 1,
                "source": "rss",
                "llm_category": "energy",
            }
            with patch.object(agent_loop_service, "get_penalty_multiplier", return_value=1.0):
                baseline = agent_loop_service._relevance_score(card, [])
            penalized = agent_loop_service._relevance_score(card, [])
        self.assertLess(penalized, baseline)

    def test_terminal_summary_and_diff_helpers(self):
        with self._db_patches():
            migration.run_migration()
            thesis = thesis_service.upsert_thesis(
                "Summary thesis",
                "Oil shipping disruptions are tightening supply.",
                confidence=0.76,
                status="active",
                evidence_delta=3,
                last_update_reason="Reuters and BBC corroborated the disruption.",
                title="Oil shipping disruption",
            )
            thesis_service.record_thesis_event(thesis["thesis_key"], "strengthened", "Confidence moved higher", 0.76, 3)
            conn = goal_service.get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO agent_journal (run_id, journal_type, summary, metrics_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    None,
                    "agent_loop",
                    "Previous run",
                    '{"items_kept":1,"items_fetched":2,"alerts_created":0,"action_proposals_created":0,"reasoning_chains_built":0,"research_agent_runs":0,"autonomous_goals_created":0,"thesis_updates":{"upserts":0,"confidence_updates":0,"touched":[]}}',
                    "2026-03-28T09:00:00+00:00",
                ),
            )
            cur.execute(
                """
                INSERT INTO agent_journal (run_id, journal_type, summary, metrics_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    None,
                    "agent_loop",
                    "Latest run",
                    '{"items_kept":4,"items_fetched":6,"alerts_created":2,"action_proposals_created":1,"reasoning_chains_built":2,"research_agent_runs":1,"autonomous_goals_created":1,"task_closures":{"completed":1,"stale":0,"contradicted":0,"superseded":0},"cluster_identities_seen":3,"llm_metrics":{"llm_calls_made":1,"cache_hits":2},"contradiction_llm_metrics":{"llm_calls_made":0},"duration_seconds":1.2,"thesis_updates":{"upserts":1,"confidence_updates":1,"touched":["summary thesis"]}}',
                    "2026-03-28T10:00:00+00:00",
                ),
            )
            conn.commit()
            conn.close()
            summary = terminal_service.get_terminal_agent_summary()
            diff = terminal_service.get_terminal_diff()
        self.assertEqual(summary["stories_reviewed"], 4)
        self.assertEqual(summary["actions_proposed"], 1)
        self.assertEqual(summary["top_belief_change"]["thesis_key"], thesis["thesis_key"])
        self.assertIn("metric_deltas", diff)
        self.assertEqual(diff["metric_deltas"]["items_kept"], 3)

    def test_terminal_drilldown_returns_trace(self):
        with self._db_patches():
            migration.run_migration()
            conn = goal_service.get_conn()
            article_id = self._insert_article(conn, headline="Oil shipping route disrupted", url="https://example.com/drilldown")
            conn.commit()
            thesis = thesis_service.upsert_thesis(
                "Drilldown thesis",
                "Shipping disruptions are tightening crude supply expectations.",
                confidence=0.74,
                status="active",
                evidence_delta=2,
                last_article_id=article_id,
                title="Oil shipping risk",
            )
            decision = decision_service.create_decision(
                article_id=article_id,
                decision_type="alert",
                reason="Strong corroboration",
                confidence=78,
                priority_score=85,
                state="open",
                cluster_key="cluster:drilldown",
                thesis_key=thesis["thesis_key"],
            )
            memory_service.write_memory(
                article_id=article_id,
                memory_type="thesis",
                thesis=thesis["current_claim"],
                confidence=78,
                status="active",
                linked_decision_id=decision["id"],
                thesis_key=thesis["thesis_key"],
            )
            task_service.create_task(
                "follow_up",
                "Follow up drilldown thesis",
                "Check if the shipping disruption persists.",
                thesis_key=thesis["thesis_key"],
                identity_key="cluster:drilldown",
                related_article_id=article_id,
            )
            reasoning_service.build_reasoning_chain(
                "Oil shipping route disrupted",
                "energy",
                db=conn,
                article_id=article_id,
                thesis_key=thesis["thesis_key"],
                source_name="rss",
            )
            conn.close()
            action_service.propose_action("alert", {}, thesis["thesis_key"], 0.74, 2, "test")
            detail = terminal_service.get_terminal_drilldown(thesis["thesis_key"])
        self.assertEqual(detail["thesis"]["thesis_key"], thesis["thesis_key"])
        self.assertTrue(detail["decisions"])
        self.assertTrue(detail["actions"])
        self.assertIn("trace", detail)

    def test_repeated_run_stability(self):
        state_file = Path(self.tmpdir.name) / "agent_state.json"
        with self._db_patches(extra=[(agent_state_service, "AGENT_STATE_FILE", state_file)]):
            migration.run_migration()
            payload = {
                "cards": [
                    {
                        "article_id": 0,
                        "source": "rss",
                        "headline": "Oil shipping risk rises",
                        "summary": "Crude supply concerns are building after a route disruption.",
                        "published_at": "2026-03-28T10:00:00+00:00",
                        "signal": "Bullish",
                        "impact_score": 72,
                        "confidence": 76,
                        "asset_tags": ["OIL"],
                        "macro_tags": ["GEOPOLITICS"],
                        "watchlist_hits": ["oil"],
                        "alert_tags": [],
                        "thesis": "Shipping disruptions are tightening crude supply expectations.",
                        "bull_case": "More disruption headlines confirm tighter supply.",
                        "bear_case": "Routes reopen and freight rates normalize.",
                        "what_to_watch": "Look for tanker rerouting and insurance updates.",
                        "confidence_score": 0.78,
                        "llm_category": "energy",
                        "llm_importance": "high",
                        "cluster_key": "cluster:oil-shipping",
                        "cluster_size": 2,
                    }
                ],
                "stats": {"watchlist_hits": 1},
            }
            with patch.object(agent_loop_service, "run_agent_cycle", return_value={"items_fetched": 1, "items_kept": 1, "alerts_created": 0, "llm_metrics": {}, "reasoning_chains_built": 0, "reasoning_cap_blocks": 0}), \
                 patch.object(agent_loop_service, "get_terminal_payload_clean", return_value=payload), \
                 patch.object(agent_loop_service, "evaluate_previous_items", return_value=[]), \
                 patch.object(agent_loop_service, "research_thesis", return_value={"metrics": {"research_agent_runs": 0}}), \
                 patch.object(agent_loop_service, "run_reflection", return_value={}), \
                 patch.object(agent_loop_service, "generate_autonomous_goals", return_value=[]), \
                 patch.object(agent_loop_service, "generate_daily_briefing", return_value={}):
                for _ in range(10):
                    result = agent_loop_service.run_real_agent_loop(max_records_per_source=1)
                    self.assertEqual(result["status"], "ok")
            decisions = decision_service.list_decisions(limit=50, open_only=False)
            tasks = task_service.list_tasks(limit=50, status=None)
            actions = action_service.list_actions(limit=50)
            journal = agent_loop_service.list_journal(limit=20)
        self.assertLessEqual(len(decisions), 2)
        self.assertLessEqual(len(tasks), 2)
        self.assertLessEqual(len(actions), 1)
        self.assertGreaterEqual(len(journal), 10)


if __name__ == "__main__":
    unittest.main()
