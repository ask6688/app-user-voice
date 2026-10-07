"""Synthetic preset checks and an in-memory comparison with the pre-extraction code."""
import copy
import json
import re
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from openpyxl import load_workbook

from feedback_pipeline import exporters, report, semantics
from feedback_pipeline.analysis import dynamic, dynamic_engine, eligibility

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "03d22063f0ef72ff31eb31e0cca3829052a5f810"


def baseline_source(path):
    try:
        result = subprocess.run(["git", "show", f"{BASELINE}:{path}"], cwd=ROOT,
                                text=True, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise unittest.SkipTest("Compatibility baseline requires Git; preset checks still run") from exc
    if result.returncode:
        raise unittest.SkipTest("Compatibility baseline requires Git and commit " + BASELINE)
    return result.stdout


def load_baseline():
    """Keep historical relative imports in a private package; leave current imports alone."""
    namespace = "_semantic_compat_baseline"
    names = ["analysis.period", "analysis.engine", "analysis.dynamic_base", "analysis.dynamic",
             "analysis.eligibility", "analysis.media_report", "analysis.dynamic_engine",
             "report", "exporters"]
    loaded, previous = {}, {}
    try:
        for name in (namespace, namespace + ".analysis"):
            previous[name] = sys.modules.get(name)
            package = types.ModuleType(name)
            package.__path__ = []
            sys.modules[name] = package
        for name in names:
            qualified = namespace + "." + name
            previous[qualified] = sys.modules.get(qualified)
            path = "feedback_pipeline/" + name.replace(".", "/") + ".py"
            module = types.ModuleType(qualified)
            module.__package__ = qualified.rpartition(".")[0]
            module.__file__ = f"git:{BASELINE}:{path}"
            sys.modules[qualified] = module
            exec(compile(baseline_source(path), module.__file__, "exec"), module.__dict__)
            loaded[name] = module
        return loaded
    finally:
        for name, old in previous.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def semantic_cases():
    data = json.loads((ROOT / "tests/semantic_cases.json").read_text(encoding="utf-8"))
    if data["schema_version"] != 1:
        raise ValueError("Unsupported semantic case schema")
    return data["cases"]


def make_record(rid, text, **fields):
    return {"id": rid, "text": text, "title": "", "source": "ios", "platform": "iOS",
            "rating": 3, "date": "2026-01-04", "file": "synthetic.csv", "sheet": "",
            "row": 2, **fields}


def make_payload(records):
    return {"period": {"start_date": "2026-01-01", "end_date": "2026-01-07"},
            "records": records, "files": [], "matched_sheets": [], "warnings": []}


def regression_records():
    records = [make_record(case["id"], case["text"], title=case["title"],
                           source=case["source"], rating=case["rating"])
               for case in semantic_cases()]
    texts = ["播放不了", "不能播放", "播不了", "下载不了", "不能下载", "下不了",
             "搜不到", "搜索不了", "扣费", "扣钱", "自动扣费", "申请退费",
             "会员下载速度太慢", "开通会员后视频无法播放", "更新以后下载失败",
             "希望下个版本支持字幕翻译", "字幕加载失败", "投屏搜不到设备",
             "浏览器网页打不开", "下载文件找不到", "退款迟迟未到账",
             "上传文件失败", "没有闪退，视频播放已经恢复正常",
             "视频无法播放，而且下载失败；另外搜索失败，搜不到资源"]
    records.extend(make_record(f"extra-{index}", text) for index, text in enumerate(texts))
    records.extend([
        make_record("pc-only", "电脑端视频无法播放", source="external", rating=None),
        make_record("pc-mobile", "电脑端正常，手机视频无法播放", source="external", rating=None),
        make_record("store-pc", "电脑端视频无法播放"),
        make_record("cs-user", "视频无法播放；请您提供截图", source="customer_service", rating=None),
        make_record("cs-template", "您好，请您提供视频播放失败的截图", source="customer_service", rating=None),
        make_record("title-body", "视频无法播放", title="播放失败"),
        make_record("missing-date", "视频无法播放", date=None),
        make_record("rating-one", "视频无法播放", rating=1),
        make_record("rating-four", "视频无法播放", rating=4),
        make_record("rating-five", "视频无法播放", rating=5),
        make_record("rating-missing", "视频无法播放", rating=None),
        make_record("rating-invalid", "视频无法播放", rating=6),
        make_record("rating-bool", "视频无法播放", rating=True),
        make_record("rating-string", "视频无法播放", rating="3"),
        make_record("external-rating", "视频无法播放", source="external", rating=1),
        make_record("empty", ""), make_record("low-information", "好！"),
        make_record("numbers", "123456789"),
    ])
    # Distinct IDs with the same text verify that quote caps never cap statistics.
    records.extend(make_record(f"repeated-{i}", "视频无法播放") for i in range(13))
    for row, record in enumerate(records, 2):
        record["row"] = row
    return records


def workbook_contents(path):
    workbook = load_workbook(path)
    try:
        return {sheet.title: {"cells": [[(cell.value, cell.data_type, cell.number_format)
                                        for cell in row] for row in sheet],
                              "freeze": sheet.freeze_panes, "filter": sheet.auto_filter.ref,
                              "widths": {key: col.width for key, col in sheet.column_dimensions.items()}}
                for sheet in workbook}
    finally:
        workbook.close()


class SemanticPresetTest(unittest.TestCase):
    def test_malformed_alias_pairs_are_rejected_before_use(self):
        data = json.loads((ROOT / "feedback_pipeline/semantic_definitions.json").read_text())
        for pairs in ([["播放不了"]], [["播放不了", ""]], [["播放不了", 1]],
                      [["播放不了", "无法播放"], ["播放不了", "播放失败"]]):
            with self.subTest(pairs=pairs):
                invalid = copy.deepcopy(data)
                invalid["aliases"]["playback_failure"] = pairs
                with patch.object(semantics.Path, "read_text", return_value=json.dumps(invalid)), \
                     self.assertRaises(ValueError):
                    semantics._load_definitions()

    def test_frozen_routes_and_final_issues_are_separate(self):
        cases = semantic_cases()
        records = [make_record(c["id"], c["text"], title=c["title"],
                               source=c["source"], rating=c["rating"]) for c in cases]
        rules = yaml.safe_load((ROOT / "config/rules.media.yaml").read_text())
        result = dynamic_engine.analyze_dynamic_records(make_payload(records), rules)
        for case, record in zip(cases, records):
            with self.subTest(case=case["id"]):
                self.assertEqual(sorted({row["module"] for row in dynamic.route_record(record)}),
                                 sorted(case["route_modules"]))
                self.assertEqual(sorted({issue["category_key"] for issue in result["issues"]
                                         if case["id"] in issue["record_ids"]}),
                                 sorted(case["issue_modules"]))
        self.assertEqual({issue["code"]: sorted(issue["record_ids"]) for issue in result["issues"]},
                         {"P1": ["browser-playback", "cannot-play", "playback-failure"],
                          "M1": ["renewal-charge"], "N1": ["update-playback"]})


class SemanticCompatibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = load_baseline()
        cls.rules = yaml.safe_load(baseline_source("config/rules.media.yaml"))

    def test_filters_fragments_routing_and_normalized_text_match_baseline(self):
        patterns = [re.compile(p, re.I) for p in self.rules["rules"]["low_information_patterns"]]
        for record in regression_records():
            with self.subTest(record=record["id"]):
                self.assertEqual(eligibility.check_eligibility(record, patterns),
                                 self.old["analysis.eligibility"].check_eligibility(record, patterns))
                text = (record["title"] + " " + record["text"]).strip()
                self.assertEqual(dynamic.split_fragments(text), self.old["analysis.dynamic"].split_fragments(text))
                self.assertEqual(dynamic.route_record(record), self.old["analysis.dynamic"].route_record(record))
                for module in (None, "playback", "web_search", "member", "download", "update"):
                    self.assertEqual(dynamic.semantic_normalize(text, module),
                                     self.old["analysis.dynamic"].semantic_normalize(text, module))

    def test_full_analysis_and_all_report_formats_match_baseline(self):
        batch = make_payload(regression_records())
        original = copy.deepcopy(batch)
        for qa in (True, False):
            for negative_max in (3, 4):
                with self.subTest(qa=qa, negative_max=negative_max):
                    rules = copy.deepcopy(self.rules)
                    rules["rules"].update(media_report_quality=qa, negative_rating_max=negative_max)
                    expected = self.old["analysis.dynamic_engine"].analyze_dynamic_records(batch, rules)
                    actual = dynamic_engine.analyze_dynamic_records(batch, rules)
                    self.assertEqual(actual, expected)
                    self.assertEqual(batch, original)
                    expected_plan = self.old["report"].build_report_plan(expected, {})
                    actual_plan = report.build_report_plan(actual, {})
                    self.assertEqual(actual_plan, expected_plan)
                    with tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        app, quality = {"name": "Synthetic compatibility App"}, {"files": [], "excluded": []}
                        formats = ["html", "markdown", "xlsx"]
                        old_files = self.old["exporters"].export_report(expected_plan, app, quality, root / "old", formats)
                        new_files = exporters.export_report(actual_plan, app, quality, root / "new", formats)
                        self.assertEqual(new_files, old_files)
                        for name in old_files:
                            if name.endswith(".xlsx"):
                                self.assertEqual(workbook_contents(root / "new" / name),
                                                 workbook_contents(root / "old" / name))
                            else:
                                self.assertEqual((root / "new" / name).read_bytes(),
                                                 (root / "old" / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
