# app.py
from datetime import datetime, date
from typing import List, Optional, Dict, Literal
import json, hashlib
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Float, ForeignKey, Boolean, Text, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.orm import joinedload
from zoneinfo import ZoneInfo
from datetime import datetime, timezone
from fastapi import status

# ---------- DB (共有モジュールから) ----------
from db_shared import Base, engine, SessionLocal

app = FastAPI(title="Cabaret POS Full")

# ---------- Auth / Role ----------
Role = Literal["owner", "manager", "cashier", "staff"]
def require_role(role_header: Optional[str], allowed: List[str]):
    if not role_header:
        raise HTTPException(401, "Missing X-Role")
    if role_header not in allowed:
        raise HTTPException(403, f"Role '{role_header}' not allowed for this action")

# ---------- Models ----------
class Store(Base):
    __tablename__ = "stores"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)

class Table(Base):
    __tablename__ = "tables"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"))
    name = Column(String)
    store = relationship("Store")

class Cast(Base):
    __tablename__ = "casts"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"))
    name = Column(String, index=True)
    rank = Column(String, default="")
    is_active = Column(Boolean, default=True)
    store = relationship("Store")

class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"))
    name = Column(String, index=True)
    category = Column(String) # set/drink/bottle/food/other
    price = Column(Float, default=0.0)
    stock = Column(Integer, default=0)
    keepable = Column(Boolean, default=False)
    capacity_ml = Column(Integer, default=0)
    store = relationship("Store")

class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"))
    nickname = Column(String, index=True)
    phone = Column(String, default="")
    memo = Column(Text, default="")
    is_ng = Column(Boolean, default=False)
    store = relationship("Store")

class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"))
    table_id = Column(Integer, ForeignKey("tables.id"))
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    guest_count = Column(Integer, default=1)
    set_minutes = Column(Integer, default=60)
    extend_unit = Column(Integer, default=30)
    status = Column(String, default="open")
    note = Column(Text, default="")
    table = relationship("Table")
    customer = relationship("Customer")
    orders = relationship("Order", back_populates="session", cascade="all,delete")
    nominations = relationship("Nomination", back_populates="session", cascade="all,delete")
    payments = relationship("Payment", back_populates="session", cascade="all,delete")

# ---- Session -> レスポンス用の辞書に安全変換（テーブルを含む） ----
def session_to_out_dict(s: Session) -> Dict:
    return {
        "id": s.id,
        "store_id": s.store_id,
        "table": {
            "id": s.table.id if s.table else None,
            "store_id": s.table.store_id if s.table else None,
            "name": s.table.name if s.table else None,
        },
        "start_time": s.start_time,
        "end_time": s.end_time,
        "guest_count": s.guest_count,
        "status": s.status,
    }

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"))
    session_id = Column(Integer, ForeignKey("sessions.id"))
    item_id = Column(Integer, ForeignKey("items.id"))
    qty = Column(Integer, default=1)
    unit_price = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    session = relationship("Session", back_populates="orders")
    item = relationship("Item")

class Nomination(Base):
    __tablename__ = "nominations"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"))
    session_id = Column(Integer, ForeignKey("sessions.id"))
    cast_id = Column(Integer, ForeignKey("casts.id"))
    nomi_type = Column(String) # hon/jyonai/dohan
    fee = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    session = relationship("Session", back_populates="nominations")
    cast = relationship("Cast")

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"))
    session_id = Column(Integer, ForeignKey("sessions.id"))
    method = Column(String) # cash/card/qr
    amount = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    session = relationship("Session", back_populates="payments")

# --- 会計用 ---
class BusinessProfile(Base):
    __tablename__ = "business_profiles"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, index=True)
    legal_name = Column(String, default="株式会社サンプル")
    address = Column(String, default="東京都品川区1-2-3")
    invoice_reg_no = Column(String, default="T1234567890123")
    tel = Column(String, default="03-0000-0000")
    email = Column(String, default="info@example.com")
    bank = Column(String, default="三井住友銀行 ○○支店 普通 1234567")
    note = Column(String, default="")

class InvoiceSeq(Base):
    __tablename__ = "invoice_seq"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, index=True)
    yyyymm = Column(String, index=True)
    seq = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint("store_id", "yyyymm", name="uniq_seq_ym"),)

class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, index=True)
    session_id = Column(Integer, index=True)
    invoice_no = Column(String, index=True, unique=True)
    issued_at = Column(DateTime, default=datetime.utcnow)
    customer = Column(String, default="")
    total = Column(Integer, default=0)
    tax10 = Column(Integer, default=0)
    tax8 = Column(Integer, default=0)
    body_json = Column(Text)
    hash = Column(String)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=datetime.utcnow)
    actor_role = Column(String)
    path = Column(String)
    method = Column(String)
    payload = Column(Text)
    ip = Column(String)
    hash = Column(String)

class PrintLog(Base):
    __tablename__ = "print_logs"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=datetime.utcnow)
    store_id = Column(Integer, index=True)
    session_id = Column(Integer, nullable=True)
    invoice_no = Column(String, nullable=True)
    kind = Column(String) # receipt / invoice
    actor_role = Column(String)

class Cashbook(Base):
    __tablename__ = "cashbook"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=datetime.utcnow)
    store_id = Column(Integer, index=True)
    kind = Column(String) # in/out
    amount = Column(Integer)
    memo = Column(String, default="")
    actor_role = Column(String)

class Attendance(Base):
    __tablename__ = "attendance"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, index=True)
    person_type = Column(String)
    person_id = Column(Integer)
    clock_in = Column(DateTime)
    clock_out = Column(DateTime, nullable=True)

# ---------- Schemas ----------
class TableIn(BaseModel):
    store_id: int
    name: str
class TableOut(TableIn):
    id: int
    class Config: model_config = {"from_attributes": True}

class CastIn(BaseModel):
    store_id: int
    name: str
class CastOut(CastIn):
    id: int
    class Config: model_config = {"from_attributes": True}

class ItemIn(BaseModel):
    store_id: int
    name: str
    category: Literal["set","drink","bottle","food","other"]
    price: float
class ItemOut(ItemIn):
    id: int
    class Config: model_config = {"from_attributes": True}

class SessionStartIn(BaseModel):
    store_id: int
    table_id: int
    guest_count: int = 1
    set_minutes: int = 60
    extend_unit: int = 30
class SessionOut(BaseModel):
    id: int
    store_id: int
    table: TableOut
    start_time: datetime
    end_time: Optional[datetime]
    guest_count: int
    status: str
    class Config: model_config = {"from_attributes": True}

class OrderIn(BaseModel):
    store_id: int
    item_id: int
    qty: int = Field(gt=0, default=1)

class PaymentIn(BaseModel):
    store_id: int
    method: Literal["cash","card","qr"]
    amount: float

