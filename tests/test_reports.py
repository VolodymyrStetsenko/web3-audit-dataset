import json

from web3_dataset.reports import (
    _download_url,
    _public_http_url,
    _solodit_report_sources,
    solodit_report_links,
)
from web3_dataset.storage import atomic_json


def test_report_links_are_deduplicated(tmp_path) -> None:
    url = "https://example.com/report.pdf"
    atomic_json(
        tmp_path / "raw/solodit/full/page-00000001.json",
        {"findings": [{"id": 1, "pdf_link": url}, {"id": 2, "pdf_link": url}]},
    )
    assert solodit_report_links(tmp_path) == {url: {"1", "2"}}


def test_private_report_urls_are_rejected() -> None:
    assert _public_http_url("https://example.com/report.pdf")
    assert not _public_http_url("http://127.0.0.1/report.pdf")
    assert not _public_http_url("file:///etc/passwd")


def test_github_blob_pdf_uses_raw_download() -> None:
    assert _download_url("https://github.com/org/repo/blob/main/reports/a.pdf") == (
        "https://raw.githubusercontent.com/org/repo/main/reports/a.pdf"
    )


def test_known_official_report_rename_is_resolved() -> None:
    assert _download_url(
        "https://github.com/trailofbits/publications/blob/master/reviews/"
        "2025-02-chainlinklabs-customsendersreceivers-securityreview.pdf"
    ).endswith("/2025-02-chainlink-customsendersreceivers-securityreview.pdf")


def test_official_pdf_source_is_a_fallback(tmp_path) -> None:
    primary = "https://cdn.example.com/report.pdf"
    fallback = "https://github.com/auditor/reports/blob/main/report.pdf"
    atomic_json(
        tmp_path / "raw/solodit/full/page-00000001.json",
        {
            "findings": [
                {"id": 1, "pdf_link": primary, "source_link": fallback},
                {"id": 2, "pdf_link": primary, "source_link": "https://example.com/finding"},
            ]
        },
    )
    assert _solodit_report_sources(tmp_path) == {
        primary: {"finding_ids": {"1", "2"}, "fallbacks": {fallback}}
    }