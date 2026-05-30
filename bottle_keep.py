"""bottle_keep.py — ボトルキープ管理
お客様名+ボトル種類+保管期限を記録。来店時にキープボトル一覧を表示、期限切れアラート
"""

from datetime import datetime, date, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Header, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, Text, ForeignKey

from db_shared import Base, SessionLocal, require_role

router = APIRouter(tags=["bottle_keep"])
ADMIN_ROLES = ["owner", "manager"]
ALL_ROLES = ["owner", "manager", "cashier", "staff"]

# ---------- Model ----------
class BottleKeep(Base):
    __tablename__ = "bottle_keeps"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, index=True)
    customer_id = Column(Integer, nullable=True)
    customer_name = Column(String, default="")
    item_name = Column(String, default="")
    price = Column(Float, default=0)
    stored_date = Column(String, default="")    # YYYY-MM-DD
    expire_date = Column(String, default="")    # YYYY-MM-DD
    remaining_pct = Column(Integer, default=100)  # 0-100%
    status = Column(String, default="active")   # active / empty / expired / disposed
    memo = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

# ---------- Schemas ----------
class BottleKeepIn(BaseModel):
    store_id: int
    customer_name: str = ""
    customer_id: Optional[int] = None
    item_name: str
    price: float = 0
    expire_days: int = 90
    memo: str = ""

class BottleKeepUpdate(BaseModel):
    remaining_pct: Optional[int] = None
    status: Optional[str] = None
    memo: Optional[str] = None
    expire_date: Optional[str] = None

