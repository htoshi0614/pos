"""pricing_engine.py — 料金ルールエンジン + /settings/pricing UI"""
from datetime import datetime, timezone
from typing import Optional, List
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey
from sqlalchemy.orm import relationship

from db_shared import Base, SessionLocal

JST = ZoneInfo("Asia/Tokyo")
router = APIRouter(tags=["pricing"])

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

class TimeSlotRuleIn(BaseModel):
    label: str = ""
    start_hour: int
    end_hour: Optional[int] = None
    set_price: float
    extend_price: float
    days: str = "all"
    holiday_eve: bool = False
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
                            start_dt: datetime) -> float:
    """深夜加算を計算して返す（22:00跨ぎ / 24:00跨ぎ）"""
    if not config:
        return 0.0
    start_jst = start_dt.replace(tzinfo=timezone.utc).astimezone(JST)
    hour = start_jst.hour
    surcharge = 0.0

    if config.night22_enabled and hour >= 22:
        if config.night22_type == "rate":
            surcharge += subtotal * config.night22_value
        else:
            surcharge += config.night22_value

    if config.night24_enabled and hour < 4:
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

@router.get("/settings/pricing/{store_id}")
def get_pricing(store_id: int,
                x_role: Optional[str] = Header(None, alias="X-Role")):
    db = SessionLocal()
    try:
        cfg   = db.query(PricingConfig).filter_by(store_id=store_id).first()
        slots = db.query(TimeSlotRule).filter_by(store_id=store_id).order_by(TimeSlotRule.start_hour).all()
        return {
            "config": {k: v for k, v in cfg.__dict__.items() if not k.startswith("_")} if cfg else None,
            "slots":  [{k: v for k, v in s.__dict__.items() if not k.startswith("_")} for s in slots],
        }
    finally:
        db.close()

@router.post("/settings/pricing/{store_id}/config")
def save_pricing_config(store_id: int, payload: PricingConfigIn,
                        x_role: Optional[str] = Header(None, alias="X-Role")):
    db = SessionLocal()
    try:
        cfg = db.query(PricingConfig).filter_by(store_id=store_id).first()
        if cfg:
            for k, v in payload.dict().items():
                setattr(cfg, k, v)
        else:
            cfg = PricingConfig(store_id=store_id, **payload.dict())
            db.add(cfg)
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@router.post("/settings/pricing/{store_id}/slots")
def add_slot(store_id: int, payload: TimeSlotRuleIn,
             x_role: Optional[str] = Header(None, alias="X-Role")):
    db = SessionLocal()
    try:
        rule = TimeSlotRule(store_id=store_id, **payload.dict())
        db.add(rule); db.commit(); db.refresh(rule)
        return {"ok": True, "id": rule.id}
    finally:
        db.close()

