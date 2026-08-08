from __future__ import annotations

import asyncio
import copy
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
import requests

from .http import AsyncJsonClient, dotted_get, dotted_set
from .storage import Catalog, atomic_json


@dataclass(frozen=True)
class SoloditContract:
    endpoint: str
    transport: str
    method: str = "GET"
    api_key_env: str = "SOLODIT_API_KEY"
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    page_parameter: str = "page"
    page_size_parameter: str = "limit"
    page_start: int = 1
    page_size: int = 100
    items_path: str = "data.items"
    total_path: str | None = "data.total"
    has_more_path: str | None = None
    updated_since_parameter: str | None = None
    incremental_stop_field: str | None = None
    incremental_identity_field: str | None = None
    incremental_parameters: dict[str, Any] = field(default_factory=dict)
    static_parameters: dict[str, Any] = field(default_factory=dict)
    static_headers: dict[str, str] = field(default_factory=dict)
    graphql_query: str | None = None
    minimum_interval: float = 3.1

    @classmethod
    def load(cls, path: Path) -> "SoloditContract":
        payload = json.loads(path.read_text())
        contract = cls(**payload)
        if contract.transport not in {"rest", "graphql"}:
            raise ValueError("transport must be 'rest' or 'graphql'")
        if contract.transport == "graphql" and not contract.graphql_query:
            raise ValueError("graphql_query is required for GraphQL transport")
        if contract.method.upper() not in {"GET", "POST"}:
            raise ValueError("method must be GET or POST")
        return contract

    def headers(self) -> dict[str, str]:
        headers = dict(self.static_headers)
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"set {self.api_key_env} before using Solodit")
        value = f"{self.auth_scheme} {api_key}".strip()
        headers[self.auth_header] = value
        headers.setdefault("Accept", "application/json")
        return headers

    def request_parts(
        self, page: int, since: str | None
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        parameters = copy.deepcopy(self.static_parameters)
        parameters[self.page_parameter] = page
        parameters[self.page_size_parameter] = self.page_size
        if since:
            if (
                not self.updated_since_parameter
                and not self.incremental_stop_field
                and not self.incremental_identity_field
            ):
                raise ValueError(
                    "--since requires an updated-since parameter or incremental stop field"
                )
            for path, value in self.incremental_parameters.items():
                dotted_set(parameters, path, value)
            if self.updated_since_parameter:
                dotted_set(parameters, self.updated_since_parameter, since)
        if self.transport == "graphql":
            return None, {"query": self.graphql_query, "variables": parameters}
        if self.method.upper() == "POST":
            return None, parameters
        return parameters, None


def validate_contract(contract: SoloditContract, since: str | None = None) -> dict[str, Any]:
    params, json_body = contract.request_parts(contract.page_start, since)
    response = requests.request(
        contract.method,
        contract.endpoint,
        headers=contract.headers(),
        params=params,
        json=json_body,
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    items = dotted_get(payload, contract.items_path)
    if not isinstance(items, list):
        raise ValueError(
            f"items_path {contract.items_path!r} did not resolve to a JSON array"
        )
    return {
        "status": response.status_code,
        "items": len(items),
        "total": dotted_get(payload, contract.total_path),
        "top_level_keys": sorted(payload) if isinstance(payload, dict) else [],
    }


async def collect_solodit(
    root: Path,
    contract: SoloditContract,
    *,
    since: str | None = None,
    concurrency: int = 4,
) -> int:
    catalog = Catalog(root / "state" / "catalog.sqlite3")
    catalog.initialize()
    partition = since or "full"
    checkpoint = catalog.checkpoint("solodit", partition)
    if checkpoint and checkpoint["completed"]:
        return 0
    start_page = int(checkpoint["cursor"]) + 1 if checkpoint and checkpoint["cursor"] else contract.page_start
    timeout = aiohttp.ClientTimeout(total=90, connect=20)
    connector = aiohttp.TCPConnector(limit=max(concurrency * 2, 8))
    collected = 0
    known_ids: set[str] = set()
    if since and contract.incremental_identity_field:
        for raw_page in (root / "raw/solodit").glob("**/page-*.json"):
            try:
                previous_payload = json.loads(raw_page.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            previous_items = dotted_get(previous_payload, contract.items_path, [])
            if not isinstance(previous_items, list):
                continue
            for previous_item in previous_items:
                identity = dotted_get(previous_item, contract.incremental_identity_field)
                if identity is not None:
                    known_ids.add(str(identity))

    def after_watermark(item: Any) -> bool:
        if not since:
            return True
        if contract.incremental_identity_field:
            identity = dotted_get(item, contract.incremental_identity_field)
            return identity is None or str(identity) not in known_ids
        if not contract.incremental_stop_field:
            return True
        value = dotted_get(item, contract.incremental_stop_field)
        if not value:
            return True
        return str(value) > since

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        client = AsyncJsonClient(
            session,
            concurrency=concurrency,
            minimum_interval=contract.minimum_interval,
        )

        async def fetch(page: int) -> tuple[int, Any, list[Any], int | None, bool | None]:
            params, json_body = contract.request_parts(page, since)
            payload, _ = await client.request_json(
                contract.method,
                contract.endpoint,
                headers=contract.headers(),
                params=params,
                json_body=json_body,
            )
            items = dotted_get(payload, contract.items_path)
            if not isinstance(items, list):
                raise ValueError(
                    f"items_path {contract.items_path!r} did not resolve to an array"
                )
            total_value = dotted_get(payload, contract.total_path)
            total = int(total_value) if total_value is not None else None
            has_more_value = dotted_get(payload, contract.has_more_path)
            has_more = bool(has_more_value) if has_more_value is not None else None
            return page, payload, items, total, has_more

        page = start_page
        final_page: int | None = None
        while final_page is None or page <= final_page:
            pages = list(range(page, page + concurrency))
            if final_page is not None:
                pages = [value for value in pages if value <= final_page]
            results = await asyncio.gather(*(fetch(value) for value in pages))
            stop = False
            for page_number, payload, items, total, has_more in sorted(results):
                raw_path = root / "raw" / "solodit" / partition / f"page-{page_number:08d}.json"
                digest = atomic_json(raw_path, payload)
                catalog.record_source_object(
                    "solodit-page", f"{partition}:{page_number}", raw_path, digest
                )
                new_items = [item for item in items if after_watermark(item)]
                collected += len(new_items)
                catalog.save_checkpoint("solodit", partition, str(page_number), False)
                if total is not None:
                    final_page = contract.page_start + max(math.ceil(total / contract.page_size) - 1, 0)
                if has_more is False or (has_more is None and len(items) < contract.page_size):
                    final_page = page_number
                    stop = True
                    break
                if since and items and not new_items:
                    final_page = page_number
                    stop = True
                    break
            page = pages[-1] + 1
            if stop:
                break

    catalog.save_checkpoint("solodit", partition, str(final_page or page - 1), True)
    return collected