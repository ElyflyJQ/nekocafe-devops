"""
NekoCafé Reservation Service — FastAPI implementation.
Matches D2-5 reservation-service.yaml OpenAPI contract.
"""
import time
import uuid
import logging
import json
import os
from datetime import datetime, timezone
from typing import Optional, List
from enum import Enum

from fastapi import FastAPI, HTTPException, Query, Path, Body
from pydantic import BaseModel, Field
import redis.asyncio as redis  # noqa: F401

# === Observability: OpenTelemetry ===
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor

# Structured JSON logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("reservation-service")

class StructuredFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": "reservation",
            "message": record.getMessage(),
            "traceId": getattr(record, "traceId", None),
        }
        return json.dumps(log_entry)

for handler in logging.root.handlers:
    handler.setFormatter(StructuredFormatter())

tracer = trace.get_tracer("reservation-service")

# === App ===
app = FastAPI(
    title="Reservation Service API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)
FastAPIInstrumentor.instrument_app(app)

# === Redis ===
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
redis_client: Optional[redis.Redis] = None

@app.on_event("startup")
async def startup():
    global redis_client
    redis_client = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    RedisInstrumentor().instrument()
    logger.info("Reservation service started")

@app.on_event("shutdown")
async def shutdown():
    if redis_client:
        await redis_client.close()

# === Models ===
class ReservationStatus(str, Enum):
    PENDING_DEPOSIT = "PENDING_DEPOSIT"
    CONFIRMED = "CONFIRMED"
    CHECKED_IN = "CHECKED_IN"
    SEATED = "SEATED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"

class Money(BaseModel):
    amount: float
    currency: str = "CNY"

class CreateReservationRequest(BaseModel):
    customer_id: str
    store_id: str
    slot_id: str
    party_size: int = Field(ge=1, le=20)
    special_requests: Optional[str] = Field(None, max_length=500)
    cat_preferences: Optional[List[str]] = Field(None, max_items=5)

class ConfirmReservationRequest(BaseModel):
    payment_transaction_id: str
    paid_amount: Money

class CheckinRequest(BaseModel):
    checkin_method: str = Field("QR_SCAN", pattern=r'^(QR_SCAN|STAFF_MANUAL)$')
    assign_table_immediately: bool = True

class QueueTicketRequest(BaseModel):
    customer_id: str
    party_size: int = Field(ge=1, le=20)
    special_needs: Optional[str] = None

# In-memory stores
_reservations: dict = {}
_slots: dict = {}
_queues: dict = {}
_slot_counter = 0
_queue_counter = 0

def _seed_slots(store_id: str):
    global _slot_counter
    slots = []
    for h in range(10, 21):
        for m in (0, 30):
            slot_id = f"slot-{store_id}-{_slot_counter}"
            _slot_counter += 1
            s = {
                "slot_id": slot_id,
                "store_id": store_id,
                "start_time": f"2026-06-20T{h:02d}:{m:02d}:00+08:00",
                "end_time": f"2026-06-20T{h:02d}:{(m+30):02d}:00+08:00",
                "table_type": "CAT_ZONE",
                "available_tables": 3,
                "total_tables": 8,
                "min_deposit": 50.00,
            }
            slots.append(s)
            _slots[slot_id] = s
    return slots

# === Health ===
@app.get("/healthz")
async def healthz():
    return {"status": "healthy", "service": "reservation"}

@app.get("/readyz")
async def readyz():
    if redis_client:
        await redis_client.ping()
    return {"status": "ready"}

# === Slots ===
@app.get("/v1/stores/{store_id}/slots", summary="查询门店可预约时段", tags=["Slots"])
async def get_available_slots(
    store_id: str = Path(description="门店ID"),
    date_q: str = Query("2026-06-20", alias="date"),
    table_type: Optional[str] = Query(None, pattern=r'^(WINDOW|CAT_ZONE|PRIVATE_ROOM|STANDARD)$'),
    party_size: Optional[int] = Query(None, ge=1, le=20),
):
    with tracer.start_as_current_span("get_slots") as span:
        span.set_attribute("store_id", store_id)
        if not any(s["store_id"] == store_id for s in _slots.values()):
            _seed_slots(store_id)
        results = [s for s in _slots.values() if s["store_id"] == store_id]
        if table_type:
            results = [s for s in results if s["table_type"] == table_type]
        return results

# === Reservations ===
@app.get("/v1/reservations", summary="查询预约列表", tags=["Reservations"])
async def list_reservations(
    customer_id: str = Query(description="顾客ID"),
    status: Optional[ReservationStatus] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    results = [r for r in _reservations.values() if r["customer_id"] == customer_id]
    if status:
        results = [r for r in results if r["status"] == status.value]
    total = len(results)
    start = (page - 1) * size
    return {
        "content": results[start:start+size],
        "page": page, "size": size,
        "total_elements": total,
        "total_pages": max(1, (total + size - 1) // size),
    }

@app.post("/v1/reservations", summary="创建预约", status_code=201, tags=["Reservations"])
async def create_reservation(body: CreateReservationRequest):
    with tracer.start_as_current_span("create_reservation") as span:
        span.set_attribute("customer_id", body.customer_id)
        span.set_attribute("store_id", body.store_id)

        slot = _slots.get(body.slot_id)
        if not slot:
            raise HTTPException(404, detail="时段不存在")

        # Distributed lock via Redis
        lock_key = f"slot_lock:{body.store_id}:{body.slot_id}"
        if redis_client:
            acquired = await redis_client.set(lock_key, "locked", nx=True, ex=30)
            if not acquired:
                raise HTTPException(409, detail="该时段正在被其他请求处理")
            try:
                if slot["available_tables"] <= 0:
                    raise HTTPException(409, detail="该时段已无可用桌位")
                slot["available_tables"] -= 1
            finally:
                await redis_client.delete(lock_key)
        else:
            if slot["available_tables"] <= 0:
                raise HTTPException(409, detail="该时段已无可用桌位")
            slot["available_tables"] -= 1

        resv_id = f"resv-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        reservation = {
            "reservation_id": resv_id,
            "customer_id": body.customer_id,
            "store_id": body.store_id,
            "store_name": f"NekoCafé {body.store_id}",
            "table_id": None,
            "time_slot": {"date": "2026-06-20", "start_time": slot["start_time"][11:16], "end_time": slot["end_time"][11:16]},
            "party_size": body.party_size,
            "status": "PENDING_DEPOSIT",
            "deposit": {"amount": slot["min_deposit"], "currency": "CNY"},
            "deposit_status": "UNPAID",
            "special_requests": body.special_requests,
            "cat_preferences": body.cat_preferences or [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "confirmed_at": None,
            "cancelled_at": None,
        }
        _reservations[resv_id] = reservation
        logger.info(json.dumps({"event": "reservation_created", "reservation_id": resv_id}))
        return reservation

@app.get("/v1/reservations/{reservation_id}", summary="查询预约详情", tags=["Reservations"])
async def get_reservation(reservation_id: str = Path()):
    r = _reservations.get(reservation_id)
    if not r:
        raise HTTPException(404, detail="预约不存在")
    return r

@app.delete("/v1/reservations/{reservation_id}", summary="取消预约", tags=["Reservations"])
async def cancel_reservation(reservation_id: str = Path(), reason: Optional[str] = Query(None)):
    r = _reservations.get(reservation_id)
    if not r:
        raise HTTPException(404, detail="预约不存在")

    hours_until = 48  # Simplified: assume > 2h before reservation
    if hours_until >= 2:
        penalty, refund = 0.0, r["deposit"]["amount"]
    else:
        penalty = r["deposit"]["amount"] * 0.5
        refund = r["deposit"]["amount"] - penalty

    r["status"] = "CANCELLED"
    r["cancelled_at"] = datetime.now(timezone.utc).isoformat()
    r["deposit_status"] = "REFUNDED"
    for s in _slots.values():
        if s["store_id"] == r["store_id"]:
            s["available_tables"] = min(s["total_tables"], s["available_tables"] + 1)
            break

    return {
        "reservation_id": reservation_id, "status": "CANCELLED",
        "refund_amount": {"amount": refund, "currency": "CNY"},
        "penalty_amount": {"amount": penalty, "currency": "CNY"},
    }

@app.post("/v1/reservations/{reservation_id}/confirm", summary="确认预约", tags=["Reservations"])
async def confirm_reservation(reservation_id: str = Path(), body: ConfirmReservationRequest = Body()):
    r = _reservations.get(reservation_id)
    if not r:
        raise HTTPException(404)
    if r["status"] != "PENDING_DEPOSIT":
        raise HTTPException(422, detail="预约状态不允许确认")
    if abs(body.paid_amount.amount - r["deposit"]["amount"]) > 0.01:
        raise HTTPException(402, detail="支付金额与定金不匹配")
    r["status"] = "CONFIRMED"
    r["deposit_status"] = "PAID"
    r["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    return r

# === Checkin ===
@app.post("/v1/reservations/{reservation_id}/checkin", summary="到店签到", tags=["Checkin"])
async def checkin(reservation_id: str = Path(), body: CheckinRequest = Body(default_factory=CheckinRequest)):
    r = _reservations.get(reservation_id)
    if not r:
        raise HTTPException(404)
    if r["status"] != "CONFIRMED":
        raise HTTPException(422, detail="预约未确认或已过期")
    r["status"] = "CHECKED_IN"
    r["checkin_time"] = datetime.now(timezone.utc).isoformat()
    assigned_table = {
        "table_id": f"table-{uuid.uuid4().hex[:4]}",
        "table_number": "A-12", "table_type": "CAT_ZONE",
        "zone": "猫咪互动区", "capacity": 4, "cat_names": ["布丁", "奶茶"],
    }
    r["table_id"] = assigned_table["table_id"]
    return {"reservation_id": reservation_id, "checkin_time": r["checkin_time"], "assigned_table": assigned_table, "status": "CHECKED_IN"}

@app.post("/v1/reservations/{reservation_id}/no-show", summary="标记爽约", tags=["Checkin"])
async def mark_no_show(reservation_id: str = Path()):
    r = _reservations.get(reservation_id)
    if not r:
        raise HTTPException(404)
    r["status"] = "NO_SHOW"
    return {"reservation_id": reservation_id, "status": "NO_SHOW"}

# === Queue ===
@app.get("/v1/stores/{store_id}/queue", summary="查询排队状态", tags=["Queue"])
async def get_queue_status(store_id: str = Path(), customer_id: Optional[str] = Query(None)):
    store_queue = [q for q in _queues.values() if q["store_id"] == store_id and q["status"] == "WAITING"]
    store_queue.sort(key=lambda x: (-x["priority"], x["created_at"]))
    my_ticket = None
    if customer_id:
        for q in store_queue:
            if q["customer_id"] == customer_id:
                my_ticket = q
                break
    return {"store_id": store_id, "total_waiting": len(store_queue), "current_serving_number": 0, "my_ticket": my_ticket}

@app.post("/v1/stores/{store_id}/queue", summary="取号排队", status_code=201, tags=["Queue"])
async def take_queue_ticket(store_id: str = Path(), body: QueueTicketRequest = Body()):
    global _queue_counter
    _queue_counter += 1
    ticket = {
        "ticket_id": f"queue-{uuid.uuid4().hex[:6]}",
        "ticket_number": _queue_counter, "store_id": store_id,
        "customer_id": body.customer_id, "party_size": body.party_size,
        "priority": 1000000 - int(time.time() * 1000),
        "estimated_wait_minutes": len([q for q in _queues.values() if q["store_id"] == store_id]) * 5,
        "position_in_queue": len([q for q in _queues.values() if q["store_id"] == store_id]),
        "status": "WAITING", "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _queues[ticket["ticket_id"]] = ticket
    return ticket

@app.post("/v1/stores/{store_id}/queue/call-next", summary="叫号", tags=["Queue"])
async def call_next(store_id: str = Path()):
    store_queue = [q for q in _queues.values() if q["store_id"] == store_id and q["status"] == "WAITING"]
    if not store_queue:
        raise HTTPException(404, detail="队列为空")
    store_queue.sort(key=lambda x: (-x["priority"], x["created_at"]))
    store_queue[0]["status"] = "CALLED"
    return store_queue[0]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
