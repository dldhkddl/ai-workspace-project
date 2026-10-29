def test_office_and_employee_routes(client):
    office = client.get("/api/v1/office")
    assert office.status_code == 200
    assert len(office.json()["employees"]) == 26
    assert len(office.json()["teams"]) == 10

    teams = client.get("/api/v1/teams")
    assert teams.status_code == 200
    assert len(teams.json()) == 10

    filtered = client.get("/api/v1/employees?team_id=operations")
    assert filtered.status_code == 200
    assert len(filtered.json()) == 2

    missing = client.get("/api/v1/employees/missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "EMPLOYEE_NOT_FOUND"


def test_version_and_connections_use_contract(client):
    version = client.get("/api/v1/version")
    assert version.status_code == 200
    assert version.json()["service"] == "samplebusiness-ai-company"

    connections = client.get("/api/v1/connections")
    assert connections.status_code == 200
    assert len(connections.json()) == 26
    assert {item["status"] for item in connections.json()} == {"not_connected"}
