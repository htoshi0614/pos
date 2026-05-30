"""pricing_engine.py — 料金ルールエンジン + /settings/pricing UI"""
from datetime import datetime, timezone
from typing import Optional, List
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey
from sqlalchemy.orm import relationship

from db_shared import Base, SessionLocal, require_role

JST = ZoneInfo("Asia/Tokyo")
router = APIRouter(tags=["pricing"])
ADMIN_ROLES = ["owner", "manager"]

# ─────────────────────────── DB Models ───────────────────────────

class PricingConfig(Base):
    __tablename__ = "pricing_configs"
    id               = Column(Integer, primary_key=True)
    store_id         = Column(Integer, ForeignKey("stores.id"), unique=True)
    service_fee_rate = Column(Float, default=0.10)   # 0.10 = 10%
    tax_rate         = Column(Float, default=0.10)
    rounding         = Column(String, default="round_half")   # round_up / round_down / round_half
    calc_order       = Column(String, default="sc_then_tax")  # sc_then_tax / tax_then_sc
    vip_seat_fee     = Column(Float, default=0.0)
    table_charge_pp  = Column(Float, default=0.0)    # お通し/TC per person
    night22_enabled  = Column(Boolean, default=False)
    night22_type     = Column(String, default="rate") # rate / fixed
    night22_value    = Column(Float, default=0.0)
    night24_enabled  = Column(Boolean, default=False)
    night24_type     = Column(String, default="rate")
    night24_value    = Column(Float, default=0.0)
    set_minutes      = Column(Integer, default=60)     # ワンセット（分）
    extend_unit      = Column(Integer, default=30)     # 延長単位（分）

class TimeSlotRule(Base):
    __tablename__ = "time_slot_rules"
    id           = Column(Integer, primary_key=True)
    store_id     = Column(Integer, ForeignKey("stores.id"))
    label        = Column(String, default="")
    start_hour   = Column(Integer, default=19)
    end_hour     = Column(Integer, nullable=True)     # None = LAST
    set_price    = Column(Float, default=6000.0)
    extend_price = Column(Float, default=3000.0)
    days         = Column(String, default="all")      # "all" or "0,1,2,3,4,5,6"
    holiday_eve  = Column(Boolean, default=False)
    is_active    = Column(Boolean, default=True)

class DiscountRule(Base):
    __tablename__ = "discount_rules"
    id         = Column(Integer, primary_key=True)
    store_id   = Column(Integer, ForeignKey("stores.id"))
    label      = Column(String, default="")               # 例: "初回割引", "レディースデー"
    disc_type  = Column(String, default="fixed")           # fixed / rate / set_override / free_drink
    value      = Column(Float, default=0.0)                # fixed→円, rate→0.10=10%, set_override→セット料金, free_drink→杯数
    is_active  = Column(Boolean, default=True)

class SetPlanOption(Base):
    """入店時コース選択肢（例: 40分¥3,000 / 60分¥4,000）"""
    __tablename__ = "set_plan_options"
    id         = Column(Integer, primary_key=True)
    store_id   = Column(Integer, ForeignKey("stores.id"))
    label      = Column(String, default="")   # 例: "40分コース"
    minutes    = Column(Integer, default=60)  # セット時間（分）
    price      = Column(Float, default=3000.0)  # 1人あたり料金（円）
    sort_order = Column(Integer, default=0)
    is_active  = Column(Boolean, default=True)

class ExtendOption(Base):
    """延長オプション選択肢（例: +20分¥2,000 / +40分¥3,000 / +60分¥4,000）"""
    __tablename__ = "extend_options"
    id         = Column(Integer, primary_key=True)
    store_id   = Column(Integer, ForeignKey("stores.id"))
    label      = Column(String, default="")   # 例: "+40分"
    minutes    = Column(Integer, default=30)  # 延長時間（分）
    price      = Column(Float, default=3000.0)  # 1人あたり料金（円）
    sort_order = Column(Integer, default=0)
    is_active  = Column(Boolean, default=True)

# ─────────────────────────── Pydantic ───────────────────────────

class PricingConfigIn(BaseModel):
    service_fee_rate: float = 0.10
    tax_rate: float = 0.10
    rounding: str = "round_half"
    calc_order: str = "sc_then_tax"
    vip_seat_fee: float = 0.0
    table_charge_pp: float = 0.0
    night22_enabled: bool = False
    night22_type: str = "rate"
    night22_value: float = 0.0
    night24_enabled: bool = False
    night24_type: str = "rate"
    night24_value: float = 0.0
    set_minutes: int = 60
    extend_unit: int = 30

class TimeSlotRuleIn(BaseModel):
    label: str = ""
    start_hour: int
    end_hour: Optional[int] = None
    set_price: float
    extend_price: float
    days: str = "all"
    holiday_eve: bool = False
    is_active: bool = True

class DiscountRuleIn(BaseModel):
    label: str = ""
    disc_type: str = "fixed"       # fixed / rate / set_override / free_drink
    value: float = 0.0
    is_active: bool = True

class SetPlanOptionIn(BaseModel):
    label: str = ""
    minutes: int = 60
    price: float = 3000.0
    sort_order: int = 0
    is_active: bool = True

class ExtendOptionIn(BaseModel):
    label: str = ""
    minutes: int = 30
    price: float = 3000.0
    sort_order: int = 0
    is_active: bool = True

# ─────────────────────────── Pricing Logic ───────────────────────────

def _apply_round(val: float, mode: str) -> int:
    import math
    if mode == "round_up":   return math.ceil(val)
    if mode == "round_down": return math.floor(val)
    return int(round(val))

def get_pricing_config(db, store_id: int) -> PricingConfig:
    return db.query(PricingConfig).filter_by(store_id=store_id).first()

def get_slot_rule(db, store_id: int, start_dt: datetime) -> Optional[TimeSlotRule]:
    """入店時刻に対応するTimeSlotRuleを返す"""
    start_jst = start_dt.replace(tzinfo=timezone.utc).astimezone(JST)
    hour    = start_jst.hour
    weekday = start_jst.weekday()  # Mon=0, Sun=6

    rules = (db.query(TimeSlotRule)
             .filter_by(store_id=store_id, is_active=True)
             .order_by(TimeSlotRule.start_hour.desc())
             .all())

    for rule in rules:
        # 曜日チェック
        if rule.days != "all":
            valid = [int(d) for d in rule.days.split(",") if d.strip()]
            if weekday not in valid:
                continue
        # 時間帯チェック
        if hour >= rule.start_hour:
            if rule.end_hour is None or hour < rule.end_hour:
                return rule
    return None