# ---------- API ----------
@router.post("/bottle-keeps")
def create_bottle_keep(payload: BottleKeepIn, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        today = date.today()
        bk = BottleKeep(
            store_id=payload.store_id,
            customer_id=payload.customer_id,
            customer_name=payload.customer_name,
            item_name=payload.item_name,
            price=payload.price,
            stored_date=today.isoformat(),
            expire_date=(today + timedelta(days=payload.expire_days)).isoformat(),
            memo=payload.memo,
        )
        db.add(bk); db.commit(); db.refresh(bk)
        return _to_dict(bk)
    finally:
        db.close()

@router.get("/bottle-keeps")
def list_bottle_keeps(
    store_id: int,
    status: Optional[str] = None,
    customer_name: Optional[str] = None,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        q = db.query(BottleKeep).filter_by(store_id=store_id)
        if status:
            q = q.filter_by(status=status)
        if customer_name:
            q = q.filter(BottleKeep.customer_name.contains(customer_name))
        # 期限切れ自動更新
        today_str = date.today().isoformat()
        rows = q.order_by(BottleKeep.expire_date.asc()).all()
        for r in rows:
            if r.status == "active" and r.expire_date and r.expire_date < today_str:
                r.status = "expired"
        db.commit()
        return [_to_dict(r) for r in rows]
    finally:
        db.close()

@router.get("/bottle-keeps/{bk_id}")
def get_bottle_keep(bk_id: int, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        bk = db.get(BottleKeep, bk_id)
        if not bk:
            raise HTTPException(404, "BottleKeep not found")
        return _to_dict(bk)
    finally:
        db.close()

@router.patch("/bottle-keeps/{bk_id}")
def update_bottle_keep(bk_id: int, payload: BottleKeepUpdate, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        bk = db.get(BottleKeep, bk_id)
        if not bk:
            raise HTTPException(404, "BottleKeep not found")
        if payload.remaining_pct is not None:
            bk.remaining_pct = payload.remaining_pct
            if payload.remaining_pct <= 0:
                bk.status = "empty"
        if payload.status is not None:
            bk.status = payload.status
        if payload.memo is not None:
            bk.memo = payload.memo
        if payload.expire_date is not None:
            bk.expire_date = payload.expire_date
        db.commit()
        return _to_dict(bk)
    finally:
        db.close()

@router.delete("/bottle-keeps/{bk_id}")
def delete_bottle_keep(bk_id: int, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        bk = db.get(BottleKeep, bk_id)
        if not bk:
            raise HTTPException(404, "BottleKeep not found")
        bk.status = "disposed"
        db.commit()
        return {"ok": True}
    finally:
        db.close()

# 顧客のキープボトル一覧（来店時表示用）
@router.get("/bottle-keeps/customer/{customer_name}")
def get_customer_bottles(customer_name: str, store_id: int, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        today_str = date.today().isoformat()
        rows = db.query(BottleKeep).filter(
            BottleKeep.store_id == store_id,
            BottleKeep.customer_name == customer_name,
            BottleKeep.status.in_(["active", "expired"]),
        ).order_by(BottleKeep.expire_date.asc()).all()
        for r in rows:
            if r.status == "active" and r.expire_date and r.expire_date < today_str:
                r.status = "expired"
        db.commit()
        return [_to_dict(r) for r in rows]
    finally:
        db.close()

def _to_dict(bk: BottleKeep) -> dict:
    today_str = date.today().isoformat()
    days_left = None
    if bk.expire_date:
        try:
            exp = date.fromisoformat(bk.expire_date)
            days_left = (exp - date.today()).days
        except Exception:
            pass
    return {
        "id": bk.id,
        "store_id": bk.store_id,
        "customer_id": bk.customer_id,
        "customer_name": bk.customer_name,
        "item_name": bk.item_name,
        "price": bk.price,
        "stored_date": bk.stored_date,
        "expire_date": bk.expire_date,
        "remaining_pct": bk.remaining_pct,
        "status": bk.status,
        "memo": bk.memo,
        "days_left": days_left,
        "is_expiring_soon": days_left is not None and 0 < days_left <= 7,
        "is_expired": bk.status == "expired" or (days_left is not None and days_left < 0),
    }

# ---------- UI ----------
@router.get("/ui/bottles", response_class=HTMLResponse)
def ui_bottles():
    return HTMLResponse("""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ボトルキープ管理</title>
<style>
:root{--bg:#fafafa;--card:#ffffff;--card2:#ffffff;--line:#eaeaef;--border:#eaeaef;--text:#0a0a0f;--ink:#0a0a0f;--muted:#8a8a95;--body:#4a4a55;--accent:#d64583;--accent-soft:#fdf0f7;--accent-dark:#b03468;--gold:#c9a96e;--gold-soft:#faf3e3;--warn:#f59e0b;--amber:#f59e0b;--err:#ef4444;--red:#ef4444;--ok:#22c55e;--green:#22c55e;--blue:#3b82f6;--purple:#a855f7;}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text);padding:20px}
h1{font-size:22px;margin-bottom:16px}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;align-items:center}
input,select{font-size:14px;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text)}
.btn{cursor:pointer;padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:#111827;color:var(--text);font-size:14px}
.btn.solid{background:var(--accent);border-color:var(--accent);color:#001018}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left}
th{background:#111827;font-size:12px;color:var(--muted)}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px}
.badge.active{background:#14532d;color:#4ade80}
.badge.expired{background:#7f1d1d;color:#fca5a5}
.badge.empty{background:#1e3a5f;color:#93c5fd}
.badge.soon{background:#713f12;color:#fcd34d}
.bar{height:8px;border-radius:4px;background:#1e293b;overflow:hidden}
.bar-fill{height:100%;border-radius:4px;background:#22c55e}
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;align-items:center;justify-content:center}
.modal-bg.show{display:flex}
.modal{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px;min-width:360px;max-width:500px}
.modal h2{margin:0 0 16px;font-size:18px}
.field{margin-bottom:12px}
.field label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
.field input,.field select,.field textarea{width:100%}
.field textarea{min-height:60px;resize:vertical}
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
<h1>🍾 ボトルキープ管理</h1>
<div class="toolbar">
  <input id="search" placeholder="顧客名で検索..." style="width:200px">
  <select id="filterStatus"><option value="">全ステータス</option><option value="active">保管中</option><option value="expired">期限切れ</option><option value="empty">空</option></select>
  <button class="btn solid" onclick="showAdd()">+ 新規キープ</button>
  <a href="/ui" style="margin-left:auto;font-size:13px">← POS に戻る</a>
</div>
<table>
<thead><tr><th>顧客名</th><th>ボトル</th><th>保管日</th><th>期限</th><th>残量</th><th>状態</th><th>操作</th></tr></thead>
<tbody id="list"></tbody>
</table>

<div class="modal-bg" id="addModal">
<div class="modal">
<h2 id="modalTitle">新規ボトルキープ</h2>
<div class="field"><label>顧客名</label><input id="fCust"></div>
<div class="field"><label>ボトル名</label><input id="fItem"></div>
<div class="field"><label>価格</label><input id="fPrice" type="number" value="0"></div>
<div class="field"><label>保管期間（日）</label><input id="fDays" type="number" value="90"></div>
<div class="field"><label>メモ</label><textarea id="fMemo"></textarea></div>
<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
<button class="btn" onclick="closeModal()">キャンセル</button>
<button class="btn solid" onclick="saveNew()">保存</button>
</div></div></div>

<script>
const storeId=1,role='owner';
const H={'Content-Type':'application/json','X-Role':role};
async function load(){
  const s=document.getElementById('search').value;
  const st=document.getElementById('filterStatus').value;
  let url=`/bottle-keeps?store_id=${storeId}`;
  if(st) url+=`&status=${st}`;
  if(s) url+=`&customer_name=${encodeURIComponent(s)}`;
  const r=await fetch(url,{headers:H});
  const data=await r.json();
  const tb=document.getElementById('list');
  tb.innerHTML=data.map(b=>{
    const badge=b.is_expired?'expired':b.is_expiring_soon?'soon':b.status==='empty'?'empty':'active';
    const label=b.is_expired?'期限切れ':b.is_expiring_soon?`残${b.days_left}日`:b.status==='empty'?'空':'保管中';
    return `<tr>
      <td>${b.customer_name}</td><td>${b.item_name}</td><td>${b.stored_date}</td>
      <td>${b.expire_date}${b.days_left!=null?' ('+b.days_left+'日)':''}</td>
      <td><div class="bar" style="width:100px"><div class="bar-fill" style="width:${b.remaining_pct}%;background:${b.remaining_pct<30?'#ef4444':b.remaining_pct<60?'#f59e0b':'#22c55e'}"></div></div> ${b.remaining_pct}%</td>
      <td><span class="badge ${badge}">${label}</span></td>
      <td>
        ${b.status==='active'?`<button class="btn" onclick="updRemain(${b.id})">残量更新</button>`:''}
        ${b.status==='active'?`<button class="btn" onclick="markEmpty(${b.id})">空にする</button>`:''}
      </td>
    </tr>`;
  }).join('');
}
function showAdd(){document.getElementById('addModal').classList.add('show')}
function closeModal(){document.getElementById('addModal').classList.remove('show')}
async function saveNew(){
  const body={store_id:storeId,customer_name:document.getElementById('fCust').value,item_name:document.getElementById('fItem').value,
    price:Number(document.getElementById('fPrice').value),expire_days:Number(document.getElementById('fDays').value),memo:document.getElementById('fMemo').value};
  await fetch('/bottle-keeps',{method:'POST',headers:H,body:JSON.stringify(body)});
  closeModal();load();
}
async function updRemain(id){
  const v=prompt('残量 (0-100%)');if(v==null)return;
  await fetch(`/bottle-keeps/${id}`,{method:'PATCH',headers:H,body:JSON.stringify({remaining_pct:parseInt(v)})});
  load();
}
async function markEmpty(id){
  if(!confirm('空にしますか？'))return;
  await fetch(`/bottle-keeps/${id}`,{method:'PATCH',headers:H,body:JSON.stringify({remaining_pct:0,status:'empty'})});
  load();
}
document.getElementById('search').addEventListener('input',load);
document.getElementById('filterStatus').addEventListener('change',load);
load();
</script></body></html>""")
