import httpx


async def test_health_is_stable_across_repeated_requests(client: httpx.AsyncClient) -> None:
    first_response = await client.get("/health")
    second_response = await client.get("/health")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json() == second_response.json() == {"status": "ok"}
