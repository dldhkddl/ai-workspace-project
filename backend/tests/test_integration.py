def test_backend_contract_has_no_fake_work_data(client):
    employees = client.get("/api/v1/employees").json()

    assert len(employees) == 26
    assert all(employee["connection"]["status"] == "not_connected" for employee in employees)
    assert all(employee["work"]["status"] is None for employee in employees)
    assert all(employee["work"]["progress"] is None for employee in employees)
    assert all(employee["work"]["task_id"] is None for employee in employees)
