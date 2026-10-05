"""Behavior checks use only synthetic records and no browsers or files."""
import copy
import unittest
from pathlib import Path

from feedback_pipeline.analysis import analyze_records, analyze_dynamic_records
from feedback_pipeline.config import load_yaml

ROOT = Path(__file__).resolve().parents[1]


def record(rid, text, rating=1, source="ios", **extra):
    return {"id": rid, "text": text, "title": "", "source": source,
            "platform": "iOS" if source == "ios" else source,
            "rating": rating, "date": "2026-01-04", **extra}


def payload(records):
    return {"period": {"start_date": "2026-01-01", "end_date": "2026-01-07"},
            "records": records, "files": [], "matched_sheets": [], "warnings": []}


class AnalysisAPITest(unittest.TestCase):
    def setUp(self):
        self.rules = load_yaml(ROOT / "config/rules.generic.yaml")

    def test_multilabel_distinct_counts_and_literal_quotes(self):
        batch = payload([record("a", "登录失败，而且应用一直闪退"),
                         record("b", "登录失败，而且应用一直闪退"),
                         record("c", "这条与规则无关", 5),
                         record("d", "登录失败", source="customer_service", rating=None)])
        original = copy.deepcopy(batch)
        result = analyze_records(batch, self.rules)
        self.assertEqual(batch, original)
        self.assertEqual(result["totals"]["records"], 4)
        self.assertEqual(result["totals"]["multi_label_records"], 2)
        login = next(i for i in result["issues"] if i["issue_key"] == "login_failure")
        self.assertEqual(login["record_count"], 3)
        self.assertEqual(len(login["record_ids"]), 3)
        self.assertLessEqual(len(login["quotes"]), 2)
        self.assertEqual(result["totals"]["unclassified"], 1)
        for issue in result["issues"]:
            for quote in issue["quotes"]:
                self.assertIn(quote["record_id"], issue["record_ids"])
                self.assertIn(quote["quote"], result["records"][quote["record_id"]]["text"])

    def test_context_exclusion_and_pc_feedback_retained(self):
        result = analyze_records(payload([record("a", "登录失败，问题已解决"),
                                          record("b", "电脑端账号登录失败")]), self.rules)
        login = next(i for i in result["issues"] if i["issue_key"] == "login_failure")
        self.assertEqual(login["record_ids"], ["b"])
        self.assertEqual(result["totals"]["unclassified"], 1)

    def test_rating_denominator_excludes_missing_invalid_and_nonstore(self):
        result = analyze_records(payload([record("a", "登录失败", 1), record("b", "搜索无结果", 5),
                                          record("c", "无评分", None), record("d", "无评分", 6),
                                          record("e", "登录失败", 1, "external")]), self.rules)
        self.assertEqual(result["totals"]["store_records"], 4)
        self.assertEqual(result["totals"]["rated_store_records"], 2)
        self.assertEqual(result["totals"]["negative"], 1)
        self.assertEqual(result["totals"]["negative_rate"], 50.0)
        no_rating = analyze_records(payload([record("a", "登录失败", None)]), self.rules)
        self.assertIsNone(no_rating["totals"]["negative_rate"])

    def test_quote_cap_does_not_truncate_statistics(self):
        self.rules["rules"]["max_quotes_per_issue"] = 1
        self.rules["rules"]["max_quote_chars"] = 5
        result = analyze_records(payload([record(str(i), "账号登录失败，无法正常使用") for i in range(13)]), self.rules)
        issue = next(i for i in result["issues"] if i["issue_key"] == "login_failure")
        self.assertEqual(issue["record_count"], 13)
        self.assertEqual(len(issue["record_ids"]), 13)
        self.assertEqual(len(issue["quotes"]), 1)
        self.assertEqual(issue["quotes"][0]["quote"], "账号登录失")

    def test_configurable_strong_signal_and_invalid_threshold(self):
        rules = {"rules": {"negative_rating_max": 3}, "categories": [
            {"key": "security", "code": "R", "name": "安全", "module_title": "安全反馈", "order": 1,
             "strong_only": True, "strong_keywords": ["封禁"],
             "issues": [{"key": "blocked", "name": "账号封禁", "keywords": ["账号"]}]}]}
        result = analyze_records(payload([record("a", "账号登录失败"), record("b", "账号被封禁")]), rules)
        self.assertEqual(result["issues"][0]["record_ids"], ["b"])
        for value in (True, 0, 6, 1.5):
            rules["rules"]["negative_rating_max"] = value
            with self.assertRaises(ValueError):
                analyze_records(payload([record("a", "账号封禁")]), rules)

    def test_duplicate_ids_dates_and_missing_date_audit(self):
        for records in ([record("a", "登录失败"), record("a", "闪退")],
                        [record("a", "登录失败", date="2025-12-31")]):
            with self.assertRaises(ValueError):
                analyze_records(payload(records), self.rules)
        result = analyze_records(payload([record("a", "登录失败", date=None)]), self.rules)
        self.assertTrue(any("no date" in w for w in result["metadata"]["warnings"]))
        self.assertEqual(result["metadata"]["contamination"]["records_out_of_requested_range"], [])


class MediaAnalysisAPITest(unittest.TestCase):
    def setUp(self):
        self.rules = load_yaml(ROOT / "config/rules.media.yaml")

    def test_reused_media_clustering_full_counts_and_exact_evidence(self):
        batch = payload([record(str(i), "视频无法播放", 1) for i in range(13)] +
                        [record("s", "搜索失败，搜不到资源", 4), record("u", "只有问候", None)])
        original = copy.deepcopy(batch)
        result = analyze_dynamic_records(batch, self.rules)
        self.assertEqual(batch, original)
        self.assertEqual(result["architecture"]["fixed_modules"], ["P", "W", "M", "D", "N"])
        playback = next(i for i in result["issues"] if i["category_key"] == "playback")
        self.assertEqual(playback["record_count"], 13)
        self.assertEqual(len(playback["record_ids"]), 13)
        self.assertEqual(playback["negative_count"], 13)
        self.assertLessEqual(len(playback["quotes"]), 2)
        self.assertIn("u", {row["id"] for row in result["unclassified"]})
        for issue in result["issues"]:
            for quote in issue["quotes"]:
                self.assertIn(quote["record_id"], issue["record_ids"])
                self.assertIn(quote["quote"], result["records"][quote["record_id"]]["text"])

    def test_title_body_routing_never_invents_a_combined_quote(self):
        from feedback_pipeline.report import validate_analysis
        for qa in (True, False):
            self.rules["rules"]["media_report_quality"] = qa
            result = analyze_dynamic_records(payload([record("a", "视频无法播放", title="播放失败")]), self.rules)
            self.assertTrue(result["issues"])
            validate_analysis(result)

    def test_media_custom_threshold_and_no_score_denominator(self):
        self.rules["rules"]["negative_rating_max"] = 4
        result = analyze_dynamic_records(payload([record("a", "视频无法播放", 4),
                                                  record("b", "视频无法播放", None),
                                                  record("c", "视频无法播放", None, "external")]), self.rules)
        self.assertEqual(result["totals"]["negative_rate"], 100.0)
        self.assertEqual(result["issues"][0]["negative_count"], 1)
        unrated = analyze_dynamic_records(payload([record("a", "视频无法播放", None)]), self.rules)
        self.assertIsNone(unrated["totals"]["negative_rate"])


if __name__ == "__main__":
    unittest.main()
