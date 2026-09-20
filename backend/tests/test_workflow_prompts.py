import json
import unittest

from app.workflow import prompts


def claim(claim_id, statement="Pernyataan uji yang cukup panjang untuk klaim.", domain="news"):
    return {"claim_id": claim_id, "domain": domain, "statement": statement, "metric_ids": [], "source_ids": []}


class ClaimBatchesTests(unittest.TestCase):
    def test_no_claims_means_no_batches(self):
        self.assertEqual(prompts.claim_batches([], 10_000), [])

    def test_everything_fits_in_one_batch_when_the_budget_is_generous(self):
        claims = [claim(f"c{i}") for i in range(4)]
        batches = prompts.claim_batches(claims, 10_000)
        self.assertEqual(len(batches), 1)
        self.assertEqual([view["claim_id"] for view in batches[0]], ["c0", "c1", "c2", "c3"])

    def test_splits_into_multiple_batches_that_together_cover_every_claim(self):
        claims = [claim(f"c{i}") for i in range(4)]
        one_size = len(json.dumps(prompts.claim_view(claims[0]), ensure_ascii=False)) + 1
        batches = prompts.claim_batches(claims, one_size * 2)
        self.assertEqual(len(batches), 2)
        self.assertEqual([len(batch) for batch in batches], [2, 2])
        covered = [view["claim_id"] for batch in batches for view in batch]
        self.assertEqual(covered, ["c0", "c1", "c2", "c3"])

    def test_a_claim_too_big_for_the_budget_still_gets_its_own_batch_instead_of_looping_forever(self):
        claims = [claim("c0"), claim("c1")]
        batches = prompts.claim_batches(claims, max_chars=1)
        self.assertEqual([view["claim_id"] for batch in batches for view in batch], ["c0", "c1"])
        self.assertTrue(all(len(batch) == 1 for batch in batches))


if __name__ == "__main__":
    unittest.main()
