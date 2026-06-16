"""Smoke tests for Reservation Service."""
import pytest
from httpx import ASGITransport, AsyncClient
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_healthz(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["service"] == "reservation"


@pytest.mark.asyncio
async def test_readyz(client):
    resp = await client.get("/readyz")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_slots(client):
    resp = await client.get("/v1/stores/store-001/slots?date=2026-06-20")
    assert resp.status_code == 200
    slots = resp.json()
    assert len(slots) > 0
    assert "slot_id" in slots[0]


@pytest.mark.asyncio
async def test_create_reservation(client):
    resp = await client.get("/v1/stores/store-001/slots?date=2026-06-20")
    slot_id = resp.json()[0]["slot_id"]
    resp = await client.post("/v1/reservations", json={
        "customer_id": "cust-test-001",
        "store_id": "store-001",
        "slot_id": slot_id,
        "party_size": 4,
        "cat_preferences": ["英短", "布偶"],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "PENDING_DEPOSIT"
    return data["reservation_id"]


@pytest.mark.asyncio
async def test_get_reservation(client):
    resv_id = await test_create_reservation(client)
    resp = await client.get(f"/v1/reservations/{resv_id}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_confirm_reservation(client):
    resv_id = await test_create_reservation(client)
    resp = await client.post(f"/v1/reservations/{resv_id}/confirm", json={
        "payment_transaction_id": "txn-001",
        "paid_amount": {"amount": 50.00, "currency": "CNY"},
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_checkin(client):
    resv_id = await test_create_reservation(client)
    await client.post(f"/v1/reservations/{resv_id}/confirm", json={
        "payment_transaction_id": "txn-001",
        "paid_amount": {"amount": 50.00, "currency": "CNY"},
    })
    resp = await client.post(f"/v1/reservations/{resv_id}/checkin", json={"checkin_method": "QR_SCAN"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "CHECKED_IN"


@pytest.mark.asyncio
async def test_cancel_reservation(client):
    resv_id = await test_create_reservation(client)
    resp = await client.delete(f"/v1/reservations/{resv_id}?reason=USER_CANCEL")
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_queue(client):
    resp = await client.post("/v1/stores/store-001/queue", json={
        "customer_id": "cust-001", "party_size": 4,
    })
    assert resp.status_code == 201
    resp = await client.get("/v1/stores/store-001/queue")
    assert resp.status_code == 200