def compute_night_surcharge(config: Optional[PricingConfig], subtotal: float,
                            start_dt: datetime,
                            end_dt: Optional[datetime] = None) -> float:
    """深夜加算を計算して返す（22:00跨ぎ / 24:00跨ぎ）

    入店時刻だけでなく「在席していた時間帯」で判定する。
    例: 21:00入店 → 23:00退店 でも night22 が適用される。
    """
    if not config:
        return 0.0
    start_jst = start_dt.replace(tzinfo=timezone.utc).astimezone(JST)
    if end_dt is None:
        end_dt = datetime.utcnow()
    end_jst = end_dt.replace(tzinfo=timezone.utc).astimezone(JST)
    surcharge = 0.0

    # ── 22:00サーチャージ ──────────────────────────────────────
    # セッションが 22:00〜翌0:00 に重なっていれば適用
    # 条件: 22時以降に入店 OR 22時以降まで滞在 OR 日をまたいだ（22:00-24:00を通過）
    if config.night22_enabled:
        night22_active = (
            start_jst.hour >= 22                          # 22時以降に入店
            or end_jst.hour >= 22                         # 22時以降まで滞在（同日）
            or end_jst.date() > start_jst.date()          # 日またぎ（22:00-24:00を通過）
        )
        if night22_active:
            if config.night22_type == "rate":
                surcharge += subtotal * config.night22_value
            else:
                surcharge += config.night22_value

    # ── 深夜サーチャージ（0:00〜4:00）────────────────────────
    # セッションが翌0:00以降も続いていれば適用
    # 条件: 日をまたいだ（0時を越えた）OR 早朝スタート（0〜3時台入店）
    if config.night24_enabled:
        night24_active = (
            end_jst.date() > start_jst.date()             # 日またぎ（0時を越えた）
            or start_jst.hour < 4                         # 深夜0〜3時台の入店
        )
        if night24_active:
            if config.night24_type == "rate":
                surcharge += subtotal * config.night24_value
            else:
                surcharge += config.night24_value

    return surcharge

def compute_totals(subtotal: float, night_add: float,
                   config: Optional[PricingConfig]) -> dict:
    """SC・税・合計を計算。config に従って順序・端数処理を適用"""
    sc_rate   = config.service_fee_rate if config else 0.10
    tax_rate  = config.tax_rate         if config else 0.10
    rounding  = config.rounding         if config else "round_half"
    order     = config.calc_order       if config else "sc_then_tax"

    base = subtotal + night_add

    if order == "sc_then_tax":
        sc  = _apply_round(base * sc_rate, rounding)
        tax = _apply_round((base + sc) * tax_rate, rounding)
    else:  # tax_then_sc
        tax = _apply_round(base * tax_rate, rounding)
        sc  = _apply_round((base + tax) * sc_rate, rounding)

    total = _apply_round(base + sc + tax, rounding)
    return {"service_fee": sc, "tax": tax, "total": total}

# ─────────────────────────── API Routes ───────────────────────────

@router.get("/settings/store-time/{store_id}")
def get_store_time(store_id: int):
    """セット時間・延長単位・コース選択肢・延長オプションを返す（全ロール利用可）"""
    db = SessionLocal()
    try:
        cfg = db.query(PricingConfig).filter_by(store_id=store_id).first()
        plans = db.query(SetPlanOption).filter_by(store_id=store_id, is_active=True).order_by(SetPlanOption.sort_order, SetPlanOption.id).all()
        extend_opts = db.query(ExtendOption).filter_by(store_id=store_id, is_active=True).order_by(ExtendOption.sort_order, ExtendOption.id).all()
        return {
            "set_minutes": cfg.set_minutes if cfg else 60,
            "extend_unit": cfg.extend_unit if cfg else 30,
            "set_plans": [{"id": p.id, "label": p.label, "minutes": p.minutes, "price": p.price, "is_active": p.is_active} for p in plans],
            "extend_options": [{"id": o.id, "label": o.label, "minutes": o.minutes, "price": o.price, "is_active": o.is_active} for o in extend_opts],
        }
    finally:
        db.close()