@router.put("/settings/pricing/{store_id}/slots/{rule_id}")
def update_slot(store_id: int, rule_id: int, payload: TimeSlotRuleIn,
                x_role: Optional[str] = Header(None, alias="X-Role")):
    db = SessionLocal()
    try:
        rule = db.query(TimeSlotRule).filter_by(id=rule_id, store_id=store_id).first()
        if not rule:
            raise HTTPException(404, "Rule not found")
        for k, v in payload.dict().items():
            setattr(rule, k, v)
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@router.delete("/settings/pricing/{store_id}/slots/{rule_id}")
def delete_slot(store_id: int, rule_id: int,
                x_role: Optional[str] = Header(None, alias="X-Role")):
    db = SessionLocal()
    try:
        rule = db.query(TimeSlotRule).filter_by(id=rule_id, store_id=store_id).first()
        if rule:
            db.delete(rule); db.commit()
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
:root{--bg:#0b1220;--card:#0f172a;--line:#1f2937;--text:#e5e7eb;--muted:#94a3b8;--accent:#0ea5e9;--ink:#0b1220}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text)}
header{position:sticky;top:0;z-index:40;display:flex;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);background:rgba(11,18,32,.95);backdrop-filter:blur(6px)}
header h1{margin:0;font-size:17px}
.nav a{color:var(--accent);text-decoration:none;font-size:14px;padding:6px 10px;border-radius:8px;border:1px solid var(--line)}
.nav a:hover{background:var(--card)}
.container{max-width:900px;margin:0 auto;padding:20px 16px;display:flex;flex-direction:column;gap:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px}
.card h2{margin:0 0 16px;font-size:15px;border-bottom:1px solid var(--line);padding-bottom:10px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--muted)}
input,select{font-size:14px;padding:8px 10px;border-radius:8px;border:1px solid #263244;background:#0a1220;color:var(--text)}
.btn{cursor:pointer;font-size:14px;padding:8px 16px;border-radius:10px;border:1px solid #334155;background:#111827;color:var(--text)}
.btn.solid{background:var(--accent);border-color:var(--accent);color:#001018;font-weight:700}
.btn.danger{background:#7f1d1d;border-color:#ef4444;color:#fff}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.slot-row{display:flex;gap:8px;align-items:center;padding:10px;border:1px solid var(--line);border-radius:10px;flex-wrap:wrap}
.slot-row input,select{width:auto}
.storeRow{display:flex;gap:8px;align-items:center;margin-bottom:16px}
.storeRow label{flex-direction:row;align-items:center;gap:6px;color:var(--text)}
#toast{position:fixed;right:16px;bottom:16px;background:#0e2d14;border:1px solid #22c55e;border-radius:12px;padding:10px 16px;display:none;font-size:14px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--muted)}
@media(max-width:700px){
  .grid2{grid-template-columns:1fr}
  .grid3{grid-template-columns:1fr}
  .container{padding:12px 10px}
  table{font-size:11px;display:block;overflow-x:auto}
  th,td{padding:6px 4px;white-space:nowrap}
  .row{gap:6px}
  header{flex-wrap:wrap;gap:8px}
}
</style></head><body>
<header>
  <h1>料金ルール設定</h1>
  <div class="nav" style="display:flex;gap:8px;margin-left:auto">
    <a href="/ui">← フロア</a>
    <a href="/ui/salary">給与管理</a>
    <a href="/ui/weather">天気/シフト</a>
    <a href="/ui/subscription">サブスク</a>
  </div>
</header>

<div class="container">
  <div class="storeRow">
    <label>店舗ID <input id="storeId" type="number" value="1" style="width:80px"></label>
    <button class="btn solid" onclick="loadAll()">読み込み</button>
  </div>

  <!-- 基本設定 -->
  <div class="card">
    <h2>基本料金設定</h2>
    <div class="grid3">
      <label>サービス料率（例 0.10）<input id="sc_rate" type="number" step="0.01" min="0" max="1" value="0.10"></label>
      <label>消費税率（例 0.10）<input id="tax_rate" type="number" step="0.01" min="0" max="1" value="0.10"></label>
      <label>端数処理<select id="rounding">
        <option value="round_half">四捨五入</option>
        <option value="round_up">切上げ</option>
        <option value="round_down">切捨て</option>
      </select></label>
      <label>計算順序<select id="calc_order">
        <option value="sc_then_tax">小計→SC→税</option>
        <option value="tax_then_sc">小計→税→SC</option>
      </select></label>
      <label>VIP席料（円）<input id="vip_fee" type="number" value="0"></label>
      <label>お通し/TC（円/人）<input id="table_charge" type="number" value="0"></label>
    </div>

    <div style="margin-top:16px;border-top:1px solid var(--line);padding-top:14px">
      <div style="font-size:13px;color:var(--muted);margin-bottom:10px">深夜加算</div>
      <div class="grid2">
        <div>
          <label style="flex-direction:row;align-items:center;gap:6px;margin-bottom:8px">
            <input type="checkbox" id="night22_en"> 22:00跨ぎ加算
          </label>
          <div class="row">
            <select id="night22_type"><option value="rate">率（例 0.25）</option><option value="fixed">固定（円）</option></select>
            <input id="night22_val" type="number" step="0.01" value="0" style="width:120px">
          </div>
        </div>
        <div>
          <label style="flex-direction:row;align-items:center;gap:6px;margin-bottom:8px">
            <input type="checkbox" id="night24_en"> 24:00跨ぎ加算
          </label>
          <div class="row">
            <select id="night24_type"><option value="rate">率（例 0.25）</option><option value="fixed">固定（円）</option></select>
            <input id="night24_val" type="number" step="0.01" value="0" style="width:120px">
          </div>
        </div>
      </div>
    </div>
    <div class="row" style="margin-top:16px;justify-content:flex-end">
      <button class="btn solid" onclick="saveConfig()">保存</button>
    </div>
  </div>

  <!-- 時間帯ルール -->
  <div class="card">
    <h2>時間帯料金ルール</h2>
    <table id="slotTable">
      <thead><tr><th>ラベル</th><th>開始</th><th>終了</th><th>セット料金</th><th>延長料金</th><th>曜日</th><th>祝前日</th><th>有効</th><th></th></tr></thead>
      <tbody id="slotBody"></tbody>
    </table>
    <div style="margin-top:16px;border-top:1px solid var(--line);padding-top:14px">
      <div style="font-size:13px;color:var(--muted);margin-bottom:10px">新規ルール追加</div>
      <div class="row" style="flex-wrap:wrap;gap:8px">
        <input id="n_label" placeholder="ラベル（例 19-21）" style="width:120px">
        <input id="n_start" type="number" placeholder="開始h（19）" style="width:90px" value="19">
        <input id="n_end" type="number" placeholder="終了h（空=LAST）" style="width:110px">
        <input id="n_set" type="number" placeholder="セット料金" style="width:100px" value="6000">
        <input id="n_ext" type="number" placeholder="延長料金" style="width:100px" value="3000">
        <select id="n_days" style="width:160px">
          <option value="all">全曜日</option>
          <option value="0,1,2,3">月〜木</option>
          <option value="4,5,6">金土日</option>
          <option value="5,6">土日</option>
          <option value="4">金曜</option>
          <option value="5">土曜</option>
          <option value="6">日曜</option>
        </select>
        <label style="flex-direction:row;align-items:center;gap:4px">
          <input type="checkbox" id="n_heve"> 祝前日
        </label>
        <button class="btn solid" onclick="addSlot()">追加</button>
      </div>
    </div>
  </div>
</div>

<div id="toast">保存しました</div>

<script>
const $ = id => document.getElementById(id);
const store = () => parseInt($('storeId').value||'1',10);

function showToast(msg='保存しました'){
  const t=$('toast'); t.textContent=msg; t.style.display='block';
  setTimeout(()=>t.style.display='none',2000);
}

async function api(path,opt={}){
  const o={method:'GET',headers:{'Content-Type':'application/json','X-Role':'owner'},...opt};
  if(o.body&&typeof o.body!=='string') o.body=JSON.stringify(o.body);
  const r=await fetch(path,o);
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}

async function loadAll(){
  try{
    const d=await api(`/settings/pricing/${store()}`);
    if(d.config){
      const c=d.config;
      $('sc_rate').value=c.service_fee_rate??0.10;
      $('tax_rate').value=c.tax_rate??0.10;
      $('rounding').value=c.rounding??'round_half';
      $('calc_order').value=c.calc_order??'sc_then_tax';
      $('vip_fee').value=c.vip_seat_fee??0;
      $('table_charge').value=c.table_charge_pp??0;
      $('night22_en').checked=!!c.night22_enabled;
      $('night22_type').value=c.night22_type??'rate';
      $('night22_val').value=c.night22_value??0;
      $('night24_en').checked=!!c.night24_enabled;
      $('night24_type').value=c.night24_type??'rate';
      $('night24_val').value=c.night24_value??0;
    }
    renderSlots(d.slots||[]);
  }catch(e){alert(e.message)}
}

function renderSlots(slots){
  const tb=$('slotBody'); tb.innerHTML='';
  slots.forEach(s=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`<td>${s.label||''}</td><td>${s.start_hour}:00</td>
      <td>${s.end_hour!=null?s.end_hour+':00':'LAST'}</td>
      <td>¥${(s.set_price||0).toLocaleString()}</td>
      <td>¥${(s.extend_price||0).toLocaleString()}</td>
      <td>${s.days||'all'}</td><td>${s.holiday_eve?'✓':''}</td>
      <td>${s.is_active?'✓':'—'}</td>
      <td><button class="btn danger" onclick="deleteSlot(${s.id})">削除</button></td>`;
    tb.appendChild(tr);
  });
}

async function saveConfig(){
  try{
    await api(`/settings/pricing/${store()}/config`,{method:'POST',body:{
      service_fee_rate:parseFloat($('sc_rate').value||0.10),
      tax_rate:parseFloat($('tax_rate').value||0.10),
      rounding:$('rounding').value,
      calc_order:$('calc_order').value,
      vip_seat_fee:parseFloat($('vip_fee').value||0),
      table_charge_pp:parseFloat($('table_charge').value||0),
      night22_enabled:$('night22_en').checked,
      night22_type:$('night22_type').value,
      night22_value:parseFloat($('night22_val').value||0),
      night24_enabled:$('night24_en').checked,
      night24_type:$('night24_type').value,
      night24_value:parseFloat($('night24_val').value||0),
    }});
    showToast('基本設定を保存しました');
  }catch(e){alert(e.message)}
}

async function addSlot(){
  const end=$('n_end').value;
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
    showToast('時間帯ルールを追加しました');
    await loadAll();
  }catch(e){alert(e.message)}
}

async function deleteSlot(id){
  if(!confirm('削除しますか？'))return;
  try{
    await api(`/settings/pricing/${store()}/slots/${id}`,{method:'DELETE'});
    showToast('削除しました');
    await loadAll();
  }catch(e){alert(e.message)}
}

loadAll();
</script>
</body></html>
""")
