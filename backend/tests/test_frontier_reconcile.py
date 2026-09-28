"""Aturan rekonsiliasi pembanding lokal vs frontier (R01-R04 dan penggabungan lokal)."""
from app.frontier.reconcile import frontier_view, merge_local, reconcile, reviewer_conflict


def stable(status):
    return {"independent": {"status": status, "evidence_ids": ["news:a"], "reason": "ok"},
            "second_look_needed": False, "second_look": None}


def looked(first, second):
    return {"independent": {"status": first, "evidence_ids": ["news:a"], "reason": "ok"},
            "second_look_needed": True,
            "second_look": {"status": second, "evidence_ids": ["news:a"], "reason": "ok"} if second else None}


def test_local_merge_is_pessimistic_and_needs_every_reviewer():
    assert merge_local(["supported", "contradicted"], 2) == "contradicted"
    assert merge_local(["supported", "not_supported"], 2) == "unsupported"
    assert merge_local(["supported", None], 2) == "pending"
    assert merge_local(["supported", "supported"], 2) == "supported"
    assert reviewer_conflict(["supported", "unsupported"]) and not reviewer_conflict(["supported", None])


def test_r01_mechanical_failure_cannot_be_overridden():
    decision = reconcile(local_statuses=["supported", "contradicted"], expected_reviewers=2,
                         verdict=stable("supported"), mechanical_ok=False)
    assert decision["rule"] == "R0_mechanical" and decision["final_status"] == "contradicted"


def test_r02_stable_frontier_breaks_a_local_tie_only_toward_an_existing_opinion():
    tie = reconcile(local_statuses=["supported", "contradicted"], expected_reviewers=2,
                    verdict=looked("supported", "supported"))
    assert tie["rule"] == "R3_tie_break" and tie["final_status"] == "supported" and tie["changed"]
    novel = reconcile(local_statuses=["supported", "contradicted"], expected_reviewers=2,
                      verdict=stable("unsupported"))
    assert novel["rule"] == "R3_no_match" and novel["final_status"] == "contradicted"


def test_r03_a_frontier_that_changes_its_mind_is_not_used():
    decision = reconcile(local_statuses=["supported", "contradicted"], expected_reviewers=2,
                         verdict=looked("supported", "contradicted"))
    assert decision["rule"] == "R2_frontier_unstable" and decision["final_status"] == "contradicted"
    assert frontier_view(looked("supported", "contradicted"))[1] is False


def test_r04_a_missing_second_look_never_promotes_the_claim():
    decision = reconcile(local_statuses=["supported", "contradicted"], expected_reviewers=2,
                         verdict=looked("supported", None))
    assert decision["rule"] == "R2_frontier_unstable" and decision["final_status"] != "supported"


def test_missing_frontier_keeps_the_local_status():
    decision = reconcile(local_statuses=["supported", "unsupported"], expected_reviewers=2, verdict=None)
    assert decision["rule"] == "R1_frontier_missing" and decision["final_status"] == "unsupported"


def test_frontier_can_downgrade_but_never_upgrade_an_agreed_status():
    down = reconcile(local_statuses=["supported", "supported"], expected_reviewers=2, verdict=stable("contradicted"))
    assert down["rule"] == "R4_downgrade" and down["final_status"] == "unsupported"
    up = reconcile(local_statuses=["unsupported", "unsupported"], expected_reviewers=2, verdict=stable("supported"))
    assert up["rule"] == "R5_keep_local" and up["final_status"] == "unsupported"
    same = reconcile(local_statuses=["supported", "supported"], expected_reviewers=2, verdict=stable("supported"))
    assert same["rule"] == "R5_agree" and not same["changed"]
