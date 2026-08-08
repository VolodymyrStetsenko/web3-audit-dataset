import asyncio

from web3_dataset import pipeline


def test_sync_prunes_before_clone(tmp_path, monkeypatch) -> None:
    events: list[str] = []
    clone_options: list[bool] = []

    async def collect(*args, **kwargs):
        return 0

    async def discover(*args, **kwargs):
        events.append("discover")
        return 0

    def prune(root, *, execute):
        assert execute is True
        events.append("prune")
        return {"kept": 0, "removed": 0}

    async def clone(*args, **kwargs):
        events.append("clone")
        clone_options.append(kwargs["include_unlicensed"])
        return {"cloned": 0, "updated": 0, "failed": 0}

    async def reports(*args, **kwargs):
        return {"downloaded": 0, "existing": 0, "failed": 0}

    monkeypatch.setattr(pipeline, "collect_solodit", collect)
    monkeypatch.setattr(pipeline, "discover_repositories", discover)
    monkeypatch.setattr(pipeline, "prune_irrelevant_repositories", prune)
    monkeypatch.setattr(pipeline, "clone_repositories", clone)
    monkeypatch.setattr(pipeline, "download_solodit_reports", reports)
    monkeypatch.setattr(pipeline, "normalize_solodit", lambda root: 0)
    monkeypatch.setattr(pipeline, "normalize_repositories", lambda root: 0)
    monkeypatch.setattr(pipeline, "normalize_local_reports", lambda root: 0)
    monkeypatch.setattr(pipeline, "export_rag", lambda root: 0)

    result = asyncio.run(pipeline.synchronize(tmp_path, object()))

    assert events == ["discover", "prune", "clone"]
    assert clone_options == [False]
    assert result["github_pruned"] == {"kept": 0, "removed": 0}

    asyncio.run(pipeline.synchronize(tmp_path, object(), include_unlicensed=True))
    assert clone_options[-1] is True