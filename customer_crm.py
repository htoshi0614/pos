"""customer_crm.py — 顧客台帳（CRM）
来店回数、指名キャスト、好みのドリンク、誕生日、メモを記録。リピーター対応強化
"""

from datetime import datetime, date
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Header, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, Text, ForeignKey, func

from db_shared import Base, SessionLocal, require_role

router = APIRouter(tags=["customer_crm"])
ALL_ROLES = ["owner", "manager", "cashier", "staff"]
ADMIN_ROLES = ["owner", "manager"]

# ---------- Model ----------
class CustomerProfile(Base):
    __tablename__ = "customer_profiles"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, index=True)
    name = Column(String, index=True)
    phone = Column(String, default="")
    birthday = Column(String, default="")  # MM-DD or YYYY-MM-DD
    favorite_cast = Column(String, default="")
    favorite_drink = Column(String, default="")
    tags = Column(String, default="")  # comma-separated: VIP,常連,etc
    visit_count = Column(Integer, default=0)
    total_spent = Column(Float, default=0)
    last_visit = Column(String, default="")  # YYYY-MM-DD
    first_visit = Column(String, default="")
    memo = Column(Text, default="")
    is_ng = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class VisitLog(Base):
    __tablename__ = "visit_logs"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, index=True)
    customer_id = Column(Integer, ForeignKey("customer_profiles.id"), index=True)
    session_id = Column(Integer, nullable=True)
    visit_date = Column(String, default="")  # YYYY-MM-DD
    spent = Column(Float, default=0)
    cast_names = Column(String, default="")
    memo = Column(Text, default="")

# ---------- Schemas ----------
class CustomerIn(BaseModel):
    store_id: int
    name: str
    phone: str = ""
    birthday: str = ""
    favorite_cast: str = ""
    favorite_drink: str = ""
    tags: str = ""
    memo: str = ""
    is_ng: bool = False

class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    birthday: Optional[str] = None
    favorite_cast: Optional[str] = None
    favorite_drink: Optional[str] = None
    tags: Optional[str] = None
    memo: Optional[str] = None
    is_ng: Optional[bool] = None

class VisitLogIn(BaseModel):
    store_id: int
    customer_id: int
    session_id: Optional[int] = None
    spent: float = 0
    cast_names: str = ""
    memo: str = ""