# ---------- 初期化 ----------
def seed():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if db.query(Store).count() == 0:
            s1 = Store(name="本店")
            db.add(s1); db.commit(); db.refresh(s1)
            db.add_all([Table(store_id=s1.id, name="T-1"), Table(store_id=s1.id, name="T-2")])
            db.add_all([Cast(store_id=s1.id, name="みさき"), Cast(store_id=s1.id, name="ゆい")])
            db.add_all([
                Item(store_id=s1.id, name="セット60", category="set", price=6000),
                Item(store_id=s1.id, name="延長30", category="set", price=3000),
                Item(store_id=s1.id, name="生ビール", category="drink", price=800),
                Item(store_id=s1.id, name="ハイボール", category="drink", price=700),
                Item(store_id=s1.id, name="シャンパン", category="bottle", price=15000),
                Item(store_id=s1.id, name="ワイン", category="bottle", price=9000),
                Item(store_id=s1.id, name="枝豆", category="food", price=400),
                Item(store_id=s1.id, name="唐揚げ", category="food", price=600),
            ])
            db.commit()
    finally:
        db.close()
seed()

# ---------- 拡張モジュールのテーブル作成 ----------
try:
    from pricing_engine import PricingConfig, TimeSlotRule
    from cast_salary import CastSalaryConfig, DrinkBackRecord
    from weather_service import WeatherConfig, StaffSchedule
    from stripe_service import StripeSubscription, StripeConfig
    Base.metadata.create_all(engine)
except Exception as _ext_err:
    print(f"[warn] 拡張モジュール読み込み: {_ext_err}")

