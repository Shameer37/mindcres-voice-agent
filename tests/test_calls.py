def test_create_call_for_existing_customer(client, test_customer):
    response = client.post(
        "/calls",
        json={"customer_id": test_customer},
    )

    assert response.status_code == 201

    body = response.json()
    assert body["customer_id"] == test_customer
    assert body["status"] == "created"
    assert body["id"]


def test_create_call_for_unknown_customer(client):
    response = client.post(
        "/calls",
        json={"customer_id": "DOES_NOT_EXIST"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Customer not found."


def test_list_calls_and_filter_by_followup(
    client,
    test_customer,
    test_session_factory,
):
    from app.models import Call

    client.post("/calls", json={"customer_id": test_customer})

    with test_session_factory() as db:
        call = db.query(Call).first()
        call.needs_followup = True
        call.outcome = "followup_required"
        db.commit()

    response = client.get(
        "/calls",
        params={"outcome": "followup_required", "needs_followup": "true"},
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["needs_followup"] is True


def test_get_call_detail(client, test_customer):
    create_response = client.post(
        "/calls",
        json={"customer_id": test_customer},
    )
    call_id = create_response.json()["id"]

    response = client.get(f"/calls/{call_id}")

    assert response.status_code == 200
    assert response.json()["id"] == call_id
    assert response.json()["transcript"] == []
    assert response.json()["turn_metrics"] == []


def test_unknown_call_returns_404(client):
    response = client.get("/calls/unknown-call-id")

    assert response.status_code == 404