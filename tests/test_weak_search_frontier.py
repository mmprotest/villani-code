from villani_code.search.frontier import FrontierBranch
from villani_code.search.pruning import no_progress_stop, should_prune_branch


def test_branch_pruning_on_repeated_failure_signatures():
    branch = FrontierBranch(id="b", suspect_ref="x.py", hypothesis_id="h")
    assert not should_prune_branch(branch, "same")
    assert should_prune_branch(branch, "same")


def test_no_progress_stop_behavior():
    assert no_progress_stop(2, 2)


def test_timeout_aware_early_stop():
    from villani_code.runtime.budgets import timeout_imminent

    assert timeout_imminent(0, 95, 100, 10)
