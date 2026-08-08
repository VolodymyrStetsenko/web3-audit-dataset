from pathlib import Path

from web3_dataset.solodit import SoloditContract


def test_official_contract_builds_full_and_delta_requests() -> None:
    contract = SoloditContract.load(Path("config/solodit.json"))

    _, full = contract.request_parts(3, None)
    _, delta = contract.request_parts(4, "2025-01-02T03:04:05+00:00")

    assert full is not None
    assert full["page"] == 3
    assert full["pageSize"] == 100
    assert full["filters"]["reported"]["value"] == "alltime"
    assert delta is not None
    assert delta["filters"]["reported"]["value"] == "alltime"
    assert delta["filters"]["sortDirection"] == "Desc"
    assert "reportedAfter" not in delta["filters"]