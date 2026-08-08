import time

from web3_dataset.http import AsyncJsonClient


def test_retry_delay_caps_bad_rate_reset() -> None:
    delay = AsyncJsonClient._retry_delay(
        {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(time.time() + 86_400),
        },
        1,
    )
    assert delay == 120.0


def test_retry_after_is_capped() -> None:
    assert AsyncJsonClient._retry_delay({"Retry-After": "9999"}, 1) == 120.0