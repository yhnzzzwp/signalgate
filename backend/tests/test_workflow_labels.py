import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from app.workflow import labels

METRICS = {
    "fundamental:debt_to_equity": {"metric_id": "fundamental:debt_to_equity", "domain": "fundamental",
                                   "name": "Utang berbunga terhadap ekuitas", "value": 0.17, "unit": "x",
                                   "formula": "total_debt / total_equity", "period": "Q2 2026", "status": "ok"},
    "valuation:peer_pe_median": {"metric_id": "valuation:peer_pe_median", "domain": "valuation",
                                 "name": "Median PE TTM peer", "value": 63.61, "unit": "x",
                                 "formula": "median(pe_ttm peer > 0)", "period": "peer", "status": "ok"},
}


def claim(claim_id, statement, status, metric_ids=(), mechanical=(), withdrawn=False, author="model:ollama:qwen2.5:7b"):
    return {"claim_id": claim_id, "domain": claim_id.split(":")[0], "kind": "interpretation", "statement": statement,
            "attribution": "data", "metric_ids": list(metric_ids), "source_ids": [], "quote": None,
            "validation_status": status, "validation_notes": ["Pembanding: setuju"], "mechanical_issues": list(mechanical),
            "review_notes": ["Pembanding: setuju"], "withdrawn": withdrawn, "author": author}


def report(run_id, claims, generated_at="2026-09-19T07:00:00", reviewer="gemma3:4b", metrics=None):
    panels = {}
    for item in claims:
        panels.setdefault(item["domain"], {"claims": []})["claims"].append(item)
    return {"run_id": run_id, "ticker": "TEST", "as_of": "2026-09-19", "generated_at": generated_at,
            "panels": panels, "metrics": metrics or METRICS, "validation": {"reviewer_model": reviewer},
            "model_runs": [{"role": "analyst", "model": "ollama:qwen2.5:7b"}]}


SHOWN_OK = claim("fundamental:model:1", "Utang berbunga terhadap ekuitas 0,17x.", "supported",
                 ["fundamental:debt_to_equity"])
SHOWN_BAD = claim("valuation:model:1", "Median PE peer 63,61x lebih tinggi dari PE historis.", "supported",
                  ["valuation:peer_pe_median"])
KILLED_GOOD = claim("fundamental:model:2", "Leverage rendah (0,17x).", "unsupported", ["fundamental:debt_to_equity"],
                    mechanical=["Angka tanpa dasar"])
KILLED_BAD = claim("valuation:model:2", "PE peer turun.", "contradicted", ["valuation:peer_pe_median"])
CODE = claim("fundamental:calc:x", "Template kode.", "supported", author="code")


class LabelSheetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def rows_to_labels(self, rows, verdicts, labeler="A"):
        path = self.tmp / f"labels-{labeler}.csv"
        labels.write_csv(path, [{**row, "putusan": verdicts.get(row["pernyataan"], ""), "pelabel": labeler}
                                for row in rows])
        return path

    def test_export_is_blind_and_deduplicates_replays(self):
        reports = {"r1": report("r1", [SHOWN_OK, KILLED_GOOD, CODE]),
                   "r2": report("r2", [dict(SHOWN_OK, validation_status="unsupported"), SHOWN_BAD])}
        rows = labels.export_rows(reports, ["r1", "r2"], self.tmp, "dev")
        self.assertEqual([row["pernyataan"] for row in rows],
                         [SHOWN_OK["statement"], KILLED_GOOD["statement"], SHOWN_BAD["statement"]])
        path = self.tmp / "sheet.csv"
        labels.write_csv(path, rows)
        text = path.read_text(encoding="utf-8-sig")
        for leak in ("supported", "unsupported", "Pembanding", "Angka tanpa dasar", "withdrawn"):
            self.assertNotIn(leak, text)
        self.assertIn("Utang berbunga terhadap ekuitas (Q2 2026) = 0,17x", rows[0]["bukti"])

        again = labels.export_rows(reports, ["r1", "r2"], self.tmp, "dev", skip={rows[0]["kunci"]})
        self.assertNotIn(SHOWN_OK["statement"], [row["pernyataan"] for row in again])

    def test_key_changes_when_cited_value_changes(self):
        other = {**METRICS, "fundamental:debt_to_equity": {**METRICS["fundamental:debt_to_equity"], "value": 0.9}}
        self.assertNotEqual(labels.claim_key(report("a", []), SHOWN_OK),
                            labels.claim_key(report("b", [], metrics=other), SHOWN_OK))

    def test_score_counts_precision_rejections_and_catches(self):
        reports = {"r1": report("r1", [SHOWN_OK, SHOWN_BAD, KILLED_GOOD, KILLED_BAD])}
        rows = labels.export_rows(reports, ["r1"], self.tmp, "dev")
        path = self.rows_to_labels(rows, {SHOWN_OK["statement"]: "benar", SHOWN_BAD["statement"]: "salah_tafsir",
                                          KILLED_GOOD["statement"]: "benar", KILLED_BAD["statement"]: "salah_fakta"})
        result = labels.score(reports, labels.read_labels([path]))
        summary = result["ringkasan"]["qwen2.5:7b -> gemma3:4b | semua"]
        self.assertEqual(summary["tampil"], 2)
        self.assertEqual(summary["presisi_ketat"], 0.5)
        self.assertEqual(summary["ditolak_padahal_benar"], 1)
        self.assertEqual(summary["ditolak_oleh"], {"kode": 1, "pembanding": 1})
        self.assertEqual((summary["klaim_salah"], summary["klaim_salah_tertangkap"]), (2, 1))
        self.assertEqual([row["pernyataan"] for row in result["lolos_padahal_bermasalah"]], [SHOWN_BAD["statement"]])
        self.assertEqual(result["ditolak_padahal_benar"][0]["oleh"], "kode")

    def test_disagreement_needs_consensus(self):
        reports = {"r1": report("r1", [SHOWN_OK])}
        rows = labels.export_rows(reports, ["r1"], self.tmp, "dev")
        first = self.rows_to_labels(rows, {SHOWN_OK["statement"]: "benar"}, "A")
        second = self.rows_to_labels(rows, {SHOWN_OK["statement"]: "berlebihan"}, "B")
        result = labels.score(reports, labels.read_labels([first, second]))
        self.assertEqual(len(result["label"]["konflik"]), 1)
        self.assertEqual(result["label"]["kesepakatan"]["persen_sepakat"], 0.0)
        self.assertEqual(result["ringkasan"]["qwen2.5:7b -> gemma3:4b | semua"]["tampil"], 0)

        agreed = self.rows_to_labels(rows, {SHOWN_OK["statement"]: "berlebihan"}, "konsensus")
        result = labels.score(reports, labels.read_labels([first, second, agreed]))
        self.assertEqual(result["label"]["konflik"], [])
        self.assertEqual(result["ringkasan"]["qwen2.5:7b -> gemma3:4b | semua"]["presisi_longgar"], 1.0)

    def test_reads_semicolon_csv_and_flags_unknown_verdicts(self):
        path = self.tmp / "excel.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["kunci", "putusan", "pelabel"])
            writer.writerow(["abc", "benar", "A"])
            writer.writerow(["def", "mungkin", "A"])
        resolved, problems = labels.resolve_labels(labels.read_labels([path]))
        self.assertEqual(list(resolved), ["abc"])
        self.assertIn("mungkin", problems["tidak_valid"][0])

    def test_default_scores_latest_run_per_model_configuration(self):
        for run_id, generated, reviewer in (("old", "2026-09-19T01:00", "gemma3:4b"),
                                            ("new", "2026-09-19T02:00", "gemma3:4b"),
                                            ("glm", "2026-09-19T01:30", "glm4:9b")):
            directory = self.tmp / run_id
            directory.mkdir()
            (directory / "report-v1.json").write_text(json.dumps(report(run_id, [SHOWN_OK], generated, reviewer)))
        reports = labels.load_reports(self.tmp)
        key = labels.claim_key(reports["old"], SHOWN_OK)
        self.assertEqual(labels.select_runs(reports, {key}, []), ["glm", "new"])
        self.assertEqual(labels.latest_for(reports, "test"), "new")

    def test_wilson_interval_is_wide_for_small_samples(self):
        low, high = labels.wilson(8, 10)
        self.assertLess(low, 0.5)
        self.assertGreater(high, 0.9)


if __name__ == "__main__":
    unittest.main()
