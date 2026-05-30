"""tab_management.py — 伝票（ツケ）管理
未払い伝票の一覧、回収状況追跡、期限アラート
"""

from datetime import datetime, date, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Header, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, Text, ForeignKey

from db_shared import Base, SessionLocal, require_role

router = APIRouter(tags=["tab_management"])
ALL_ROLES = ["owner", "manager", "cashier", "staff"]
ADMIN_ROLES = ["owner", "manager"]

# ---------- Model ----------
class TabRecord(Base):
    __tablename__ = "tab_records"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, index=True)
    customer_name = Column(String, default="")
    phone = Column(String, default="")
    session_id = Column(Integer, nullable=True)
    total_amount = Column(Float, default=0)
    paid_amount = Column(Float, default=0)
    status = Column(String, default="open")  # open / partial / paid / overdue
    due_date = Column(String, default="")  # YYYY-MM-DD
    memo = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)

class TabPayment(Base):
    __tablename__ = "tab_payments"
    id = Column(Integer, primary_key=True)
    tab_id = Column(Integer, ForeignKey("tab_records.id"), index=True)
    amount = Column(Float, default=0)
    method = Column(String, default="cash")  # cash / card / transfer
    paid_at = Column(DateTime, default=datetime.utcnow)
    memo = Column(String, default="")

# ---------- Schemas ----------
class TabIn(BaseModel):
    store_id: int
    customer_name: str
    phone: str = ""
    session_id: Optional[int] = None
    total_amount: float
    due_days: int = 30
    memo: str = ""

class TabPaymentIn(BaseModel):
    amount: float
    method: str = "cash"
    memo: str = ""

