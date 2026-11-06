import asyncio
import os
import statistics
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import user_service


@pytest.mark.load
def test_bounded_concurrent_user_creation_load():
    request_count = int(os.getenv("TEST_LOAD_REQUESTS", "500"))
    if not 1 <= request_count <= 2000:
        pytest.fail("TEST_LOAD_REQUESTS must be between 1 and 2000.")

    concurrency = min(25, request_count)
    latencies: list[float] = []

    async def run_load_test():
        semaphore = asyncio.Semaphore(concurrency)
        transport = httpx.ASGITransport(app=user_service.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://local-test",
        ) as client:
            async def create_user(index: int):
                payload = {
                    "name": f"Load User {index}",
                    "email": f"load-{index}@example.test",
                }
                async with semaphore:
                    started = time.perf_counter()
                    response = await client.post("/users", json=payload)
                    latencies.append(time.perf_counter() - started)
                return response

            return await asyncio.gather(
                *(create_user(index) for index in range(request_count))
            )

    started = time.perf_counter()
    responses = asyncio.run(run_load_test())
    elapsed = time.perf_counter() - started

    assert all(response.status_code == 201 for response in responses)
    assert len({response.json()["id"] for response in responses}) == request_count
    sorted_latencies = sorted(latencies)
    p95 = sorted_latencies[min(len(sorted_latencies) - 1, int(len(sorted_latencies) * 0.95))]
    print(
        f"local ASGI load: {request_count} requests, concurrency={concurrency}, "
        f"elapsed={elapsed:.2f}s, throughput={request_count / elapsed:.1f} req/s, "
        f"p95={p95 * 1000:.1f}ms, mean={statistics.mean(latencies) * 1000:.1f}ms"
    )