# ---------- 会計計算 ----------
def compute_bill(db, s: Session) -> Dict:
    # 料金ルールエンジンから設定を取得
    try:
        from pricing_engine import get_pricing_config, get_slot_rule, compute_night_surcharge, compute_totals
        config   = get_pricing_config(db, s.store_id)
        slot     = get_slot_rule(db, s.store_id, s.start_time)
        set_fee    = slot.set_price    if slot else 6000.0
        extend_fee = slot.extend_price if slot else 3000.0
    except Exception:
        config = None
        set_fee = 6000.0
        extend_fee = 3000.0

    end_time = s.end_time or datetime.utcnow()

    total_minutes  = max(0, int((end_time - s.start_time).total_seconds() // 60))
    booked_minutes = int(s.set_minutes or 60)
    sets      = 1
    remaining = max(0, total_minutes - booked_minutes)
    extends   = (remaining + s.extend_unit - 1) // s.extend_unit if remaining > 0 else 0

    set_amount    = sets * set_fee * s.guest_count
    extend_amount = extends * extend_fee * s.guest_count
    time_amount   = set_amount + extend_amount

    # お通し/TC・VIP席料
    table_charge = 0.0
    vip_fee      = 0.0
    if config:
        table_charge = (config.table_charge_pp or 0) * s.guest_count
        vip_fee      = config.vip_seat_fee or 0

    order_subtotal = sum(o.unit_price * o.qty for o in s.orders)
    subtotal = time_amount + order_subtotal + table_charge + vip_fee

    # 深夜加算
    try:
        night_add = compute_night_surcharge(config, subtotal, s.start_time)
    except Exception:
        night_add = 0.0

    # SC・税・合計（設定に従って計算）
    try:
        totals = compute_totals(subtotal, night_add, config)
        service_fee = totals["service_fee"]
        tax         = totals["tax"]
        total       = totals["total"]
    except Exception:
        service_fee = int(round(subtotal * 0.10))
        tax         = int(round((subtotal + service_fee) * 0.10))
        total       = int(round(subtotal + service_fee + tax))

    paid = int(round(sum(p.amount for p in s.payments)))
    due  = max(0, total - paid)

    return {
        "session_id": s.id,
        "store": s.store_id,
        "table": s.table.name if s.table else None,
        "start_time": s.start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "guest_count": s.guest_count,
        "booked_minutes": booked_minutes,
        "elapsed_minutes": total_minutes,
        "time_breakdown": {
            "total_minutes": total_minutes,
            "sets": int(sets),
            "extends": int(extends),
            "set_amount": float(set_amount),
            "extend_amount": float(extend_amount),
            "time_amount": float(time_amount),
        },
        "table_charge": float(table_charge),
        "vip_fee": float(vip_fee),
        "night_surcharge": float(night_add),
        "orders": [
            {"name": o.item.name, "qty": o.qty, "amount": o.unit_price * o.qty}
            for o in s.orders
        ],
        "order_subtotal": order_subtotal,
        "subtotal": subtotal,
        "service_fee": service_fee,
        "tax": tax,
        "total": total,
        "paid": paid,
        "due": due,
    }

# --- インボイス発行 ---
def issue_invoice(db, s: Session):
    bill = compute_bill(db, s)
    no = datetime.utcnow().strftime("%Y%m") + "-" + f"{s.id:04d}"
    inv = Invoice(
        store_id=s.store_id,
        session_id=s.id,
        invoice_no=no,
        issued_at=datetime.utcnow(),
        customer=bill.get("customer") or "",
        total=int(bill["total"]),
        tax10=int(bill["tax"]),
        tax8=0,
        body_json=json.dumps(bill, ensure_ascii=False),
        hash=hashlib.sha256((no + str(int(bill["total"]))).encode()).hexdigest()
    )
    db.add(inv); db.commit(); db.refresh(inv)
    return inv

# ---------- API ----------
@app.post("/tables", response_model=TableOut)
def create_table(payload: TableIn, x_role: Optional[Role] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager"])
    db = SessionLocal()
    try:
        t = Table(**payload.dict())
        db.add(t); db.commit(); db.refresh(t)
        return t
    finally:
        db.close()

@app.get("/tables", response_model=List[TableOut])
def list_tables(store_id:int, x_role: Optional[Role]=Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager","cashier","staff"])
    db=SessionLocal()
    try:
        return db.query(Table).filter_by(store_id=store_id).all()
    finally:
        db.close()

@app.post("/items", response_model=ItemOut)
def create_item(payload: ItemIn, x_role: Optional[Role] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager"])
    db=SessionLocal()
    try:
        it=Item(**payload.dict()); db.add(it); db.commit(); db.refresh(it); return it
    finally:
        db.close()

@app.get("/items", response_model=List[ItemOut])
def list_items(store_id:int, x_role: Optional[Role]=Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager","cashier","staff"])
    db=SessionLocal()
    try:
        return db.query(Item).filter_by(store_id=store_id).all()
    finally:
        db.close()

@app.post("/sessions", response_model=SessionOut)
def start_session(payload: SessionStartIn, x_role: Optional[Role]=Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        # テーブル存在チェック（任意）
        tbl = db.query(Table).filter_by(id=payload.table_id, store_id=payload.store_id).first()
        if not tbl:
            raise HTTPException(404, "Table not found")

        s = Session(
            store_id=payload.store_id,
            table_id=payload.table_id,
            guest_count=payload.guest_count,
            set_minutes=payload.set_minutes,
            extend_unit=payload.extend_unit,
            start_time=datetime.utcnow(),
            status="open",
        )
        db.add(s)
        db.commit()
        db.refresh(s)

        # eager load してから辞書化
        s = db.query(Session).options(joinedload(Session.table)).get(s.id)
        out = SessionOut.model_validate(session_to_out_dict(s), from_attributes=True)
        return out
    finally:
        db.close()

@app.get("/sessions", response_model=List[SessionOut])
def list_sessions(
    store_id: int,
    status: Optional[str] = None,
    x_role: Optional[Role] = Header(None, alias="X-Role")
):
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        q = db.query(Session).options(joinedload(Session.table)).filter(Session.store_id == store_id)
        if status:
            q = q.filter(Session.status == status)
        rows = q.order_by(Session.id.desc()).all()

        # ここで辞書化してから返す（Pydanticがfrom_attributesで安全に変換）
        out = [SessionOut.model_validate(session_to_out_dict(s), from_attributes=True) for s in rows]
        return out
    finally:
        db.close()

@app.get("/sessions/{session_id}/bill")
def get_bill(session_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s: raise HTTPException(404, "Session not found")
        return compute_bill(db, s)
    finally:
        db.close()

@app.post("/sessions/{session_id}/orders")
def add_order(session_id: int, payload: OrderIn, x_role: Optional[Role] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s or s.status!="open": raise HTTPException(404, "Session not found")
        item = db.get(Item, payload.item_id)
        if not item: raise HTTPException(404, "Item not found")
        o = Order(store_id=s.store_id, session_id=session_id,
                  item_id=payload.item_id, qty=payload.qty, unit_price=item.price)
        db.add(o); db.commit()
        return {"ok": True, "order_id": o.id}
    finally:
        db.close()

@app.post("/sessions/{session_id}/payments")
def add_payment(session_id: int, payload: PaymentIn, x_role: Optional[Role] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s: raise HTTPException(404, "Session not found")
        p = Payment(store_id=s.store_id, session_id=session_id,
                    method=payload.method, amount=payload.amount)
        db.add(p); db.commit()
        return {"ok": True, "payment_id": p.id}
    finally:
        db.close()

class ExtendIn(BaseModel):
    minutes: int = 30

@app.post("/sessions/{session_id}/extend")
def extend_session(session_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s or s.status != "open":
            raise HTTPException(404, "Session not found or closed")
        s.set_minutes = int(s.set_minutes or 60) + int(s.extend_unit or 30)
        db.commit()
        return {"ok": True, "set_minutes": s.set_minutes}
    finally:
        db.close()

@app.post("/sessions/{session_id}/checkout")
def checkout(session_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager","cashier"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s: raise HTTPException(404, "Session not found")
        s.status = "closed"
        s.end_time = datetime.utcnow()
        inv = issue_invoice(db, s)
        db.commit()
        return {"ok": True, "invoice_no": inv.invoice_no}
    finally:
        db.close()

@app.post("/sessions/{session_id}/unextend")
def unextend_session(session_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """
    延長取消API：set_minutesをextend_unit分だけ減算する（最低限set_minutes>=extend_unit）
    """
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s or s.status != "open":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found or closed")
        s.set_minutes = max(s.extend_unit, int(s.set_minutes or 60) - int(s.extend_unit or 30))
        db.commit()
        return {"ok": True, "set_minutes": s.set_minutes}
    finally:
        db.close()


@app.delete("/sessions/{session_id}")
@app.post("/sessions/{session_id}/cancel") # UI側のフォールバックにも対応
def cancel_session(session_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """
    入店取消API：セッションを丸ごと削除する（openのみ）
    """
    require_role(x_role, ["owner","manager","cashier"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s or s.status != "open":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found or closed")
        db.delete(s)
        db.commit()
        return {"ok": True, "deleted": session_id}
    finally:
        db.close()


@app.post("/sessions/{session_id}/orders/cancel")
def cancel_order(session_id: int, payload: OrderIn, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """
    注文取消API：同じitem_idから指定数だけ減算（古い注文から順に）
    """
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s or s.status != "open":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found or closed")
        orders = (
            db.query(Order)
            .filter_by(session_id=session_id, item_id=payload.item_id)
            .order_by(Order.created_at.asc())
            .all()
        )
        to_cancel = payload.qty
        for o in orders:
            if to_cancel <= 0:
                break
            if o.qty <= to_cancel:
                to_cancel -= o.qty
                db.delete(o)
            else:
                o.qty -= to_cancel
                to_cancel = 0
        db.commit()
        return {"ok": True, "remaining": to_cancel}
    finally:
        db.close()

@app.get("/closing")
def closing(store_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    # 全ロール閲覧可（必要に応じて絞ってOK）
    require_role(x_role, ["owner", "manager", "cashier", "staff"])
    db = SessionLocal()
    try:
        jst = ZoneInfo("Asia/Tokyo")
        now_jst = datetime.now(tz=jst)
        start_jst = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)
        # DB は UTC 想定なので UTC に変換
        start_utc = start_jst.astimezone(timezone.utc)

        # 今日入店したセッション（open/closed 両方）を対象にする
        sessions = (
            db.query(Session)
            .options(
                joinedload(Session.orders).joinedload(Order.item),
                joinedload(Session.payments),
                joinedload(Session.table),
                )
                .filter(Session.store_id == store_id, Session.start_time >= start_utc)
                .all()
        )

        total_sales = 0
        sess_details = []
        for s in sessions:
            # OPEN のものは「今この瞬間まで」の見込み額で計算される
            bill = compute_bill(db, s)
            total_sales += int(bill.get("total", 0))
            sess_details.append({
                "session_id": s.id,
                "status": s.status,
                "table": s.table.name if s.table else None,
                "total": int(bill.get("total", 0)),
            })

        return {
            "store_id": store_id,
            "mode": "projected", # 見込み売上
            "total_sales": int(total_sales),
            "session_count": len(sessions),
            "period": {
            "start_jst": start_jst.isoformat(),
            "as_of_jst": now_jst.isoformat(),
            },
            # 必要なら UI デバッグ用にコメントアウト解除
            # "sessions": sess_details,
        }
    finally:
        db.close()

@app.post("/admin/seed_demo")
def seed_demo(store_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """ デモデータを追加入荷（重複は軽く抑制） """
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        store = db.query(Store).filter_by(id=store_id).first()
        if not store:
            store = Store(id=store_id, name=f"本店{store_id}")
            db.add(store); db.commit()
        # テーブル2つまで補充
        if db.query(Table).filter_by(store_id=store_id).count() < 2:
            db.add_all([Table(store_id=store_id, name="T-1"), Table(store_id=store_id, name="T-2")])
        # アイテム補充
        names = {i.name for i in db.query(Item).filter_by(store_id=store_id).all()}
        def add_if_missing(name, cat, price):
            if name not in names:
                db.add(Item(store_id=store_id, name=name, category=cat, price=price))
        add_if_missing("セット60", "set", 6000)
        add_if_missing("延長30", "set", 3000)
        add_if_missing("生ビール", "drink", 800)
        add_if_missing("ハイボール", "drink", 700)
        add_if_missing("シャンパン", "bottle", 15000)
        add_if_missing("ワイン", "bottle", 9000)
        add_if_missing("枝豆", "food", 400)
        add_if_missing("唐揚げ", "food", 600)
        db.commit()
        return {"ok": True}
    finally:
        db.close()

# ---------- 領収書API ----------
@app.get("/sessions/{session_id}/receipt")
def get_receipt(session_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """領収書データを返す（HTML印刷用）"""
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s:
            raise HTTPException(404, "Session not found")
        bill = compute_bill(db, s)
        profile = db.query(BusinessProfile).filter_by(store_id=s.store_id).first()
        invoice = db.query(Invoice).filter_by(session_id=session_id).first()
        return {
            "bill": bill,
            "invoice_no": invoice.invoice_no if invoice else f"TMP-{session_id}",
            "store": {
                "legal_name": profile.legal_name if profile else "店舗名",
                "address":    profile.address    if profile else "",
                "invoice_reg_no": profile.invoice_reg_no if profile else "",
                "tel":        profile.tel         if profile else "",
            }
        }
    finally:
        db.close()

# ---------- 拡張ルーターの登録 ----------
try:
    from pricing_engine import router as _pricing_router
    app.include_router(_pricing_router)
except Exception as e:
    print(f"[warn] pricing_engine router: {e}")
try:
    from cast_salary import router as _salary_router
    app.include_router(_salary_router)
except Exception as e:
    print(f"[warn] cast_salary router: {e}")
try:
    from weather_service import router as _weather_router
    app.include_router(_weather_router)
except Exception as e:
    print(f"[warn] weather_service router: {e}")
try:
    from stripe_service import router as _stripe_router
    app.include_router(_stripe_router)
except Exception as e:
    print(f"[warn] stripe_service router: {e}")

# ======================= UI (/ui) 完全版（取消＆数量管理つき） =======================
from fastapi.responses import HTMLResponse

@app.get("/ui", response_class=HTMLResponse)
def ui():
    return HTMLResponse(r"""
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Cabaret POS - Floor</title>
<style>
:root{
  --bg:#0b1220;--card:#0f172a;--line:#1f2937;--text:#e5e7eb;--muted:#b0bec5;--accent:#0ea5e9;
  --table-free:#e5e7eb; --t-ok:#ffffff; --t-warn:#facc15; --t-over:#ef4444; --t-paid:#3b82f6; --ink:#0b1220;
}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",Segoe UI,Roboto,sans-serif}
html,body{height:100%} body{margin:0;background:var(--bg);color:var(--text)}
header{position:sticky;top:0;z-index:40;display:flex;gap:12px;align-items:center;padding:12px 14px;border-bottom:1px solid var(--line);background:rgba(11,18,32,.9);backdrop-filter:blur(6px)}
header h1{margin:0 10px 0 0;font-size:18px}
select,input{font-size:16px;padding:8px 10px;border-radius:10px;border:1px solid #263244;background:var(--card);color:var(--text)}
.btn{cursor:pointer;font-size:15px;padding:8px 12px;border-radius:10px;border:1px solid #334155;background:#111827;color:var(--text)}
.btn.solid{background:var(--accent);border-color:var(--accent);color:#001018}
.page{display:grid;grid-template-columns:1fr 440px;gap:14px;padding:14px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.panel h2{margin:0;padding:12px 14px;border-bottom:1px solid var(--line);font-size:15px}
.p{padding:14px}
.floor-wrap{position:relative;height:68vh;border:1px dashed #223046;border-radius:12px;background:#0a1423;overflow:hidden}
.table{
  position:absolute;min-width:140px;min-height:86px;border:2px solid #cbd5e1;border-radius:14px;
  padding:10px 12px;cursor:pointer;user-select:none;background:#fff;color:var(--ink);
  box-shadow:0 8px 24px rgba(0,0,0,.18);
}
.table.sel{outline:3px solid #155e75}
.table .name{font-size:18px;font-weight:700}
.table .small{font-size:12px;opacity:.9}
.table .ttime{font-size:14px;font-weight:700;margin-top:4px}
.table.t-free{background:var(--table-free);color:#111827}
.table.t-ok{background:var(--t-ok);color:var(--ink)}
.table.t-warn{background:var(--t-warn);color:#1f2937}
.table.t-over{background:var(--t-over);color:#fff;border-color:#fee2e2}
.table.t-paid{background:var(--t-paid);color:#fff;border-color:#93c5fd}
.side{display:flex;flex-direction:column;gap:12px}
.card{background:#0c1626;border:1px solid #1f2b3f;border-radius:14px}
.card h3{margin:0;padding:10px 12px;border-bottom:1px solid #1f2b3f;font-size:14px}
.card .cbody{padding:10px 12px}
.tabs{display:flex;border-bottom:1px solid var(--line)}
.tab{flex:1;text-align:center;padding:10px 8px;cursor:pointer;border-bottom:2px solid transparent;font-size:14px}
.tab.active{border-color:var(--accent);font-weight:700;background:#0c1624}
.tabpanes>div{display:none}.tabpanes>div.active{display:block}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.bigbtn{cursor:pointer;font-size:16px;min-height:48px;padding:10px 12px;border-radius:12px;border:1px solid #334155;background:#111827;color:var(--text);width:100%;text-align:left}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.kv{display:flex;justify-content:space-between;gap:8px;margin:6px 0}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.muted{color:var(--muted)}
hr{border:0;border-top:1px solid var(--line);margin:10px 0}
#toasts{position:fixed;right:16px;bottom:16px;display:flex;flex-direction:column;gap:8px;z-index:60}
.toast{min-width:260px;max-width:420px;border-radius:12px;padding:10px 12px;border:1px solid #1f2f3f;background:#0e1a26;box-shadow:0 8px 24px rgba(0,0,0,.35);animation:slide .22s ease-out}
.toast.ok{border-color:#214a2c;background:#0f2615}.toast.err{border-color:#5c2328;background:#1a0e12}
.toast .title{font-weight:700;margin-bottom:4px}
@keyframes slide{from{transform:translateY(10px);opacity:0}to{transform:translateY(0);opacity:1}}
.badge{display:inline-block;padding:2px 6px;border-radius:999px;font-size:11px;border:1px solid rgba(255,255,255,.35)}
.badge.on{background:#14532d;border-color:#22c55e}
.badge.off{background:#2c1b1b;border-color:#ef4444}

/* 数量コントローラ */
.itemRow{display:flex;align-items:center;gap:8px;border:1px solid #2a384f;border-radius:10px;padding:8px}
.itemName{flex:1}
.qtyCtrl{display:flex;align-items:center;gap:6px}
.qtyCtrl button{width:34px;height:34px;border-radius:10px;border:1px solid #32445f;background:#0f1a2a;color:#e5e7eb;font-size:18px;cursor:pointer}
.qtyCtrl .val{min-width:28px;text-align:center;font-weight:700}

/* モバイル対応 */
@media(max-width:900px){
  header{flex-wrap:wrap;gap:8px;padding:10px 12px}
  header h1{font-size:15px}
  .page{grid-template-columns:1fr;gap:10px;padding:10px}
  .floor-wrap{height:40vh}
  .side{gap:10px}
  .grid{grid-template-columns:1fr}
  header div[style*="gap:6px"]{flex-wrap:wrap}
}
@media(max-width:500px){
  .page{padding:6px}
  .floor-wrap{height:35vh}
  .bigbtn{font-size:14px;padding:8px 10px;min-height:40px}
  .table{min-width:110px !important;min-height:70px !important}
}
</style>
</head>
<body>
<header>
  <h1>Girls Bar POS</h1>
  <label>Store <input id="storeId" type="number" value="1" style="width:70px"></label>
  <label>Role
    <select id="role">
      <option value="owner" selected>owner</option>
      <option value="manager">manager</option>
      <option value="cashier">cashier</option>
      <option value="staff">staff</option>
    </select>
  </label>
  <button id="seedBtn" class="btn">デモデータ</button>
  <label style="display:flex;align-items:center;gap:6px;">
    <input id="editToggle" type="checkbox"> 配置編集
  </label>
  <div style="display:flex;gap:6px;margin-left:8px">
    <a href="/ui/pricing" target="_blank" style="color:#0ea5e9;font-size:13px;padding:6px 10px;border-radius:8px;border:1px solid #1f2937;text-decoration:none">料金設定</a>
    <a href="/ui/salary" target="_blank" style="color:#0ea5e9;font-size:13px;padding:6px 10px;border-radius:8px;border:1px solid #1f2937;text-decoration:none">給与管理</a>
    <a href="/ui/weather" target="_blank" style="color:#0ea5e9;font-size:13px;padding:6px 10px;border-radius:8px;border:1px solid #1f2937;text-decoration:none">天気/シフト</a>
    <a href="/ui/subscription" target="_blank" style="color:#0ea5e9;font-size:13px;padding:6px 10px;border-radius:8px;border:1px solid #1f2937;text-decoration:none">サブスク</a>
  </div>
  <div class="muted" style="margin-left:auto">テーブル: <span id="selTable" class="mono">-</span> ／ SS: <span id="selSess" class="mono">-</span></div>
</header>

<div class="page">
  <section class="panel">
    <h2>フロア</h2>
    <div class="p"><div class="floor-wrap" id="floor"></div></div>
  </section>

  <aside class="side">
    <div class="card">
      <h3>操作</h3>
      <div class="cbody">
        <div class="tabs">
          <div class="tab active" data-tab="entry">入店/会計</div>
          <div class="tab" data-tab="drink">ドリンク</div>
          <div class="tab" data-tab="bottle">ボトル</div>
          <div class="tab" data-tab="food">フード</div>
        </div> 
        <div class="tabpanes">
          <div id="pane-entry" class="active">
            <div class="row" style="gap:8px 8px">
              <button class="bigbtn" id="btnCheckin">入店（60分）</button>
              <button class="bigbtn" id="btnExtend30">延長 +30分</button>
              <button class="bigbtn" id="btnUnextend">延長取消 -30分</button>
              <button class="bigbtn" id="btnAutoExtend">⏸️ 自動延長 OFF</button>
              <button class="bigbtn" id="btnCancelCheckin">入店取消</button>
              <input id="payAmount" class="bigbtn" style="width:160px" placeholder="金額を入力">
              <button class="bigbtn" id="btnPayCash">現金 入力金額</button>
              <button class="bigbtn solid" id="btnCheckout">会計確定</button>
              <button class="bigbtn" id="btnReceipt">🖨️ 領収書</button>
            </div>
            <hr>
            <div class="row" style="gap:16px">
              <div>
                <div class="muted">タイマー</div>
                <div class="mono" id="timerText" style="font-size:28px;font-weight:700">--:--</div>
                <div class="muted" id="timerDetail">経過 - / 予約 - / 残り -</div>
              </div>
              <div id="autoExtendBadge" class="badge off">自動延長: OFF</div>
            </div>
            <hr>
            <div id="billBox" class="muted mono" style="font-size:13px;line-height:1.5"></div>
          </div>

          <!-- 数量管理：各カテゴリ 0スタート + / - -->
          <div id="pane-drink">
            <div id="listDrink" class="grid"></div>
            <div class="row" style="justify-content:flex-end;margin-top:10px">
              <button class="btn" id="applyDrink">ドリンク反映</button>
            </div>
          </div>
          <div id="pane-bottle">
            <div id="listBottle" class="grid"></div>
            <div class="row" style="justify-content:flex-end;margin-top:10px">
              <button class="btn" id="applyBottle">ボトル反映</button>
            </div>
          </div>
          <div id="pane-food">
            <div id="listFood" class="grid"></div>
            <div class="row" style="justify-content:flex-end;margin-top:10px">
              <button class="btn" id="applyFood">フード反映</button>
            </div>
          </div>

        </div>
      </div>
    </div>

    <div class="card">
      <h3>今日の売上</h3>
      <div class="cbody">
        <div class="kv"><div class="muted">Total</div><div class="mono" id="salesToday">-</div></div>
      </div>
    </div>
  </aside>
</div>

<div id="toasts"></div>

<script>
/* ====== 共通 ====== */
const $ = (id)=>document.getElementById(id);
const role = ()=> $('role').value;
const store = ()=> parseInt($('storeId').value||'1',10);

let selectedTableId = null;
let currentSessionId = null;
let currentBill = null;
let loops = { tick:null, bill:null, sales:null, floor:null, floorTick:null };

const floorModel = {tables:[], tableEls:{}, sessionByTable:{}, billBySession:{}};
const qtyState = { drink:{}, bottle:{}, food:{} }; // itemId: qty（0スタート）

/* タブ */
document.addEventListener('click', (e)=>{
  const t = e.target.closest('.tab'); if(!t) return;
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');
  document.querySelectorAll('.tabpanes>div').forEach(p=>p.classList.remove('active'));
  $('pane-'+t.dataset.tab)?.classList.add('active');
});

/* API */
async function api(path, opt={}) {
  const headers = {'Content-Type':'application/json','X-Role': role()};
  const o = Object.assign({method:'GET', headers}, opt);
  if (o.body && typeof o.body !== 'string') o.body = JSON.stringify(o.body);
  const res = await fetch(path, o);
  if (!res.ok) { throw new Error(`${res.status} ${await res.text()}`); }
  const ct = res.headers.get('content-type')||'';
  return ct.includes('application/json') ? res.json() : res.text();
}

/* 時刻ユーティリティ */
function liveElapsed(baseMinutes, fetchedAtMs){
  const add = Math.floor((Date.now() - (fetchedAtMs||Date.now()))/60000);
  return Math.max(0, (baseMinutes||0) + add);
}

/* 自動延長（卓ごと） */
const autoExtendBySession = {}; // {sid: boolean}
const autoExtendCooldown = {}; // {sid: ms}
function toggleAutoExtend(){
  if (!currentSessionId) return toast('セッションがありません','err');
  autoExtendBySession[currentSessionId] = !autoExtendBySession[currentSessionId];
  reflectAutoExtendBtn();
}
function reflectAutoExtendBtn(){
  const on = !!autoExtendBySession[currentSessionId];
  const b = $('btnAutoExtend'), badge=$('autoExtendBadge');
  if (!b || !badge) return;
  if (on){ b.textContent='⏹️ 自動延長 ON'; badge.textContent='自動延長: ON'; badge.classList.add('on'); badge.classList.remove('off'); }
  else { b.textContent='⏸️ 自動延長 OFF'; badge.textContent='自動延長: OFF'; badge.classList.add('off'); badge.classList.remove('on'); }
}

/* テーブル色 */
function colorClass(remain, bill){
  if (bill && (bill.due||0) <= 0 && remain >= 0) return 't-paid';
  if (remain < 0) return 't-over';
  if (remain <= 15) return 't-warn';
  return 't-ok';
}

/* フロア描画 */
async function loadFloor(){
  const wrap = $('floor'); if(!wrap) return;
  wrap.innerHTML='';
  const tables = await api(`/tables?store_id=${store()}`);
  floorModel.tables = tables;

  let sessions=[]; try{ sessions = await api(`/sessions?store_id=${store()}&status=open`); }catch{}
  floorModel.billBySession = {};
  await Promise.all((sessions||[]).map(async s=>{
    try{ const b = await api(`/sessions/${s.id}/bill`); b._fetchedAt = Date.now(); floorModel.billBySession[s.id]=b; }catch{}
  }));

  const col=3,gap=12,w=160,h=100,pad=12;
  floorModel.tableEls={}; floorModel.sessionByTable={};

  tables.forEach((t,i)=>{
    const el=document.createElement('div');
    el.className='table t-free';
    el.style.cssText=`left:${pad+(i%col)*(w+gap)}px;top:${pad+Math.floor(i/col)*(h+gap)}px;width:${w}px;height:${h}px`;
    el.dataset.id=t.id; el.id=`table-${t.id}`;

    const s=(sessions||[]).find(x=> (x.table&&x.table.id)? x.table.id===t.id : x.table_id===t.id);
    let center=`<div class="small">空席</div>`;
    if (s){
      floorModel.sessionByTable[t.id]=s.id;
      const b=floorModel.billBySession[s.id];
      const baseElapsed=b?(b.elapsed_minutes ?? b?.time_breakdown?.total_minutes ?? 0):0;
      const elapsed=liveElapsed(baseElapsed, b?._fetchedAt);
      const booked=b?(b.booked_minutes ?? 60):60;
      const remain=booked-elapsed;
      center=`<div class="ttime mono" id="ttime-${t.id}">${remain>=0?`残り ${remain}分`:`超過 ${Math.abs(remain)}分`}</div>
              <div class="small mono" id="tdetail-${t.id}">経過 ${elapsed} / 予約 ${booked}</div>`;
      el.className='table '+colorClass(remain,b);
    }
    el.innerHTML=`<div class="name">${t.name}</div>${center}`;

    el.addEventListener('click', async ()=>{
      selectedTableId=t.id;
      $('selTable').textContent=t.name;
      document.querySelectorAll('.table').forEach(x=>x.classList.remove('sel'));
      el.classList.add('sel');

      const sid=floorModel.sessionByTable[t.id];
      if (sid){
        currentSessionId=sid; $('selSess').textContent=sid; reflectAutoExtendBtn(); await refreshBill();
      }else{
        currentSessionId=null; $('selSess').textContent='-'; reflectAutoExtendBtn(); renderTimer(null); renderBill(null);
      }
    });

    wrap.appendChild(el);
    floorModel.tableEls[t.id]=el;
  });

  if(!selectedTableId && tables.length){
    selectedTableId=tables[0].id; $('selTable').textContent=tables[0].name; $('table-'+selectedTableId)?.classList.add('sel');
  }
}

/* 毎秒更新（卓カード） */
function floorTick(){
  Object.entries(floorModel.sessionByTable).forEach(([tid,sid])=>{
    const el=floorModel.tableEls[tid]; const b=floorModel.billBySession[sid]; if(!el||!b) return;
    const elapsed=liveElapsed(b.elapsed_minutes ?? b?.time_breakdown?.total_minutes ?? 0, b._fetchedAt);
    const booked=b.booked_minutes ?? 60;
    const remain=booked-elapsed;
    const tt=$('ttime-'+tid), td=$('tdetail-'+tid);
    if(tt) tt.textContent= remain>=0?`残り ${remain}分`:`超過 ${Math.abs(remain)}分`;
    if(td) td.textContent= `経過 ${elapsed} / 予約 ${booked}`;
    el.classList.remove('t-free','t-ok','t-warn','t-over','t-paid');
    el.classList.add(colorClass(remain,b));
  });
}

/* アイテム読み込み：数量UI（0スタート） */
async function loadItems(){
  const items = await api(`/items?store_id=${store()}`);
  const byCat={drink:[],bottle:[],food:[]};
  items.forEach(it=>{ if(byCat[it.category]) byCat[it.category].push(it); });

  const render=(cat,wrapId)=>{
    const wrap=$(wrapId); if(!wrap) return;
    wrap.innerHTML='';
    byCat[cat].forEach(it=>{
      if(qtyState[cat][it.id]==null) qtyState[cat][it.id]=0;
      const row=document.createElement('div'); row.className='itemRow';
      row.innerHTML=`
        <div class="itemName">${it.name} <span class="muted">¥${it.price}</span></div>
        <div class="qtyCtrl">
          <button data-act="minus">−</button>
          <div class="val mono" id="q-${cat}-${it.id}">${qtyState[cat][it.id]}</div>
          <button data-act="plus">＋</button>
        </div>`;
      row.querySelector('[data-act="plus"]').addEventListener('click',()=>{
        qtyState[cat][it.id]++; $('q-'+cat+'-'+it.id).textContent=qtyState[cat][it.id];
      });
      row.querySelector('[data-act="minus"]').addEventListener('click',()=>{
        qtyState[cat][it.id]=Math.max(0, qtyState[cat][it.id]-1);
        $('q-'+cat+'-'+it.id).textContent=qtyState[cat][it.id];
      });
      wrap.appendChild(row);
    });
  };
  render('drink','listDrink');
  render('bottle','listBottle');
  render('food','listFood');
}

/* 数量の反映：+はPOST /orders、-は /orders/cancel を試す（なければ警告） */
async function applyCategory(cat){
  if (!currentSessionId) return toast('先に入店してください','err');
  const entries=Object.entries(qtyState[cat]||{});
  if(!entries.length) return;

  // 現在のBillからカテゴリ別の実績数を推定（品名→数量）※簡易：同名合算
  let current = {};
  try{
    const b = await api(`/sessions/${currentSessionId}/bill`);
    (b.orders||[]).forEach(o=>{
      // o.name からカテゴリは取れないので、減算は best-effort（サーバAPIがある前提）
    });
  }catch{}

  // 反映：ここでは「指定数を新規で追加」＋ 減算は cancel API を試行
  for (const [itemIdStr, qty] of entries){
    const itemId = parseInt(itemIdStr,10);
    if (qty>0){
      for (let i=0;i<qty;i++){
        await api(`/sessions/${currentSessionId}/orders`, {method:'POST', body:{store_id:store(), item_id:itemId, qty:1}});
      }
    }else if (qty===0){
    // 0は何もしない
    }
  }

  // 減算UI（0未満にはしない運用なので、実際の取り消しは別APIで個別に対応）
  // もし「取り消し」を実運用する場合は、下の cancelItems を有効に
  toast('反映しました'); await refreshBill(); await loadFloor();
}

/* 入店/延長/取消/支払い/会計 */
async function checkin(){
  if (!selectedTableId){
    const t = await api(`/tables?store_id=${store()}`);
    if (t.length){ selectedTableId=t[0].id; $('selTable').textContent=t[0].name; }
  }
  if (!selectedTableId) throw new Error('テーブルを選択してください');
  const s = await api('/sessions',{method:'POST', body:{store_id:store(), table_id:selectedTableId, guest_count:1, set_minutes:60, extend_unit:30}});
  currentSessionId=s.id; $('selSess').textContent=s.id;
  autoExtendBySession[s.id]=false; reflectAutoExtendBtn();
  toast('入店しました'); await refreshBill(); await loadFloor(); startLoops();
}
async function extend30(){
  if (!currentSessionId) throw new Error('セッションがありません');
  await api(`/sessions/${currentSessionId}/extend`, {method:'POST'});
  toast('+30分 延長しました'); await refreshBill(); await loadFloor();
}
async function unextend30(){
  if (!currentSessionId) throw new Error('セッションがありません');
  // 1) 推奨: /sessions/{id}/unextend にPOST
  try{
    await api(`/sessions/${currentSessionId}/unextend`, {method:'POST'});
    toast('−30分 延長取消しました');
  }catch(e){
    toast('延長取消APIが未実装です','err');
    return;
  }
  await refreshBill(); await loadFloor();
}
async function cancelCheckin(){
  if (!currentSessionId) return;
  try{
    // 1) DELETE /sessions/{id}
    try{ await api(`/sessions/${currentSessionId}`, {method:'DELETE'}); }
    catch{ await api(`/sessions/${currentSessionId}/cancel`, {method:'POST'}); }
    toast('入店を取り消しました');
  }catch(e){
    toast('入店取消APIが未実装です','err');
    return;
  }
  currentSessionId=null; $('selSess').textContent='-'; reflectAutoExtendBtn();
  renderTimer(null); renderBill(null); await loadFloor();
}

async function payCash(amount){
  if (!currentSessionId) throw new Error('セッションがありません');
  await api(`/sessions/${currentSessionId}/payments`,{method:'POST', body:{store_id:store(), method:'cash', amount:Number(amount)}});
  toast('支払いを記録しました'); $('payAmount').value=''; await refreshBill(); await loadFloor();
}
async function checkout(){
  if (!currentSessionId) throw new Error('セッションがありません');
  try{ await api(`/sessions/${currentSessionId}/checkout`, {method:'POST'}); toast('会計を確定しました'); }
  catch(e){ console.warn(e); toast('会計API未実装の可能性（UIは続行）','err'); }
  currentSessionId=null; $('selSess').textContent='-'; reflectAutoExtendBtn();
  renderTimer(null); renderBill(null); await loadFloor();
}

/* 明細＆サイドタイマー */
async function refreshBill(){
  if (!currentSessionId) return;
  const b = await api(`/sessions/${currentSessionId}/bill`);
  b._fetchedAt=Date.now(); currentBill=b;
  renderTimer(b); renderBill(b);
}
function toYen(v){ return `${Math.round(v||0).toLocaleString()} 円`; }
function renderTimer(b){
  const nowStr=new Date().toLocaleTimeString('ja-JP',{hour12:false,hour:'2-digit',minute:'2-digit'});
  if (!b){ $('timerText').textContent='--:--'; $('timerDetail').textContent='経過 - / 予約 - / 残り -'; return; }
  const base=b.elapsed_minutes ?? b?.time_breakdown?.total_minutes ?? 0;
  const elapsed=liveElapsed(base, b._fetchedAt);
  const booked=b.booked_minutes ?? 60;
  const remain=booked-elapsed;
  $('timerText').textContent=nowStr;
  $('timerDetail').textContent=`経過 ${elapsed}分 / 予約 ${booked}分 / ${remain>=0?`残り ${remain}`:`残り -${Math.abs(remain)}`}分`;
}
function renderBill(b){
  const box=$('billBox'); if(!box) return;
  if (!b){ box.innerHTML=''; return; }
  const td=b.time_breakdown||{};
  const row=(l,r)=>`<div class="kv"><span>${l}</span><span class="mono">${r}</span></div>`;
  const lines = [
    `<div class="muted" style="margin-bottom:4px">── 内訳 ──</div>`,
    row('セット/延長', toYen(td.time_amount||0)),
    row('オーダー', toYen(b.order_subtotal||0)),
  ];
  if (b.table_charge > 0) lines.push(row('お通し/TC', toYen(b.table_charge)));
  if (b.vip_fee > 0)      lines.push(row('VIP席料', toYen(b.vip_fee)));
  if (b.night_surcharge > 0) lines.push(row('深夜加算', toYen(b.night_surcharge)));
  lines.push(
    `<hr>`,
    row('小計', toYen(b.subtotal)),
    row('サービス料', toYen(b.service_fee||0)),
    row('消費税', toYen(b.tax)),
    row('<b>合計</b>', `<b>${toYen(b.total)}</b>`),
    `<hr>`,
    row('支払済', toYen(b.paid)),
    row('未収', `<b style="color:#ef4444">${toYen(b.due)}</b>`),
  );
  box.innerHTML = lines.join('');
}

/* 今日の売上（完全自動） */
async function refreshSales(){
  try{
    const d=await api(`/closing?store_id=${store()}`);
    const prefer=['total_sales','today_total','total','sales','amount','sum'];
    let val=0; for(const k of prefer){ if(typeof d?.[k]==='number'){ val=d[k]; break; } }
    if(!val){
      const pick=o=>!o||typeof o!=='object'?0:(Object.values(o).reduce((r,v)=>r||(typeof v==='number'?v:pick(v)),0));
      val=pick(d);
    }
    $('salesToday').textContent=Number(val||0).toLocaleString();
  }catch{}
}

/* 毎秒: 自動延長判定（選択中のみ） */
async function autoExtendTick(){
  const sid=currentSessionId; if(!sid||!autoExtendBySession[sid]||!currentBill) return;
  if ((autoExtendCooldown[sid]||0)>Date.now()) return;
  const base=currentBill.elapsed_minutes ?? currentBill?.time_breakdown?.total_minutes ?? 0;
  const elapsed=liveElapsed(base, currentBill._fetchedAt);
  const booked=currentBill.booked_minutes ?? 60;
  const remain=booked-elapsed;
  if (remain<=0){
    try{ await extend30(); autoExtendCooldown[sid]=Date.now()+20000; }catch{}
  }
}

/* ループ */
function startLoops(){
  ['tick','bill','sales','floor','floorTick'].forEach(k=>{ if(loops[k]) clearInterval(loops[k]); });
  loops.tick=setInterval(()=>{ if(currentBill) renderTimer(currentBill); autoExtendTick(); },1000);
  loops.bill=setInterval(()=>{ if(currentSessionId) refreshBill().catch(()=>{}); },5000);
  loops.sales=setInterval(refreshSales,1000);
  loops.floor=setInterval(()=>loadFloor().catch(()=>{}),5000);
  loops.floorTick=setInterval(floorTick,1000);
}

/* 初期化 */
async function initUI(){
  $('seedBtn')?.addEventListener('click', async ()=>{
    try{ try{ await api(`/admin/seed_demo?store_id=${store()}`,{method:'POST'}); toast('デモデータ作成'); }catch{}; await loadFloor(); await loadItems(); }
    catch(e){ toast(e.message,'err'); }
  });

  // 入店/延長関係
  $('btnCheckin').addEventListener('click', ()=>checkin().catch(e=>toast(e.message,'err')));
  $('btnExtend30').addEventListener('click', ()=>extend30().catch(e=>toast(e.message,'err')));
  $('btnUnextend').addEventListener('click', ()=>unextend30().catch(e=>toast(e.message,'err')));
  $('btnAutoExtend').addEventListener('click', toggleAutoExtend);
  $('btnCancelCheckin').addEventListener('click', ()=>cancelCheckin().catch(e=>toast(e.message,'err')));

  // 支払い
  $('btnPayCash').addEventListener('click', ()=>{
    const v=$('payAmount').value||'1000';
    const amt=parseInt(v.replace(/,/g,''),10)||1000;
    payCash(amt).catch(e=>toast(e.message,'err'));
  });
  $('btnCheckout').addEventListener('click', ()=>checkout().catch(e=>toast(e.message,'err')));

  // 領収書印刷
  $('btnReceipt')?.addEventListener('click', async ()=>{
    const sid = currentSessionId;
    if (!sid) return toast('セッションを選択してください','err');
    try{
      const d = await api(`/sessions/${sid}/receipt`);
      const b = d.bill, st = d.store;
      const now = new Date().toLocaleString('ja-JP');
      const rows = (b.orders||[]).map(o=>
        `<tr><td>${o.name}</td><td style="text-align:right">¥${Math.round(o.amount).toLocaleString()}</td></tr>`
      ).join('');
      const w = window.open('','_blank','width=400,height=600');
      w.document.write(`<!doctype html><html><head><meta charset="utf-8">
        <title>領収書</title>
        <style>body{font-family:sans-serif;font-size:13px;padding:20px;color:#111}
        h2{text-align:center;font-size:18px;border-bottom:2px solid #000;padding-bottom:8px}
        table{width:100%;border-collapse:collapse;margin:10px 0}
        td,th{padding:4px 6px}
        .right{text-align:right}.total{font-size:16px;font-weight:bold;border-top:2px solid #000}
        .muted{color:#666;font-size:11px}@media print{button{display:none}}</style>
        </head><body>
        <h2>領 収 書</h2>
        <div style="text-align:center;margin-bottom:8px">
          <div style="font-size:11px;color:#666">${now}</div>
          <div style="font-size:11px">NO: ${d.invoice_no}</div>
        </div>
        <table>
          <tr><td class="muted">テーブル</td><td>${b.table||''}</td></tr>
          <tr><td class="muted">人数</td><td>${b.guest_count}名</td></tr>
          <tr><td class="muted">入店</td><td>${new Date(b.start_time).toLocaleTimeString('ja-JP',{hour:'2-digit',minute:'2-digit'})}</td></tr>
        </table>
        <table><tr><th style="text-align:left">品目</th><th style="text-align:right">金額</th></tr>
          <tr><td>セット料金</td><td class="right">¥${Math.round(b.time_breakdown?.time_amount||0).toLocaleString()}</td></tr>
          ${b.table_charge>0?`<tr><td>お通し/TC</td><td class="right">¥${Math.round(b.table_charge).toLocaleString()}</td></tr>`:''}
          ${b.vip_fee>0?`<tr><td>VIP席料</td><td class="right">¥${Math.round(b.vip_fee).toLocaleString()}</td></tr>`:''}
          ${rows}
          ${b.night_surcharge>0?`<tr><td>深夜加算</td><td class="right">¥${Math.round(b.night_surcharge).toLocaleString()}</td></tr>`:''}
          <tr><td>小計</td><td class="right">¥${Math.round(b.subtotal).toLocaleString()}</td></tr>
          <tr><td>サービス料</td><td class="right">¥${Math.round(b.service_fee).toLocaleString()}</td></tr>
          <tr><td>消費税</td><td class="right">¥${Math.round(b.tax).toLocaleString()}</td></tr>
          <tr class="total"><td>合 計</td><td class="right">¥${Math.round(b.total).toLocaleString()}</td></tr>
          <tr><td>お支払い済み</td><td class="right">¥${Math.round(b.paid).toLocaleString()}</td></tr>
          <tr><td>お釣り</td><td class="right">¥${Math.max(0,Math.round(b.paid-b.total)).toLocaleString()}</td></tr>
        </table>
        <div style="margin-top:16px;text-align:center;font-size:11px;color:#666">
          <div>${st.legal_name||''}</div>
          <div>${st.address||''}</div>
          <div>TEL: ${st.tel||''}</div>
          ${st.invoice_reg_no?`<div>登録番号: ${st.invoice_reg_no}</div>`:''}
        </div>
        <div style="text-align:center;margin-top:12px">
          <button onclick="window.print()" style="padding:8px 20px;font-size:14px">印刷</button>
        </div>
        </body></html>`);
      w.document.close();
    }catch(e){toast('領収書エラー: '+e.message,'err')}
  });

  // 数量反映
  $('applyDrink').addEventListener('click', ()=>applyCategory('drink').catch(e=>toast(e.message,'err')));
  $('applyBottle').addEventListener('click', ()=>applyCategory('bottle').catch(e=>toast(e.message,'err')));
  $('applyFood').addEventListener('click', ()=>applyCategory('food').catch(e=>toast(e.message,'err')));

  await loadFloor();
  await loadItems();

  try{
    const sess=await api(`/sessions?store_id=${store()}&status=open`);
    if(Array.isArray(sess)&&sess.length){
      currentSessionId=sess[0].id; $('selSess').textContent=currentSessionId; autoExtendBySession[currentSessionId]=false; reflectAutoExtendBtn(); await refreshBill();
    }else{ renderTimer(null); renderBill(null); reflectAutoExtendBtn(); }
  }catch{ renderTimer(null); reflectAutoExtendBtn(); }

  startLoops();
  refreshSales();
}

/* トースト */
function toast(msg,type='ok',t=2200){
  const b=$('toasts'); if(!b) return alert(msg);
  const el=document.createElement('div'); el.className='toast '+(type==='ok'?'ok':'err');
  el.innerHTML=`<div class="title">${type==='ok'?'完了':'エラー'}</div><div>${msg}</div>`;
  b.appendChild(el); setTimeout(()=>{ el.style.opacity=0; setTimeout(()=>el.remove(),280); },t);
}

document.addEventListener('DOMContentLoaded', initUI);
</script>
</body>
</html>
""")