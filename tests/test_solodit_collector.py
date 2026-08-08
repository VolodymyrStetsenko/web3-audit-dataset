import asyncio

from aiohttp import web

from web3_dataset.solodit import SoloditContract, collect_solodit
from web3_dataset.storage import Catalog, atomic_json


def test_delta_stops_on_old_page_and_resumes_without_http(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_SOLODIT_KEY", "not-a-secret")
    requests: list[int] = []

    async def scenario() -> None:
        atomic_json(
            tmp_path / "raw/solodit/full/page-00000001.json",
            {
                "findings": [
                    {"id": 1, "report_date": {}},
                    {"id": 2, "report_date": {}},
                ]
            },
        )
        async def findings(request: web.Request) -> web.Response:
            body = await request.json()
            requests.append(body["page"])
            return web.json_response(
                {
                    "findings": [
                        {"id": 1, "report_date": "2025-01-01"},
                        {"id": 2, "report_date": "2025-01-02"},
                    ],
                    "metadata": {"totalResults": 20},
                }
            )

        application = web.Application()
        application.router.add_post("/findings", findings)
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        contract = SoloditContract(
            endpoint=f"http://127.0.0.1:{port}/findings",
            transport="rest",
            method="POST",
            api_key_env="TEST_SOLODIT_KEY",
            auth_header="X-Test-Key",
            auth_scheme="",
            page_parameter="page",
            page_size_parameter="pageSize",
            page_size=2,
            items_path="findings",
            total_path="metadata.totalResults",
            incremental_identity_field="id",
            incremental_parameters={"filters.sortDirection": "Desc"},
            static_parameters={"filters": {"sortDirection": "Asc"}},
            minimum_interval=0,
        )
        try:
            since = "2025-01-15T00:00:00+00:00"
            assert await collect_solodit(tmp_path, contract, since=since, concurrency=1) == 0
            assert await collect_solodit(tmp_path, contract, since=since, concurrency=1) == 0
            assert requests == [1]
            checkpoint = Catalog(tmp_path / "state/catalog.sqlite3").checkpoint(
                "solodit", since
            )
            assert checkpoint is not None
            assert checkpoint["completed"] == 1
            assert checkpoint["cursor"] == "1"
        finally:
            await runner.cleanup()

    asyncio.run(scenario())