# ---------- API ----------
@router.post("/customers")
def create_customer(payload: CustomerIn, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        today = date.today().isoformat()
        c = CustomerProfile(
            store_id=payload.store_id, name=payload.name, phone=payload.phone,
            birthday=payload.birthday, favorite_cast=payload.favorite_cast,
            favorite_drink=payload.favorite_drink, tags=payload.tags,
            memo=payload.memo, is_ng=payload.is_ng,
            first_visit=today, last_visit=today, visit_count=0,
        )
        db.add(c); db.commit(); db.refresh(c)
        return _to_dict(c)
    finally:
        db.close()

@router.get("/customers")
def list_customers(
    store_id: int,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        query = db.query(CustomerProfile).filter_by(store_id=store_id)
        if q:
            query = query.filter(CustomerProfile.name.contains(q))
        if tag:
            query = query.filter(CustomerProfile.tags.contains(tag))
        rows = query.order_by(CustomerProfile.last_visit.desc()).all()
        return [_to_dict(r) for r in rows]
    finally:
        db.close()

@router.get("/customers/{cid}")
def get_customer(cid: int, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        c = db.get(CustomerProfile, cid)
        if not c: raise HTTPException(404, "Customer not found")
        # 来店履歴も返す
        visits = db.query(VisitLog).filter_by(customer_id=cid).order_by(VisitLog.visit_date.desc()).limit(50).all()
        d = _to_dict(c)
        d["visits"] = [{"id":v.id,"date":v.visit_date,"spent":v.spent,"cast_names":v.cast_names,"memo":v.memo} for v in visits]
        return d
    finally:
        db.close()

@router.patch("/customers/{cid}")
def update_customer(cid: int, payload: CustomerUpdate, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        c = db.get(CustomerProfile, cid)
        if not c: raise HTTPException(404, "Customer not found")
        for k, v in (payload.model_dump(exclude_unset=True) if hasattr(payload, 'model_dump') else payload.dict(exclude_unset=True)).items():
            setattr(c, k, v)
        db.commit()
        return _to_dict(c)
    finally:
        db.close()

@router.post("/customers/{cid}/visit")
def record_visit(cid: int, payload: VisitLogIn, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        c = db.get(CustomerProfile, cid)
        if not c: raise HTTPException(404, "Customer not found")
        today = date.today().isoformat()
        vl = VisitLog(
            store_id=payload.store_id, customer_id=cid,
            session_id=payload.session_id, visit_date=today,
            spent=payload.spent, cast_names=payload.cast_names,
            memo=payload.memo,
        )
        db.add(vl)
        c.visit_count = (c.visit_count or 0) + 1
        c.total_spent = (c.total_spent or 0) + payload.spent
        c.last_visit = today
        db.commit()
        return {"ok": True, "visit_count": c.visit_count}
    finally:
        db.close()

@router.get("/customers/birthday/upcoming")
def upcoming_birthdays(store_id: int, days: int = 30, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ALL_ROLES)
    db = SessionLocal()
    try:
        today = date.today()
        rows = db.query(CustomerProfile).filter(
            CustomerProfile.store_id == store_id,
            CustomerProfile.birthday != "",
        ).all()
        result = []
        for c in rows:
            try:
                bd = c.birthday
                if len(bd) == 5:  # MM-DD
                    m, d = int(bd[:2]), int(bd[3:5])
                else:
                    m, d = int(bd[5:7]), int(bd[8:10])
                bd_this_year = date(today.year, m, d)
                if bd_this_year < today:
                    bd_this_year = date(today.year + 1, m, d)
                delta = (bd_this_year - today).days
                if delta <= days:
                    dd = _to_dict(c)
                    dd["days_until_birthday"] = delta
                    result.append(dd)
            except Exception:
                pass
        result.sort(key=lambda x: x["days_until_birthday"])
        return result
    finally:
        db.close()

def _to_dict(c: CustomerProfile) -> dict:
    return {
        "id": c.id, "store_id": c.store_id, "name": c.name,
        "phone": c.phone, "birthday": c.birthday,
        "favorite_cast": c.favorite_cast, "favorite_drink": c.favorite_drink,
        "tags": c.tags, "visit_count": c.visit_count,
        "total_spent": c.total_spent, "last_visit": c.last_visit,
        "first_visit": c.first_visit, "memo": c.memo, "is_ng": c.is_ng,
    }

# ---------- UI ----------
@router.get("/ui/customers", response_class=HTMLResponse)
def ui_customers():
    return HTMLResponse("""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>顧客台帳</title>
<style>
:root{--bg:#0b1220;--card:#0f172a;--line:#1f2937;--text:#e5e7eb;--muted:#b0bec5;--accent:#0ea5e9}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text);padding:20px}
h1{font-size:22px;margin-bottom:16px}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;align-items:center}
input,select,textarea{font-size:14px;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text)}
textarea{min-height:60px;resize:vertical}
.btn{cursor:pointer;padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:#111827;color:var(--text);font-size:14px}
.btn.solid{background:var(--accent);border-color:var(--accent);color:#001018}
.btn.ng{background:#7f1d1d;border-color:#ef4444;color:#fca5a5}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
.ccard{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;cursor:pointer;transition:border-color .2s}
.ccard:hover{border-color:var(--accent)}
.ccard.ng{border-color:#ef4444;opacity:.7}
.ccard .name{font-size:16px;font-weight:700;margin-bottom:6px}
.ccard .meta{font-size:12px;color:var(--muted);line-height:1.6}
.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;background:#1e3a5f;color:#93c5fd;margin:2px}
.tag.vip{background:#713f12;color:#fcd34d}
.tag.ng{background:#7f1d1d;color:#fca5a5}
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;align-items:center;justify-content:center}
.modal-bg.show{display:flex}
.modal{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px;min-width:400px;max-width:560px;max-height:80vh;overflow-y:auto}
.modal h2{margin:0 0 16px;font-size:18px}
.field{margin-bottom:12px}
.field label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
.field input,.field select,.field textarea{width:100%}
.detail-section{margin-top:16px;padding-top:16px;border-top:1px solid var(--line)}
.visit-row{display:flex;gap:10px;padding:6px 0;border-bottom:1px solid #1a2435;font-size:12px}
a{color:var(--accent)}
.birthday-alert{background:#713f12;border:1px solid #f59e0b;border-radius:10px;padding:10px 14px;margin-bottom:16px;font-size:13px}
</style></head><body>
<h1>📋 顧客台帳</h1>
<div id="bdAlert"></div>
<div class="toolbar">
  <input id="search" placeholder="名前で検索..." style="width:200px">
  <select id="filterTag"><option value="">全タグ</option><option value="VIP">VIP</option><option value="常連">常連</option><option value="新規">新規</option></select>
  <button class="btn solid" onclick="showAdd()">+ 新規顧客</button>
  <a href="/ui" style="margin-left:auto;font-size:13px">← POS に戻る</a>
</div>
<div class="grid" id="list"></div>

<div class="modal-bg" id="modal">
<div class="modal">
<h2 id="mTitle">新規顧客</h2>
<div class="field"><label>名前</label><input id="fName"></div>
<div class="field"><label>電話番号</label><input id="fPhone"></div>
<div class="field"><label>誕生日 (MM-DD)</label><input id="fBday" placeholder="03-15"></div>
<div class="field"><label>推しキャスト</label><input id="fCast"></div>
<div class="field"><label>好みのドリンク</label><input id="fDrink"></div>
<div class="field"><label>タグ (カンマ区切り)</label><input id="fTags" placeholder="VIP,常連"></div>
<div class="field"><label>メモ</label><textarea id="fMemo"></textarea></div>
<div class="field"><label><input type="checkbox" id="fNg"> NG顧客</label></div>
<div id="detailSection"></div>
<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
<button class="btn" onclick="closeModal()">閉じる</button>
<button class="btn solid" id="saveBtn" onclick="save()">保存</button>
</div></div></div>

<script>
const storeId=1,role='owner';
const H={'Content-Type':'application/json','X-Role':role};
let editId=null;

async function load(){
  const q=document.getElementById('search').value;
  const tag=document.getElementById('filterTag').value;
  let url=`/customers?store_id=${storeId}`;
  if(q) url+=`&q=${encodeURIComponent(q)}`;
  if(tag) url+=`&tag=${encodeURIComponent(tag)}`;
  const r=await fetch(url,{headers:H}); const data=await r.json();
  document.getElementById('list').innerHTML=data.map(c=>{
    const tags=(c.tags||'').split(',').filter(Boolean).map(t=>`<span class="tag${t==='VIP'?' vip':''}">${t}</span>`).join('');
    return `<div class="ccard${c.is_ng?' ng':''}" onclick="showDetail(${c.id})">
      <div class="name">${c.name}${c.is_ng?' 🚫':''}</div>
      <div class="meta">
        📞 ${c.phone||'-'} ／ 🎂 ${c.birthday||'-'}<br>
        来店 ${c.visit_count||0}回 ／ 累計 ¥${Math.round(c.total_spent||0).toLocaleString()}<br>
        最終来店: ${c.last_visit||'-'}<br>
        ${c.favorite_cast?'推し: '+c.favorite_cast:''} ${c.favorite_drink?'／ 好み: '+c.favorite_drink:''}
      </div>
      <div style="margin-top:6px">${tags}</div>
    </div>`;
  }).join('');
}

async function loadBirthdays(){
  try{
    const r=await fetch(`/customers/birthday/upcoming?store_id=${storeId}&days=14`,{headers:H});
    const data=await r.json();
    if(data.length){
      document.getElementById('bdAlert').innerHTML=`<div class="birthday-alert">🎂 誕生日が近い顧客: ${data.map(c=>`<b>${c.name}</b>(${c.days_until_birthday}日後)`).join('、')}</div>`;
    }
  }catch{}
}

function showAdd(){
  editId=null;document.getElementById('mTitle').textContent='新規顧客';
  ['fName','fPhone','fBday','fCast','fDrink','fTags','fMemo'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('fNg').checked=false;
  document.getElementById('detailSection').innerHTML='';
  document.getElementById('modal').classList.add('show');
}

async function showDetail(id){
  editId=id;
  const r=await fetch(`/customers/${id}`,{headers:H}); const c=await r.json();
  document.getElementById('mTitle').textContent=c.name;
  document.getElementById('fName').value=c.name||'';
  document.getElementById('fPhone').value=c.phone||'';
  document.getElementById('fBday').value=c.birthday||'';
  document.getElementById('fCast').value=c.favorite_cast||'';
  document.getElementById('fDrink').value=c.favorite_drink||'';
  document.getElementById('fTags').value=c.tags||'';
  document.getElementById('fMemo').value=c.memo||'';
  document.getElementById('fNg').checked=!!c.is_ng;
  // 来店履歴
  const visits=(c.visits||[]).map(v=>`<div class="visit-row"><span>${v.date}</span><span>¥${Math.round(v.spent).toLocaleString()}</span><span>${v.cast_names||''}</span><span style="color:var(--muted)">${v.memo||''}</span></div>`).join('');
  document.getElementById('detailSection').innerHTML=`<div class="detail-section"><h3 style="font-size:14px;margin:0 0 8px">来店履歴 (${(c.visits||[]).length}件)</h3>${visits||'<div style="color:var(--muted)">まだありません</div>'}</div>`;
  document.getElementById('modal').classList.add('show');
}

function closeModal(){document.getElementById('modal').classList.remove('show')}

async function save(){
  const body={store_id:storeId,name:document.getElementById('fName').value,phone:document.getElementById('fPhone').value,
    birthday:document.getElementById('fBday').value,favorite_cast:document.getElementById('fCast').value,
    favorite_drink:document.getElementById('fDrink').value,tags:document.getElementById('fTags').value,
    memo:document.getElementById('fMemo').value,is_ng:document.getElementById('fNg').checked};
  if(editId){
    await fetch(`/customers/${editId}`,{method:'PATCH',headers:H,body:JSON.stringify(body)});
  }else{
    await fetch('/customers',{method:'POST',headers:H,body:JSON.stringify(body)});
  }
  closeModal();load();
}

document.getElementById('search').addEventListener('input',load);
document.getElementById('filterTag').addEventListener('change',load);
load(); loadBirthdays();
</script></body></html>""")