# ---------- API ----------
@router.post("/tabs")
def create_tab(payload: TabIn, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        today = date.today()
        t = TabRecord(
            store_id=payload.store_id,
            customer_name=payload.customer_name,
            phone=payload.phone,
            session_id=payload.session_id,
            total_amount=payload.total_amount,
            due_date=(today + timedelta(days=payload.due_days)).isoformat(),
            memo=payload.memo,
        )
        db.add(t); db.commit(); db.refresh(t)
        return _to_dict(t, db)
    finally:
        db.close()

@router.get("/tabs")
def list_tabs(
    store_id: int,
    status: Optional[str] = None,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        q = db.query(TabRecord).filter_by(store_id=store_id)
        if status:
            q = q.filter_by(status=status)
        rows = q.order_by(TabRecord.due_date.asc()).all()
        today_str = date.today().isoformat()
        for r in rows:
            if r.status == "open" and r.due_date and r.due_date < today_str:
                r.status = "overdue"
        db.commit()
        return [_to_dict(r, db) for r in rows]
    finally:
        db.close()

@router.get("/tab-summary")
def tab_summary(store_id: int, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        tabs = db.query(TabRecord).filter(
            TabRecord.store_id == store_id,
            TabRecord.status.in_(["open", "partial", "overdue"]),
        ).all()
        total_outstanding = sum(t.total_amount - (t.paid_amount or 0) for t in tabs)
        overdue_count = sum(1 for t in tabs if t.status == "overdue")
        return {
            "total_outstanding": total_outstanding,
            "open_count": len(tabs),
            "overdue_count": overdue_count,
        }
    finally:
        db.close()

@router.get("/tabs/{tab_id}")
def get_tab(tab_id: int, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        t = db.get(TabRecord, tab_id)
        if not t: raise HTTPException(404, "Tab not found")
        return _to_dict(t, db)
    finally:
        db.close()

@router.post("/tabs/{tab_id}/pay")
def pay_tab(tab_id: int, payload: TabPaymentIn, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        t = db.get(TabRecord, tab_id)
        if not t: raise HTTPException(404, "Tab not found")
        if t.status == "paid":
            raise HTTPException(400, "この伝票はすでに完済済みです")
        remaining = t.total_amount - (t.paid_amount or 0)
        if payload.amount > remaining:
            raise HTTPException(400, f"支払い金額（¥{int(payload.amount):,}）が残高（¥{int(remaining):,}）を超えています")
        tp = TabPayment(tab_id=tab_id, amount=payload.amount, method=payload.method, memo=payload.memo)
        db.add(tp)
        t.paid_amount = (t.paid_amount or 0) + payload.amount
        if t.paid_amount >= t.total_amount:
            t.status = "paid"
            t.paid_at = datetime.utcnow()
        else:
            t.status = "partial"
        db.commit()
        return _to_dict(t, db)
    finally:
        db.close()

def _to_dict(t: TabRecord, db) -> dict:
    today_str = date.today().isoformat()
    days_left = None
    if t.due_date:
        try:
            days_left = (date.fromisoformat(t.due_date) - date.today()).days
        except Exception:
            pass
    payments = db.query(TabPayment).filter_by(tab_id=t.id).order_by(TabPayment.paid_at.desc()).all()
    remaining = t.total_amount - (t.paid_amount or 0)
    return {
        "id": t.id, "store_id": t.store_id,
        "customer_name": t.customer_name, "phone": t.phone,
        "session_id": t.session_id,
        "total_amount": t.total_amount,
        "paid_amount": t.paid_amount or 0,
        "remaining": remaining,
        "status": t.status, "due_date": t.due_date,
        "days_left": days_left,
        "is_overdue": t.status == "overdue" or (days_left is not None and days_left < 0),
        "memo": t.memo,
        "created_at": t.created_at.isoformat() if t.created_at else "",
        "payments": [{"id":p.id,"amount":p.amount,"method":p.method,"paid_at":p.paid_at.isoformat() if p.paid_at else "","memo":p.memo} for p in payments],
    }

# ---------- UI ----------
@router.get("/ui/tabs", response_class=HTMLResponse)
def ui_tabs():
    return HTMLResponse("""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>伝票（ツケ）管理</title>
<style>
:root{--bg:#fafafa;--card:#ffffff;--card2:#ffffff;--line:#eaeaef;--border:#eaeaef;--text:#0a0a0f;--ink:#0a0a0f;--muted:#8a8a95;--body:#4a4a55;--accent:#d64583;--accent-soft:#fdf0f7;--accent-dark:#b03468;--gold:#c9a96e;--gold-soft:#faf3e3;--warn:#f59e0b;--amber:#f59e0b;--err:#ef4444;--red:#ef4444;--ok:#22c55e;--green:#22c55e;--blue:#3b82f6;--purple:#a855f7;}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text);padding:20px}
h1{font-size:22px;margin-bottom:16px}
.summary{display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap}
.scard{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 20px;min-width:180px}
.scard .label{font-size:12px;color:var(--muted)}
.scard .val{font-size:24px;font-weight:700;margin-top:4px}
.scard .val.danger{color:#ef4444}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;align-items:center}
input,select{font-size:14px;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text)}
.btn{cursor:pointer;padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:#111827;color:var(--text);font-size:14px}
.btn.solid{background:var(--accent);border-color:var(--accent);color:#001018}
.btn.pay{background:#14532d;border-color:#22c55e;color:#4ade80}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left}
th{background:#111827;font-size:12px;color:var(--muted)}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px}
.badge.open{background:#1e3a5f;color:#93c5fd}
.badge.partial{background:#713f12;color:#fcd34d}
.badge.paid{background:#14532d;color:#4ade80}
.badge.overdue{background:#7f1d1d;color:#fca5a5}
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;align-items:center;justify-content:center}
.modal-bg.show{display:flex}
.modal{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px;min-width:380px;max-width:500px}
.field{margin-bottom:12px}
.field label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
.field input,.field textarea{width:100%}
a{color:var(--accent)}

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
<h1>📝 伝票（ツケ）管理</h1>
<div class="summary" id="summary"></div>
<div class="toolbar">
  <select id="filterStatus"><option value="">全件</option><option value="open">未払い</option><option value="partial">一部回収</option><option value="overdue">期限超過</option><option value="paid">回収済</option></select>
  <button class="btn solid" onclick="showAdd()">+ 新規伝票</button>
  <a href="/ui" style="margin-left:auto;font-size:13px">← POS に戻る</a>
</div>
<table>
<thead><tr><th>顧客名</th><th>電話</th><th>金額</th><th>回収済</th><th>残額</th><th>期限</th><th>状態</th><th>操作</th></tr></thead>
<tbody id="list"></tbody>
</table>

<div class="modal-bg" id="addModal">
<div class="modal">
<h2>新規伝票</h2>
<div class="field"><label>顧客名</label><input id="fCust"></div>
<div class="field"><label>電話番号</label><input id="fPhone"></div>
<div class="field"><label>金額</label><input id="fAmount" type="number"></div>
<div class="field"><label>回収期限（日数）</label><input id="fDays" type="number" value="30"></div>
<div class="field"><label>メモ</label><textarea id="fMemo" style="min-height:60px;resize:vertical"></textarea></div>
<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
<button class="btn" onclick="closeM('addModal')">キャンセル</button>
<button class="btn solid" onclick="saveNew()">保存</button>
</div></div></div>

<div class="modal-bg" id="payModal">
<div class="modal">
<h2>回収記録</h2>
<div class="field"><label>回収金額</label><input id="pAmount" type="number"></div>
<div class="field"><label>方法</label><select id="pMethod"><option value="cash">現金</option><option value="card">カード</option><option value="transfer">振込</option></select></div>
<div class="field"><label>メモ</label><input id="pMemo"></div>
<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
<button class="btn" onclick="closeM('payModal')">キャンセル</button>
<button class="btn pay" onclick="submitPay()">回収記録</button>
</div></div></div>

<script>
const storeId=1,role='owner';
const H={'Content-Type':'application/json','X-Role':role};
let payTabId=null;

async function loadSummary(){
  try{
    const r=await fetch(`/tab-summary?store_id=${storeId}`,{headers:H});
    const d=await r.json();
    document.getElementById('summary').innerHTML=`
      <div class="scard"><div class="label">未回収残高</div><div class="val${d.total_outstanding>0?' danger':''}">¥${Math.round(d.total_outstanding).toLocaleString()}</div></div>
      <div class="scard"><div class="label">未払い件数</div><div class="val">${d.open_count}</div></div>
      <div class="scard"><div class="label">期限超過</div><div class="val danger">${d.overdue_count}</div></div>`;
  }catch{}
}

async function load(){
  const st=document.getElementById('filterStatus').value;
  let url=`/tabs?store_id=${storeId}`;
  if(st) url+=`&status=${st}`;
  const r=await fetch(url,{headers:H}); const data=await r.json();
  document.getElementById('list').innerHTML=data.map(t=>{
    const badge=t.status;
    const label={open:'未払い',partial:'一部回収',paid:'回収済',overdue:'期限超過'}[t.status]||t.status;
    return `<tr${t.is_overdue?' style="background:#1a0e12"':''}>
      <td>${t.customer_name}</td><td>${t.phone||'-'}</td>
      <td>¥${Math.round(t.total_amount).toLocaleString()}</td>
      <td>¥${Math.round(t.paid_amount).toLocaleString()}</td>
      <td style="font-weight:700${t.remaining>0?';color:#ef4444':''}">¥${Math.round(t.remaining).toLocaleString()}</td>
      <td>${t.due_date}${t.days_left!=null?' ('+t.days_left+'日)':''}</td>
      <td><span class="badge ${badge}">${label}</span></td>
      <td>${t.status!=='paid'?`<button class="btn pay" onclick="showPay(${t.id},${t.remaining})">回収</button>`:''}</td>
    </tr>`;
  }).join('');
}

function showAdd(){document.getElementById('addModal').classList.add('show')}
function closeM(id){document.getElementById(id).classList.remove('show')}
function showPay(id,remain){payTabId=id;document.getElementById('pAmount').value=remain;document.getElementById('payModal').classList.add('show')}

async function saveNew(){
  const body={store_id:storeId,customer_name:document.getElementById('fCust').value,phone:document.getElementById('fPhone').value,
    total_amount:Number(document.getElementById('fAmount').value),due_days:Number(document.getElementById('fDays').value),memo:document.getElementById('fMemo').value};
  await fetch('/tabs',{method:'POST',headers:H,body:JSON.stringify(body)});
  closeM('addModal');load();loadSummary();
}

async function submitPay(){
  if(!payTabId)return;
  const body={amount:Number(document.getElementById('pAmount').value),method:document.getElementById('pMethod').value,memo:document.getElementById('pMemo').value};
  await fetch(`/tabs/${payTabId}/pay`,{method:'POST',headers:H,body:JSON.stringify(body)});
  closeM('payModal');payTabId=null;load();loadSummary();
}

document.getElementById('filterStatus').addEventListener('change',load);
load();loadSummary();
</script></body></html>""")