@router.get("/settings/pricing/{store_id}")
def get_pricing(store_id: int,
                x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        cfg   = db.query(PricingConfig).filter_by(store_id=store_id).first()
        slots = db.query(TimeSlotRule).filter_by(store_id=store_id).order_by(TimeSlotRule.start_hour).all()
        discs = db.query(DiscountRule).filter_by(store_id=store_id).order_by(DiscountRule.id).all()
        return {
            "config": {k: v for k, v in cfg.__dict__.items() if not k.startswith("_")} if cfg else None,
            "slots":  [{k: v for k, v in s.__dict__.items() if not k.startswith("_")} for s in slots],
            "discounts": [{"id": d.id, "label": d.label, "disc_type": d.disc_type,
                           "value": d.value, "is_active": d.is_active} for d in discs],
        }
    finally:
        db.close()

@router.post("/settings/pricing/{store_id}/config")
def save_pricing_config(store_id: int, payload: PricingConfigIn,
                        x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        cfg = db.query(PricingConfig).filter_by(store_id=store_id).first()
        if cfg:
            for k, v in (payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()).items():
                setattr(cfg, k, v)
        else:
            cfg = PricingConfig(store_id=store_id, **(payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()))
            db.add(cfg)
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@router.post("/settings/pricing/{store_id}/slots")
def add_slot(store_id: int, payload: TimeSlotRuleIn,
             x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        rule = TimeSlotRule(store_id=store_id, **(payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()))
        db.add(rule); db.commit(); db.refresh(rule)
        return {"ok": True, "id": rule.id}
    finally:
        db.close()

@router.put("/settings/pricing/{store_id}/slots/{rule_id}")
def update_slot(store_id: int, rule_id: int, payload: TimeSlotRuleIn,
                x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        rule = db.query(TimeSlotRule).filter_by(id=rule_id, store_id=store_id).first()
        if not rule:
            raise HTTPException(404, "Rule not found")
        for k, v in (payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()).items():
            setattr(rule, k, v)
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@router.delete("/settings/pricing/{store_id}/slots/{rule_id}")
def delete_slot(store_id: int, rule_id: int,
                x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        rule = db.query(TimeSlotRule).filter_by(id=rule_id, store_id=store_id).first()
        if rule:
            db.delete(rule); db.commit()
        return {"ok": True}
    finally:
        db.close()

# ─── 割引ルール API ───

@router.get("/settings/pricing/{store_id}/discounts")
def list_discounts(store_id: int,
                   x_role: Optional[str] = Header(None, alias="X-Role")):
    """割引ルール一覧（全ロール — POS側で選択肢として使う）"""
    db = SessionLocal()
    try:
        rules = db.query(DiscountRule).filter_by(store_id=store_id).order_by(DiscountRule.id).all()
        return [{"id": r.id, "label": r.label, "disc_type": r.disc_type,
                 "value": r.value, "is_active": r.is_active} for r in rules]
    finally:
        db.close()

@router.post("/settings/pricing/{store_id}/discounts")
def add_discount(store_id: int, payload: DiscountRuleIn,
                 x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        r = DiscountRule(store_id=store_id, **(payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()))
        db.add(r); db.commit(); db.refresh(r)
        return {"ok": True, "id": r.id}
    finally:
        db.close()

@router.delete("/settings/pricing/{store_id}/discounts/{disc_id}")
def delete_discount(store_id: int, disc_id: int,
                    x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        r = db.query(DiscountRule).filter_by(id=disc_id, store_id=store_id).first()
        if r:
            db.delete(r); db.commit()
        return {"ok": True}
    finally:
        db.close()

# ─── コース選択肢 API ───

@router.get("/settings/pricing/{store_id}/set-plans")
def list_set_plans(store_id: int, x_role: Optional[str] = Header(None, alias="X-Role")):
    """コース選択肢一覧（全ロール）"""
    db = SessionLocal()
    try:
        plans = db.query(SetPlanOption).filter_by(store_id=store_id).order_by(SetPlanOption.sort_order, SetPlanOption.id).all()
        return [{"id": p.id, "label": p.label, "minutes": p.minutes, "price": p.price, "sort_order": p.sort_order, "is_active": p.is_active} for p in plans]
    finally:
        db.close()

@router.post("/settings/pricing/{store_id}/set-plans")
def add_set_plan(store_id: int, payload: SetPlanOptionIn,
                 x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        p = SetPlanOption(store_id=store_id, **(payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()))
        db.add(p); db.commit(); db.refresh(p)
        return {"ok": True, "id": p.id}
    finally:
        db.close()

@router.delete("/settings/pricing/{store_id}/set-plans/{plan_id}")
def delete_set_plan(store_id: int, plan_id: int,
                    x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        p = db.query(SetPlanOption).filter_by(id=plan_id, store_id=store_id).first()
        if p:
            db.delete(p); db.commit()
        return {"ok": True}
    finally:
        db.close()

# ─── 延長オプション API ───

@router.get("/settings/pricing/{store_id}/extend-options")
def list_extend_options(store_id: int, x_role: Optional[str] = Header(None, alias="X-Role")):
    """延長オプション一覧（全ロール）"""
    db = SessionLocal()
    try:
        opts = db.query(ExtendOption).filter_by(store_id=store_id).order_by(ExtendOption.sort_order, ExtendOption.id).all()
        return [{"id": o.id, "label": o.label, "minutes": o.minutes, "price": o.price, "sort_order": o.sort_order, "is_active": o.is_active} for o in opts]
    finally:
        db.close()

@router.post("/settings/pricing/{store_id}/extend-options")
def add_extend_option(store_id: int, payload: ExtendOptionIn,
                      x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        o = ExtendOption(store_id=store_id, **(payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()))
        db.add(o); db.commit(); db.refresh(o)
        return {"ok": True, "id": o.id}
    finally:
        db.close()

@router.delete("/settings/pricing/{store_id}/extend-options/{opt_id}")
def delete_extend_option(store_id: int, opt_id: int,
                         x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        o = db.query(ExtendOption).filter_by(id=opt_id, store_id=store_id).first()
        if o:
            db.delete(o); db.commit()
        return {"ok": True}
    finally:
        db.close()

# ─────────────────────────── Pricing Settings UI ───────────────────────────

@router.get("/ui/pricing", response_class=HTMLResponse)
def ui_pricing():
    return HTMLResponse(r"""
<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>料金設定 - Girls Bar POS</title>
<style>
:root{--bg:#fafafa;--card:#ffffff;--card2:#ffffff;--line:#eaeaef;--border:#eaeaef;--text:#0a0a0f;--ink:#0a0a0f;--muted:#8a8a95;--body:#4a4a55;--accent:#d64583;--accent-soft:#fdf0f7;--accent-dark:#b03468;--gold:#c9a96e;--gold-soft:#faf3e3;--warn:#f59e0b;--amber:#f59e0b;--err:#ef4444;--red:#ef4444;--ok:#22c55e;--green:#22c55e;--blue:#3b82f6;--purple:#a855f7;}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text)}
header{position:sticky;top:0;z-index:40;display:flex;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);background:rgba(11,18,32,.95);backdrop-filter:blur(6px)}
header h1{margin:0;font-size:17px}
.nav a{color:var(--accent);text-decoration:none;font-size:14px;padding:6px 10px;border-radius:8px;border:1px solid var(--line)}
.nav a:hover{background:var(--card)}
.container{max-width:900px;margin:0 auto;padding:20px 16px;display:flex;flex-direction:column;gap:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px}
.card h2{margin:0 0 6px;font-size:16px;display:flex;align-items:center;gap:8px}
.card .section-desc{font-size:12px;color:var(--muted);margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid var(--line);line-height:1.6}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}
label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--text);font-weight:500}
.label-hint{font-size:11px;color:var(--muted);font-weight:400;margin-top:2px}
input,select{font-size:14px;padding:10px 12px;border-radius:10px;border:1px solid #263244;background:#0a1220;color:var(--text);transition:border-color .2s}
input:focus,select:focus{outline:none;border-color:var(--accent)}
.input-wrap{position:relative;display:flex;align-items:center}
.input-wrap input{flex:1;padding-right:36px}
.input-unit{position:absolute;right:12px;font-size:13px;color:var(--muted);pointer-events:none}
.btn{cursor:pointer;font-size:14px;padding:10px 20px;border-radius:10px;border:1px solid #334155;background:#111827;color:var(--text);transition:background .2s}
.btn:hover{background:#1e293b}
.btn.solid{background:var(--accent);border-color:var(--accent);color:#001018;font-weight:700}
.btn.solid:hover{background:#38bdf8}
.btn.danger{background:#7f1d1d;border-color:var(--red);color:#fff;font-size:12px;padding:6px 12px}
.btn.danger:hover{background:#991b1b}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.night-block{background:#0a1220;border:1px solid var(--line);border-radius:12px;padding:14px}
.night-block label.toggle{flex-direction:row;align-items:center;gap:8px;font-size:14px;margin-bottom:10px;cursor:pointer}
.night-block .fields{display:flex;gap:10px;align-items:center;padding-left:26px}
#toast{position:fixed;right:16px;bottom:16px;border-radius:12px;padding:12px 20px;display:none;font-size:14px;font-weight:500;z-index:100;box-shadow:0 4px 12px rgba(0,0,0,.4)}
#toast.ok{background:#0e2d14;border:1px solid var(--green);color:var(--green)}
#toast.err{background:#2d0e0e;border:1px solid var(--red);color:var(--red)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:10px 10px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--muted);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.empty-msg{text-align:center;color:var(--muted);padding:20px;font-size:13px}
.add-section{margin-top:16px;background:#0a1220;border:1px solid var(--line);border-radius:12px;padding:16px}
.add-section .add-title{font-size:13px;font-weight:600;margin-bottom:12px;display:flex;align-items:center;gap:6px}
.slot-form{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.disc-form{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
@media(max-width:700px){
  .grid2{grid-template-columns:1fr}
  .grid3{grid-template-columns:1fr}
  .slot-form{grid-template-columns:1fr 1fr}
  .disc-form{grid-template-columns:1fr}
  .container{padding:12px 10px}
  table{font-size:11px;display:block;overflow-x:auto}
  th,td{padding:6px 4px;white-space:nowrap}
  .row{gap:6px}
  header{flex-wrap:wrap;gap:8px}
}

/* === Premium Pink Theme Override (auto-injected) === */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=Inter:wght@400;500;600;700;800&display=swap');
body{font-family:'Inter','Noto Sans JP',-apple-system,system-ui,Segoe UI,Roboto,sans-serif !important;background:#fafafa !important;color:#0a0a0f !important;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4{color:#0a0a0f}
h1{font-weight:800;letter-spacing:-.01em}
a{color:#d64583}
.card,.box,section,article{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.stat,.kpi,.tile,.metric{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f}
.stat .val,.kpi .val,.metric .val,.tile .val{color:#0a0a0f !important}
.stat .label,.kpi .label,.tile .label{color:#8a8a95 !important}
table{border-color:#eaeaef !important}
th{background:#fafafa !important;color:#4a4a55 !important;font-weight:700 !important;border-color:#eaeaef !important;letter-spacing:.02em}
td{border-color:#f3f3f6 !important;color:#0a0a0f !important;background:#ffffff}
tr:nth-child(even) td{background:#fafafa}
.badge.open{background:#fafafa !important;color:#8a8a95 !important;border:1px solid #eaeaef !important}
.badge.preliminary{background:#fff7ed !important;color:#c2410c !important;border-color:#fed7aa !important}
.badge.final{background:#f0fdf4 !important;color:#15803d !important;border-color:#86efac !important}
.btn{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important;transition:all .2s;font-weight:600 !important}
.btn:hover{border-color:#d64583 !important;color:#d64583 !important;background:#ffffff !important;transform:translateY(-1px);box-shadow:0 2px 8px rgba(214,69,131,.08)}
.btn.primary,.btn.solid{background:#d64583 !important;border-color:#d64583 !important;color:#ffffff !important}
.btn.primary:hover,.btn.solid:hover{background:#b03468 !important;border-color:#b03468 !important;color:#ffffff !important;opacity:1 !important}
.btn.warn{background:#f59e0b !important;border-color:#f59e0b !important;color:#ffffff !important}
.btn.danger,.btn.err{background:#ef4444 !important;border-color:#ef4444 !important;color:#ffffff !important}
input,select,textarea{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important;font-family:inherit}
input:focus,select:focus,textarea:focus{border-color:#d64583 !important;box-shadow:0 0 0 3px #fdf0f7 !important;outline:none}
.tab{color:#8a8a95 !important;background:transparent !important}
.tab.active{background:#fdf0f7 !important;color:#d64583 !important;border-color:#eaeaef !important;border-bottom-color:#fdf0f7 !important;font-weight:700}
.tab-body{background:#ffffff !important;border-color:#eaeaef !important}
.toast.ok{background:#f0fdf4 !important;color:#14532d !important;border:1px solid #86efac}
.toast.err{background:#fef2f2 !important;color:#7f1d1d !important;border:1px solid #fca5a5}
.history-row{border-color:#f3f3f6 !important}
header{background:rgba(255,255,255,.92) !important;backdrop-filter:blur(20px);border-color:#eaeaef !important;color:#0a0a0f !important}
header h1{background:linear-gradient(135deg,#0a0a0f,#d64583);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
nav a,.nav a{color:#4a4a55}
nav a:hover,.nav a:hover{color:#d64583}
/* よくあるダーク背景の hex 値を強制ライト化 */
[style*="background:#0f172a"],[style*="background:#0b1220"],[style*="background:#1e293b"],[style*="background:#0a1423"],[style*="background:#111827"],[style*="background:#1a1d29"]{background:#ffffff !important;color:#0a0a0f !important;border-color:#eaeaef}
[style*="color:#e5e7eb"],[style*="color:#e2e8f0"]{color:#0a0a0f !important}
[style*="color:#94a3b8"],[style*="color:#9ca3af"]{color:#8a8a95 !important}
[style*="border:1px solid #1f2937"],[style*="border:1px solid #334155"],[style*="border:1px solid #263244"]{border-color:#eaeaef !important}


/* === Premium Pink v2 (page-specific) === */
.preset-btn{background:#ffffff !important;border:2px solid #eaeaef !important;color:#0a0a0f !important}
.preset-btn:hover,.preset-btn.active{border-color:#d64583 !important;background:#fdf0f7 !important;color:#b03468 !important}
.preset-btn .icon{color:#d64583 !important}
.btn.green,.btn.success{background:#f0fdf4 !important;border-color:#86efac !important;color:#15803d !important}
.btn.danger,.btn.err{background:#fef2f2 !important;border-color:#fca5a5 !important;color:#b91c1c !important}
.btn.solid,.btn.primary{color:#ffffff !important}
.step-num{color:#ffffff !important;background:#d64583 !important}
.kpi{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important}
.kpi .label{color:#8a8a95 !important}
.kpi .value{color:#0a0a0f !important}
.kpi .sub{color:#8a8a95 !important}
.badge.sent,.badge.success,.badge.ok,.badge.active{background:#f0fdf4 !important;color:#15803d !important;border:1px solid #86efac !important}
.badge.failed,.badge.error,.badge.ng,.badge.inactive{background:#fef2f2 !important;color:#b91c1c !important;border:1px solid #fca5a5 !important}
.status-bar{border-radius:10px}
.status-bar.ok{background:#f0fdf4 !important;border:1px solid #86efac !important;color:#15803d !important}
.status-bar.ng,.status-bar.err,.status-bar.warning{background:#fef2f2 !important;border:1px solid #fca5a5 !important;color:#b91c1c !important}
.recipient-item,.list-item,.row-item{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.recipient-item .name{color:#8a8a95 !important}
.note,.help,.hint{color:#4a4a55 !important}
.bar-wrap .track,.progress-track,.goal-track{background:#f3f3f6 !important}
.section-tab{color:#8a8a95 !important}
.section-tab.active{color:#d64583 !important;font-weight:700}
input,select,textarea{background:#ffffff !important;color:#0a0a0f !important;border:1px solid #eaeaef !important}
input:focus,select:focus,textarea:focus{border-color:#d64583 !important;box-shadow:0 0 0 3px #fdf0f7 !important}
.tab,.tab-btn{color:#8a8a95 !important}
.tab.active,.tab-btn.active{color:#d64583 !important;background:#fdf0f7 !important;border-color:#d64583 !important}
.tab-body{background:#ffffff !important;border-color:#eaeaef !important}
td.rank-2{color:#9aa0ac !important}
td.rank-3{color:#b08d57 !important}
/* 残ダーク背景の inline / クラスを一掃 */
[style*="#0a1423"],[style*="#0a1624"],[style*="#0a1220"],[style*="#0c1a2e"],[style*="#0c2a3d"],[style*="#1a2438"],[style*="#1c1c2e"],[style*="#0c1d2e"],[style*="#0b1220"],[style*="#0f172a"],[style*="#111827"]{background:#ffffff !important;color:#0a0a0f !important;border-color:#eaeaef !important}
[style*="#0ea5e9"]{color:#d64583 !important}

</style></head><body>
<header>
  <h1>料金設定</h1>
  <div class="nav" style="display:flex;gap:8px;margin-left:auto">
    <a href="/ui">← フロア</a>
    <a href="/ui/salary">給与管理</a>
    <a href="/ui/weather">天気/シフト</a>
    <a href="/ui/subscription">サブスク</a>
  </div>
</header>

<input type="hidden" id="storeId">

<div class="container">

  <!-- ① 基本設定 -->
  <div class="card">
    <h2>⚙️ 基本料金設定</h2>
    <p class="section-desc">お会計時の税金・サービス料・テーブルチャージなどの基本ルールを設定します。</p>

    <div class="grid2">
      <label>サービス料（SC）
        <div class="input-wrap">
          <input id="sc_rate" type="number" step="1" min="0" max="100" value="10">
          <span class="input-unit">%</span>
        </div>
        <span class="label-hint">お会計の小計に対して加算されます</span>
      </label>
      <label>消費税
        <div class="input-wrap">
          <input id="tax_rate" type="number" step="1" min="0" max="100" value="10">
          <span class="input-unit">%</span>
        </div>
        <span class="label-hint">通常は10%（軽減税率は8%）</span>
      </label>
    </div>

    <div class="grid2" style="margin-top:16px">
      <label>端数の処理方法
        <select id="rounding">
          <option value="round_half">四捨五入（一般的）</option>
          <option value="round_up">切り上げ</option>
          <option value="round_down">切り捨て</option>
        </select>
        <span class="label-hint">税金やSCの計算結果の端数をどう処理するか</span>
      </label>
      <label>SC・税の計算順序
        <select id="calc_order">
          <option value="sc_then_tax">小計にSCを足してから税を計算</option>
          <option value="tax_then_sc">小計に税を足してからSCを計算</option>
        </select>
        <span class="label-hint">お店の会計方針に合わせてください</span>
      </label>
    </div>

    <div class="grid3" style="margin-top:16px">
      <label>VIP席料
        <div class="input-wrap">
          <input id="vip_fee" type="number" value="0" min="0" step="100">
          <span class="input-unit">円</span>
        </div>
        <span class="label-hint">VIPルーム利用時に加算</span>
      </label>
      <label>お通し / テーブルチャージ
        <div class="input-wrap">
          <input id="table_charge" type="number" value="0" min="0" step="100">
          <span class="input-unit">円/人</span>
        </div>
        <span class="label-hint">お客様1人あたりの料金</span>
      </label>
      <div></div>
    </div>

    <div class="grid2" style="margin-top:16px">
      <label>ワンセットの時間
        <div class="input-wrap">
          <input id="set_minutes" type="number" value="60" min="10" max="180" step="5">
          <span class="input-unit">分</span>
        </div>
        <span class="label-hint">入店からワンセット終了までの時間</span>
      </label>
      <label>延長の単位
        <div class="input-wrap">
          <input id="extend_unit" type="number" value="30" min="5" max="120" step="5">
          <span class="input-unit">分</span>
        </div>
        <span class="label-hint">「延長+○分」のボタンで加算される時間</span>
      </label>
    </div>

    <!-- 深夜加算 -->
    <div style="margin-top:20px;border-top:1px solid var(--line);padding-top:16px">
      <div style="font-size:14px;font-weight:600;margin-bottom:12px">🌙 深夜加算</div>
      <p class="section-desc" style="border:none;padding:0;margin:0 0 12px">22時や24時を跨いだ場合に自動で料金を上乗せできます。</p>
      <div class="grid2">
        <div class="night-block">
          <label class="toggle">
            <input type="checkbox" id="night22_en"> 22時以降の加算
          </label>
          <div class="fields">
            <select id="night22_type" onchange="updateNightHint(22)" style="width:140px">
              <option value="rate">料金の○%を加算</option>
              <option value="fixed">固定額を加算</option>
            </select>
            <div class="input-wrap" style="flex:1">
              <input id="night22_val" type="number" step="1" value="0">
              <span class="input-unit" id="night22_unit">%</span>
            </div>
          </div>
        </div>
        <div class="night-block">
          <label class="toggle">
            <input type="checkbox" id="night24_en"> 24時（深夜0時）以降の加算
          </label>
          <div class="fields">
            <select id="night24_type" onchange="updateNightHint(24)" style="width:140px">
              <option value="rate">料金の○%を加算</option>
              <option value="fixed">固定額を加算</option>
            </select>
            <div class="input-wrap" style="flex:1">
              <input id="night24_val" type="number" step="1" value="0">
              <span class="input-unit" id="night24_unit">%</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="row" style="margin-top:20px;justify-content:flex-end">
      <button class="btn solid" onclick="saveConfig()" style="padding:12px 32px;font-size:15px">💾 設定を保存</button>
    </div>
  </div>

  <!-- ② 時間帯料金ルール -->
  <div class="card">
    <h2>🕐 時間帯ごとの料金</h2>
    <p class="section-desc">時間帯や曜日によってセット料金・延長料金を変えたい場合に設定します。<br>例：19〜21時はセット5,000円、21時以降はセット7,000円など。</p>

    <table id="slotTable">
      <thead><tr><th>名前</th><th>時間帯</th><th>セット料金</th><th>延長料金</th><th>曜日</th><th>祝前日</th><th>有効</th><th></th></tr></thead>
      <tbody id="slotBody"></tbody>
    </table>
    <div id="slotEmpty" class="empty-msg" style="display:none">まだ時間帯ルールがありません。下のフォームから追加できます。</div>

    <div class="add-section">
      <div class="add-title">＋ 新しい時間帯ルールを追加</div>
      <div class="slot-form">
        <label>ルール名
          <input id="n_label" placeholder="例：通常時間帯">
        </label>
        <label>開始時刻
          <select id="n_start">
            <option value="17">17:00</option><option value="18">18:00</option>
            <option value="19" selected>19:00</option><option value="20">20:00</option>
            <option value="21">21:00</option><option value="22">22:00</option>
            <option value="23">23:00</option><option value="0">0:00</option>
          </select>
        </label>
        <label>終了時刻
          <select id="n_end">
            <option value="">ラストまで</option>
            <option value="20">20:00</option><option value="21">21:00</option>
            <option value="22">22:00</option><option value="23">23:00</option>
            <option value="0">0:00</option><option value="1">1:00</option>
          </select>
        </label>
        <label>セット料金
          <div class="input-wrap"><input id="n_set" type="number" value="6000" min="0" step="500"><span class="input-unit">円</span></div>
        </label>
        <label>延長料金
          <div class="input-wrap"><input id="n_ext" type="number" value="3000" min="0" step="500"><span class="input-unit">円</span></div>
        </label>
        <label>対象曜日
          <select id="n_days">
            <option value="all">毎日（全曜日）</option>
            <option value="0,1,2,3">平日（月〜木）</option>
            <option value="4,5,6">週末（金土日）</option>
            <option value="5,6">土日のみ</option>
            <option value="4">金曜のみ</option>
            <option value="5">土曜のみ</option>
            <option value="6">日曜のみ</option>
          </select>
        </label>
      </div>
      <div class="row" style="margin-top:12px;justify-content:space-between">
        <label style="flex-direction:row;align-items:center;gap:6px;cursor:pointer">
          <input type="checkbox" id="n_heve"> 祝前日にも適用
        </label>
        <button class="btn solid" onclick="addSlot()">追加する</button>
      </div>
    </div>
  </div>

  <!-- ③ 割引ルール -->
  <div class="card">
    <h2>🏷️ 割引ルール</h2>
    <p class="section-desc">POSの画面で使える割引を登録できます。<br>スタッフが「割引」ボタンを押したときに、ここで登録したルールが選択肢として表示されます。</p>

    <table id="discTable">
      <thead><tr><th>割引名</th><th>種類</th><th>内容</th><th>有効</th><th></th></tr></thead>
      <tbody id="discBody"></tbody>
    </table>
    <div id="discEmpty" class="empty-msg" style="display:none">まだ割引ルールがありません。下のフォームから追加できます。</div>

    <div class="add-section">
      <div class="add-title">＋ 新しい割引ルールを追加</div>
      <div class="disc-form">
        <label>割引の名前
          <input id="d_label" placeholder="例：初回割引、レディースデー">
        </label>
        <label>割引の種類
          <select id="d_type" onchange="updateDiscHint()">
            <option value="fixed">金額を値引き</option>
            <option value="rate">○%OFF</option>
            <option value="set_override">セット料金を変更</option>
            <option value="free_drink">ドリンク○杯サービス</option>
          </select>
        </label>
        <label id="d_value_label">値引き額
          <div class="input-wrap">
            <input id="d_value" type="number" step="1" min="0" value="" placeholder="例：1000">
            <span class="input-unit" id="d_unit">円</span>
          </div>
          <span class="label-hint" id="d_hint">お会計から差し引く金額</span>
        </label>
      </div>
      <div class="row" style="margin-top:12px;justify-content:flex-end">
        <button class="btn solid" onclick="addDiscount()">追加する</button>
      </div>
    </div>
  </div>

  <!-- ④ コース選択肢 -->
  <div class="card">
    <h2>🎯 コース選択肢（入店時）</h2>
    <p class="section-desc">入店時にスタッフがコースを選べるようにします。<br>例: 「40分コース ¥3,000」「60分コース ¥4,000」など複数設定すると入店ボタンを押したときにポップアップで選択できます。<br>何も登録しない場合は従来通りデフォルトのセット時間・料金が使われます。</p>

    <table id="planTable" style="display:none">
      <thead><tr><th>コース名</th><th>時間</th><th>料金/人</th><th>有効</th><th></th></tr></thead>
      <tbody id="planBody"></tbody>
    </table>
    <div id="planEmpty" class="empty-msg" style="display:none">まだコースが登録されていません。下のフォームから追加できます。</div>

    <div class="add-section">
      <div class="add-title">＋ コースを追加</div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px">
        <label>コース名<input id="pl_label" placeholder="例：40分コース"></label>
        <label>時間（分）
          <div class="input-wrap"><input id="pl_minutes" type="number" value="60" min="10" step="5"><span class="input-unit">分</span></div>
        </label>
        <label>料金/人
          <div class="input-wrap"><input id="pl_price" type="number" value="3000" min="0" step="500"><span class="input-unit">円</span></div>
        </label>
      </div>
      <div class="row" style="margin-top:12px;justify-content:flex-end">
        <button class="btn solid" onclick="addSetPlan()">追加する</button>
      </div>
    </div>
  </div>

  <!-- ⑤ 延長オプション -->
  <div class="card">
    <h2>⏱️ 延長オプション</h2>
    <p class="section-desc">POSの延長ボタンを複数の選択肢にします。<br>例: 「+20分 ¥2,000」「+40分 ¥3,000」「+60分 ¥4,000」など。<br>何も登録しない場合は従来通り一種類の延長ボタンが表示されます。</p>

    <table id="extOptTable" style="display:none">
      <thead><tr><th>ボタン名</th><th>延長時間</th><th>料金/人</th><th>有効</th><th></th></tr></thead>
      <tbody id="extOptBody"></tbody>
    </table>
    <div id="extOptEmpty" class="empty-msg" style="display:none">まだ延長オプションが登録されていません。</div>

    <div class="add-section">
      <div class="add-title">＋ 延長オプションを追加</div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px">
        <label>ボタン名<input id="eo_label" placeholder="例：+40分"></label>
        <label>延長時間（分）
          <div class="input-wrap"><input id="eo_minutes" type="number" value="30" min="5" step="5"><span class="input-unit">分</span></div>
        </label>
        <label>料金/人
          <div class="input-wrap"><input id="eo_price" type="number" value="3000" min="0" step="500"><span class="input-unit">円</span></div>
        </label>
      </div>
      <div class="row" style="margin-top:12px;justify-content:flex-end">
        <button class="btn solid" onclick="addExtendOption()">追加する</button>
      </div>
    </div>
  </div>

</div>

<div id="toast" class="ok">保存しました</div>

<script>
const $ = id => document.getElementById(id);
const store = () => parseInt($('storeId').value||'1',10);

/* ── store ID を sessionStorage から自動取得 ── */
(function initStoreId(){
  const sid = sessionStorage.getItem('pos_store') || '1';
  $('storeId').value = sid;
})();

function showToast(msg='保存しました', type='ok'){
  const t=$('toast');
  t.textContent=msg;
  t.className=type;
  t.style.display='block';
  setTimeout(()=>t.style.display='none',2500);
}

async function api(path,opt={}){
  const tk=sessionStorage.getItem('pos_token')||'';
  const o={method:'GET',headers:{'Content-Type':'application/json','X-Role':'owner','X-Token':tk},...opt};
  if(o.body&&typeof o.body!=='string') o.body=JSON.stringify(o.body);
  const r=await fetch(path,o);
  if(r.status===401){sessionStorage.clear();window.location.href='/';return;}
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}

/* ── 深夜加算の単位表示切替 ── */
function updateNightHint(h){
  const t = $(`night${h}_type`).value;
  $(`night${h}_unit`).textContent = t==='rate' ? '%' : '円';
}

/* ── 割引の入力ガイド切替 ── */
function updateDiscHint(){
  const t=$('d_type').value;
  const map = {
    fixed:  {label:'値引き額', unit:'円', hint:'お会計から差し引く金額', placeholder:'例：1000'},
    rate:   {label:'割引率',   unit:'%',  hint:'お会計全体から○%割引', placeholder:'例：10'},
    set_override: {label:'変更後のセット料金', unit:'円', hint:'1人あたりのセット料金を上書き', placeholder:'例：3000'},
    free_drink:   {label:'無料杯数', unit:'杯', hint:'最も安いドリンクから無料になります', placeholder:'例：1'},
  };
  const m=map[t]||map.fixed;
  $('d_value_label').childNodes[0].textContent=m.label;
  $('d_unit').textContent=m.unit;
  $('d_hint').textContent=m.hint;
  $('d_value').placeholder=m.placeholder;
  $('d_value').value='';
}

/* ── データ読み込み ── */
async function loadAll(){
  try{
    const d=await api(`/settings/pricing/${store()}`);
    if(d.config){
      const c=d.config;
      $('sc_rate').value=Math.round((c.service_fee_rate??0.10)*100);
      $('tax_rate').value=Math.round((c.tax_rate??0.10)*100);
      $('rounding').value=c.rounding??'round_half';
      $('calc_order').value=c.calc_order??'sc_then_tax';
      $('vip_fee').value=c.vip_seat_fee??0;
      $('table_charge').value=c.table_charge_pp??0;
      $('night22_en').checked=!!c.night22_enabled;
      $('night22_type').value=c.night22_type??'rate';
      const n22v = c.night22_type==='rate' ? Math.round((c.night22_value??0)*100) : (c.night22_value??0);
      $('night22_val').value=n22v;
      updateNightHint(22);
      $('night24_en').checked=!!c.night24_enabled;
      $('night24_type').value=c.night24_type??'rate';
      const n24v = c.night24_type==='rate' ? Math.round((c.night24_value??0)*100) : (c.night24_value??0);
      $('night24_val').value=n24v;
      updateNightHint(24);
      $('set_minutes').value=c.set_minutes??60;
      $('extend_unit').value=c.extend_unit??30;
    }
    renderSlots(d.slots||[]);
    renderDiscounts(d.discounts||[]);
    // コース選択肢・延長オプション
    try{
      const plans=await api(`/settings/pricing/${store()}/set-plans`);
      renderSetPlans(plans||[]);
    }catch{}
    try{
      const extOpts=await api(`/settings/pricing/${store()}/extend-options`);
      renderExtendOptions(extOpts||[]);
    }catch{}
  }catch(e){showToast(e.message,'err')}
}

/* ── 曜日の表示変換 ── */
const daysLabel = v => {
  const m={'all':'毎日','0,1,2,3':'平日（月〜木）','4,5,6':'週末（金土日）','5,6':'土日','4':'金曜','5':'土曜','6':'日曜'};
  return m[v]||v;
};

function renderSlots(slots){
  const tb=$('slotBody'); tb.innerHTML='';
  $('slotEmpty').style.display = slots.length ? 'none' : 'block';
  $('slotTable').style.display = slots.length ? '' : 'none';
  slots.forEach(s=>{
    const tr=document.createElement('tr');
    const timeRange = `${s.start_hour}:00 〜 ${s.end_hour!=null?s.end_hour+':00':'ラスト'}`;
    tr.innerHTML=`<td style="font-weight:600">${s.label||'—'}</td>
      <td>${timeRange}</td>
      <td>¥${(s.set_price||0).toLocaleString()}</td>
      <td>¥${(s.extend_price||0).toLocaleString()}</td>
      <td>${daysLabel(s.days)}</td><td>${s.holiday_eve?'✓':''}</td>
      <td>${s.is_active?'✓':'—'}</td>
      <td><button class="btn danger" onclick="deleteSlot(${s.id})">削除</button></td>`;
    tb.appendChild(tr);
  });
}

/* ── 設定保存（%→小数変換） ── */
async function saveConfig(){
  try{
    const n22type=$('night22_type').value;
    const n24type=$('night24_type').value;
    await api(`/settings/pricing/${store()}/config`,{method:'POST',body:{
      service_fee_rate: parseFloat($('sc_rate').value||10) / 100,
      tax_rate: parseFloat($('tax_rate').value||10) / 100,
      rounding:$('rounding').value,
      calc_order:$('calc_order').value,
      vip_seat_fee:parseFloat($('vip_fee').value||0),
      table_charge_pp:parseFloat($('table_charge').value||0),
      night22_enabled:$('night22_en').checked,
      night22_type:n22type,
      night22_value: n22type==='rate' ? parseFloat($('night22_val').value||0)/100 : parseFloat($('night22_val').value||0),
      night24_enabled:$('night24_en').checked,
      night24_type:n24type,
      night24_value: n24type==='rate' ? parseFloat($('night24_val').value||0)/100 : parseFloat($('night24_val').value||0),
      set_minutes:parseInt($('set_minutes').value||60),
      extend_unit:parseInt($('extend_unit').value||30),
    }});
    showToast('✅ 基本設定を保存しました');
  }catch(e){showToast(e.message,'err')}
}

async function addSlot(){
  const end=$('n_end').value;
  if(!$('n_label').value){showToast('ルール名を入力してください','err');return;}
  try{
    await api(`/settings/pricing/${store()}/slots`,{method:'POST',body:{
      label:$('n_label').value,
      start_hour:parseInt($('n_start').value||19),
      end_hour:end?parseInt(end):null,
      set_price:parseFloat($('n_set').value||6000),
      extend_price:parseFloat($('n_ext').value||3000),
      days:$('n_days').value,
      holiday_eve:$('n_heve').checked,
      is_active:true,
    }});
    showToast('✅ 時間帯ルールを追加しました');
    $('n_label').value='';
    await loadAll();
  }catch(e){showToast(e.message,'err')}
}

async function deleteSlot(id){
  if(!confirm('この時間帯ルールを削除しますか？'))return;
  try{
    await api(`/settings/pricing/${store()}/slots/${id}`,{method:'DELETE'});
    showToast('削除しました');
    await loadAll();
  }catch(e){showToast(e.message,'err')}
}

/* ── 割引ルール ── */
const discTypeLabel={fixed:'金額値引き',rate:'割引率',set_override:'セット料金変更',free_drink:'ドリンク無料'};
function renderDiscounts(discs){
  const tb=$('discBody'); tb.innerHTML='';
  $('discEmpty').style.display = discs.length ? 'none' : 'block';
  $('discTable').style.display = discs.length ? '' : 'none';
  discs.forEach(d=>{
    let valText='';
    if(d.disc_type==='fixed') valText=`¥${d.value.toLocaleString()} 引き`;
    else if(d.disc_type==='rate') valText=`${Math.round(d.value*100)}% OFF`;
    else if(d.disc_type==='set_override') valText=`セット ¥${d.value.toLocaleString()} に変更`;
    else if(d.disc_type==='free_drink') valText=`${d.value}杯 無料`;
    const tr=document.createElement('tr');
    tr.innerHTML=`<td style="font-weight:600">${d.label||'—'}</td>
      <td>${discTypeLabel[d.disc_type]||d.disc_type}</td>
      <td>${valText}</td><td>${d.is_active?'✓':'—'}</td>
      <td><button class="btn danger" onclick="deleteDiscount(${d.id})">削除</button></td>`;
    tb.appendChild(tr);
  });
}

async function addDiscount(){
  const label=$('d_label').value.trim();
  const rawVal=parseFloat($('d_value').value);
  if(!label){showToast('割引名を入力してください','err');return;}
  if(isNaN(rawVal)||rawVal<=0){showToast('値を正しく入力してください','err');return;}
  const dtype=$('d_type').value;
  // rate の場合、UI は % で入力 → API は小数に変換
  const apiVal = dtype==='rate' ? rawVal/100 : rawVal;
  try{
    await api(`/settings/pricing/${store()}/discounts`,{method:'POST',body:{
      label: label,
      disc_type: dtype,
      value: apiVal,
      is_active: true,
    }});
    showToast('✅ 割引ルールを追加しました');
    $('d_label').value=''; $('d_value').value='';
    await loadAll();
  }catch(e){showToast(e.message,'err')}
}

async function deleteDiscount(id){
  if(!confirm('この割引ルールを削除しますか？'))return;
  try{
    await api(`/settings/pricing/${store()}/discounts/${id}`,{method:'DELETE'});
    showToast('削除しました');
    await loadAll();
  }catch(e){showToast(e.message,'err')}
}

/* ── コース選択肢 ── */
function renderSetPlans(plans){
  const tb=$('planBody'); if(!tb) return;
  tb.innerHTML='';
  $('planEmpty').style.display = plans.length ? 'none' : 'block';
  $('planTable').style.display = plans.length ? '' : 'none';
  plans.forEach(p=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`<td style="font-weight:600">${p.label||'—'}</td>
      <td>${p.minutes}分</td>
      <td>¥${(p.price||0).toLocaleString()}/人</td>
      <td>${p.is_active?'✓':'—'}</td>
      <td><button class="btn danger" onclick="deleteSetPlan(${p.id})">削除</button></td>`;
    tb.appendChild(tr);
  });
}
async function addSetPlan(){
  const label=$('pl_label').value.trim();
  if(!label){showToast('コース名を入力してください','err');return;}
  const minutes=parseInt($('pl_minutes').value||60);
  const price=parseFloat($('pl_price').value||3000);
  if(minutes<1||price<0){showToast('時間と料金を正しく入力してください','err');return;}
  try{
    await api(`/settings/pricing/${store()}/set-plans`,{method:'POST',body:{label,minutes,price,sort_order:0,is_active:true}});
    showToast('✅ コースを追加しました');
    $('pl_label').value='';
    await loadAll();
  }catch(e){showToast(e.message,'err')}
}
async function deleteSetPlan(id){
  if(!confirm('このコースを削除しますか？'))return;
  try{
    await api(`/settings/pricing/${store()}/set-plans/${id}`,{method:'DELETE'});
    showToast('削除しました');
    await loadAll();
  }catch(e){showToast(e.message,'err')}
}

/* ── 延長オプション ── */
function renderExtendOptions(opts){
  const tb=$('extOptBody'); if(!tb) return;
  tb.innerHTML='';
  $('extOptEmpty').style.display = opts.length ? 'none' : 'block';
  $('extOptTable').style.display = opts.length ? '' : 'none';
  opts.forEach(o=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`<td style="font-weight:600">${o.label||'—'}</td>
      <td>+${o.minutes}分</td>
      <td>¥${(o.price||0).toLocaleString()}/人</td>
      <td>${o.is_active?'✓':'—'}</td>
      <td><button class="btn danger" onclick="deleteExtendOption(${o.id})">削除</button></td>`;
    tb.appendChild(tr);
  });
}
async function addExtendOption(){
  const label=$('eo_label').value.trim();
  if(!label){showToast('ボタン名を入力してください','err');return;}
  const minutes=parseInt($('eo_minutes').value||30);
  const price=parseFloat($('eo_price').value||3000);
  if(minutes<1||price<0){showToast('時間と料金を正しく入力してください','err');return;}
  try{
    await api(`/settings/pricing/${store()}/extend-options`,{method:'POST',body:{label,minutes,price,sort_order:0,is_active:true}});
    showToast('✅ 延長オプションを追加しました');
    $('eo_label').value='';
    await loadAll();
  }catch(e){showToast(e.message,'err')}
}
async function deleteExtendOption(id){
  if(!confirm('この延長オプションを削除しますか？'))return;
  try{
    await api(`/settings/pricing/${store()}/extend-options/${id}`,{method:'DELETE'});
    showToast('削除しました');
    await loadAll();
  }catch(e){showToast(e.message,'err')}
}

loadAll();
</script>
</body></html>
""")
