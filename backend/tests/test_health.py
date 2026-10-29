def test_health_endpoint_returns_service_status(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["service"] == "samplebusiness-ai-company"
    assert response.json()["status"] == "ok"
    assert response.json()["timestamp"].endswith("+00:00")
