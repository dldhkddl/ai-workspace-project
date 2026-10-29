TASK_REQUEST = {
    "employee_id": "research-factcheck",
    "task_type": "employee_task",
    "title": "주문 현황 초안",
    "payload": {"range": "today"},
}


def create_task(client, key: str = "event-task"):
    return client.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": key},
        json=TASK_REQUEST,
    )


def test_task_creation_is_exposed_as_durable_activity_and_event(client):
    task = create_task(client).json()

    activities = client.get("/api/v1/activities").json()
    events = client.get("/api/v1/events?after_id=0").json()
    latest = client.get("/api/v1/events/latest").json()

    assert activities[0]["event_type"] == "task.queued"
    assert activities[0]["task_id"] == task["id"]
    assert activities[0]["employee_id"] == task["employee_id"]
    assert activities[0]["title"] == "작업이 대기열에 등록되었습니다"
    assert events[0]["event_type"] == "activity.created"
    assert events[0]["resource_id"] == activities[0]["id"]
    assert events[0]["payload"]["task_id"] == task["id"]
    assert latest["last_event_id"] == events[-1]["id"]


def test_event_replay_only_returns_events_after_cursor(client):
    create_task(client, "event-first")
    first_event = client.get("/api/v1/events?after_id=0").json()[0]
    create_task(client, "event-second")

    replay = client.get(f"/api/v1/events?after_id={first_event['id']}").json()

    assert replay
    assert all(event["id"] > first_event["id"] for event in replay)
    assert [event["id"] for event in replay] == sorted(event["id"] for event in replay)


def test_websocket_replays_existing_events_from_cursor(client):
    task = create_task(client, "websocket-event").json()

    with client.websocket_connect("/api/v1/ws/events?after_id=0") as websocket:
        event = websocket.receive_json()

    assert event["event_type"] == "activity.created"
    assert event["payload"]["task_id"] == task["id"]


def test_queued_task_cancellation_creates_activity(client):
    task = create_task(client, "cancel-event").json()
    assert client.post(f"/api/v1/tasks/{task['id']}/cancel").status_code == 200

    activities = client.get(f"/api/v1/activities?task_id={task['id']}").json()

    assert [activity["event_type"] for activity in activities] == [
        "task.cancelled",
        "task.queued",
    ]


def test_alert_endpoint_starts_empty(client):
    assert client.get("/api/v1/alerts").json() == []
