# app.py
from datetime import datetime, date
from typing import List, Optional, Dict, Literal
import json, hashlib, asyncio, os
from fastapi import FastAPI, HTTPException, Header, WebSocket, WebSocketDisconnect, Request
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

# ---------- 業者パスワード ----------
VENDOR_PASSWORD = os.environ.get("POS_VENDOR_PASSWORD", "posstart2024")

@app.post("/auth/vendor-login")
def vendor_login(payload: dict):
    """業者パスワード認証"""
    pw = payload.get("password", "")
    if pw == VENDOR_PASSWORD:
        return {"ok": True}
    raise HTTPException(401, "パスワードが正しくありません")

# ---------- 申し込みページ (/signup) ----------
@app.get("/signup", response_class=HTMLResponse)
def signup_page():
    return HTMLResponse(r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>お申し込み - POS Start</title>
<style>
:root{--bg:#0b1220;--card:#0f172a;--line:#1f2937;--text:#e5e7eb;--muted:#94a3b8;--accent:#0ea5e9;--green:#22c55e}
*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
html,body{height:100%}
body{background:var(--bg);color:var(--text);display:flex;align-items:center;justify-content:center;min-height:100vh}
.container{width:100%;max-width:520px;padding:24px}
.back{display:inline-block;color:var(--muted);font-size:13px;text-decoration:none;margin-bottom:16px}
.back:hover{color:var(--text)}
.logo-area{text-align:center;margin-bottom:24px}
.logo-area h1{font-size:24px;font-weight:800}
.logo-area h1 span{color:var(--accent)}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:28px;margin-bottom:16px}
.card h2{font-size:16px;margin-bottom:6px}
.card .sub{font-size:13px;color:var(--muted);margin-bottom:20px}
.plan-summary{display:flex;justify-content:space-between;align-items:center;background:#0a1423;border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:20px}
.plan-summary .name{font-weight:700}
.plan-summary .price{font-size:20px;font-weight:800;color:var(--accent)}
.plan-summary .price span{font-size:12px;font-weight:400;color:var(--muted)}
.field{margin-bottom:14px}
.field label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
.field input,.field select{width:100%;padding:11px 14px;font-size:15px;border-radius:10px;border:1px solid var(--line);background:#0a1423;color:var(--text);outline:none}
.field input:focus{border-color:var(--accent)}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.method-tabs{display:flex;border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-bottom:20px}
.method-tab{flex:1;padding:12px;text-align:center;font-size:14px;cursor:pointer;background:#111827;color:var(--muted);border:none;transition:all .15s}
.method-tab.active{background:var(--accent);color:#001018;font-weight:700}
.method-tab:not(:last-child){border-right:1px solid var(--line)}
.card-fields,.bank-fields{display:none}
.card-fields.show,.bank-fields.show{display:block}
.submit-btn{width:100%;padding:14px;border-radius:12px;border:none;font-size:16px;font-weight:700;cursor:pointer;transition:opacity .15s}
.submit-btn:hover{opacity:.9}
.submit-btn:disabled{opacity:.5;cursor:not-allowed}
.submit-btn.card-pay{background:var(--accent);color:#001018}
.submit-btn.bank-pay{background:var(--green);color:#001018}
.secure{display:flex;align-items:center;justify-content:center;gap:6px;font-size:11px;color:var(--muted);margin-top:12px}
.bank-info{background:#0a1423;border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px;font-size:14px;line-height:1.8}
.bank-info .label{font-size:11px;color:var(--muted)}
.success-msg{display:none;text-align:center;padding:40px 20px}
.success-msg h2{color:var(--green);margin-bottom:12px}
.error-msg{color:#ef4444;font-size:13px;margin-top:8px;display:none}
</style>
</head>
<body>
<div class="container">
  <a href="/" class="back">← 戻る</a>
  <div class="logo-area">
    <h1>🍸 <span>POS</span> Start</h1>
  </div>

  <div id="formArea">
  <div class="card">
    <h2>お申し込み</h2>
    <div class="sub">ご利用情報を入力してください</div>

    <div class="plan-summary">
      <div class="name">スタンダードプラン（月額）</div>
      <div class="price">¥80,000<span> /月（税込）</span></div>
    </div>

    <div class="field">
      <label>店舗名</label>
      <input id="shopName" placeholder="例: Bar LUNA">
    </div>
    <div class="row2">
      <div class="field">
        <label>担当者名</label>
        <input id="contactName" placeholder="山田 太郎">
      </div>
      <div class="field">
        <label>電話番号</label>
        <input id="contactPhone" type="tel" placeholder="090-1234-5678">
      </div>
    </div>
    <div class="field">
      <label>メールアドレス</label>
      <input id="contactEmail" type="email" placeholder="info@example.com">
    </div>

    <div style="font-size:13px;color:var(--muted);margin-bottom:8px">お支払い方法</div>
    <div class="method-tabs">
      <button class="method-tab active" onclick="switchMethod('card')">💳 クレジットカード</button>
      <button class="method-tab" onclick="switchMethod('bank')">🏦 口座振込</button>
    </div>

    <div class="card-fields show" id="cardSection">
      <div class="field">
        <label>カード番号</label>
        <input id="cardNum" placeholder="1234 5678 9012 3456" maxlength="19">
      </div>
      <div class="row2">
        <div class="field">
          <label>有効期限</label>
          <input id="cardExp" placeholder="MM/YY" maxlength="5">
        </div>
        <div class="field">
          <label>セキュリティコード</label>
          <input id="cardCvc" placeholder="123" maxlength="4">
        </div>
      </div>
      <button class="submit-btn card-pay" onclick="submitCard()">カードで申し込む</button>
      <div class="secure">🔒 SSL暗号化通信で安全に処理されます</div>
    </div>

    <div class="bank-fields" id="bankSection">
      <div class="bank-info">
        <div class="label">振込先</div>
        <strong>三菱UFJ銀行 ○○支店</strong><br>
        普通 1234567<br>
        カ）ポススタート<br><br>
        <div class="label">お振込金額</div>
        <strong>¥80,000（税込）</strong><br><br>
        <div style="font-size:12px;color:var(--muted)">
          ※ お振込確認後、1営業日以内にアカウントを有効化いたします。<br>
          ※ 振込手数料はお客様負担となります。
        </div>
      </div>
      <button class="submit-btn bank-pay" onclick="submitBank()">振込で申し込む</button>
    </div>

    <div class="error-msg" id="errorMsg"></div>
  </div>
  </div>

  <div class="success-msg" id="successMsg">
    <div style="font-size:48px;margin-bottom:16px">✅</div>
    <h2>お申し込みありがとうございます！</h2>
    <p style="color:var(--muted);font-size:14px;margin-bottom:20px" id="successDetail"></p>
    <a href="/" style="color:var(--accent);font-size:14px">トップに戻る</a>
  </div>
</div>

<script>
function switchMethod(m){
  document.querySelectorAll('.method-tab').forEach(t=>t.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('cardSection').classList.toggle('show', m==='card');
  document.getElementById('bankSection').classList.toggle('show', m==='bank');
}

function validate(){
  const shop=document.getElementById('shopName').value.trim();
  const name=document.getElementById('contactName').value.trim();
  const email=document.getElementById('contactEmail').value.trim();
  const err=document.getElementById('errorMsg');
  if(!shop||!name||!email){
    err.textContent='店舗名・担当者名・メールアドレスは必須です';
    err.style.display='block';
    return false;
  }
  err.style.display='none';
  return true;
}

function submitCard(){
  if(!validate()) return;
  const num=document.getElementById('cardNum').value.trim();
  const exp=document.getElementById('cardExp').value.trim();
  const cvc=document.getElementById('cardCvc').value.trim();
  const err=document.getElementById('errorMsg');
  if(!num||!exp||!cvc){
    err.textContent='カード情報を全て入力してください';
    err.style.display='block';
    return;
  }
  // 実際のStripe決済はStripe.jsで処理
  // ここではデモ表示
  document.getElementById('formArea').style.display='none';
  const s=document.getElementById('successMsg');
  s.style.display='block';
  document.getElementById('successDetail').textContent=
    'クレジットカードでのお申し込みを受け付けました。\n確認メールをお送りしましたのでご確認ください。';
}

function submitBank(){
  if(!validate()) return;
  document.getElementById('formArea').style.display='none';
  const s=document.getElementById('successMsg');
  s.style.display='block';
  document.getElementById('successDetail').textContent=
    '口座振込でのお申し込みを受け付けました。\n上記口座へのお振込をお願いいたします。確認後、アカウントを有効化いたします。';
}

// カード番号フォーマット
document.getElementById('cardNum').addEventListener('input',function(e){
  let v=e.target.value.replace(/\D/g,'');
  v=v.replace(/(.{4})/g,'$1 ').trim();
  e.target.value=v;
});
// 有効期限フォーマット
document.getElementById('cardExp').addEventListener('input',function(e){
  let v=e.target.value.replace(/\D/g,'');
  if(v.length>=2) v=v.slice(0,2)+'/'+v.slice(2);
  e.target.value=v;
});
</script>
</body>
</html>""")

# ---------- ランディングページ (/) ----------
@app.get("/", response_class=HTMLResponse)
def landing_page():
    return HTMLResponse(r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>POS Start - ガールズバー専用POSシステム</title>
<style>
:root{--bg:#0b1220;--card:#0f172a;--line:#1f2937;--text:#e5e7eb;--muted:#94a3b8;--accent:#0ea5e9;--green:#22c55e;--purple:#a855f7}
*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
html,body{height:100%}
body{background:var(--bg);color:var(--text);display:flex;align-items:center;justify-content:center;min-height:100vh}
.container{width:100%;max-width:480px;padding:24px}
.logo-area{text-align:center;margin-bottom:32px}
.logo-area h1{font-size:28px;font-weight:800;letter-spacing:1px}
.logo-area h1 span{color:var(--accent)}
.logo-area p{color:var(--muted);font-size:13px;margin-top:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:28px;margin-bottom:16px}
.card h2{font-size:16px;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.card h2 .icon{font-size:20px}
.plan-box{background:#0a1423;border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}
.plan-price{font-size:28px;font-weight:800;color:var(--accent)}
.plan-price span{font-size:14px;font-weight:400;color:var(--muted)}
.plan-detail{font-size:12px;color:var(--muted);margin-top:6px;line-height:1.7}
.plan-detail li{list-style:none;padding-left:16px;position:relative}
.plan-detail li::before{content:"✓";position:absolute;left:0;color:var(--green);font-weight:700}
.methods{display:flex;flex-direction:column;gap:10px}
.method-btn{display:flex;align-items:center;gap:12px;width:100%;padding:14px 16px;border-radius:12px;border:1px solid var(--line);background:#111827;color:var(--text);font-size:15px;cursor:pointer;transition:all .15s}
.method-btn:hover{border-color:var(--accent);background:#0c1a2e}
.method-btn .m-icon{font-size:22px;width:32px;text-align:center}
.method-btn .m-label{flex:1;text-align:left}
.method-btn .m-sub{font-size:11px;color:var(--muted)}
.method-btn .arrow{color:var(--muted);font-size:18px}
.divider{display:flex;align-items:center;gap:12px;margin:8px 0}
.divider::before,.divider::after{content:"";flex:1;height:1px;background:var(--line)}
.divider span{font-size:12px;color:var(--muted);white-space:nowrap}
.vendor-section{text-align:center}
.vendor-toggle{background:none;border:none;color:var(--muted);font-size:13px;cursor:pointer;padding:8px;text-decoration:underline}
.vendor-toggle:hover{color:var(--text)}
.vendor-form{display:none;margin-top:12px}
.vendor-form.show{display:block}
.input-group{position:relative;margin-bottom:12px}
.input-group input{width:100%;padding:12px 14px;font-size:15px;border-radius:10px;border:1px solid var(--line);background:#0a1423;color:var(--text);outline:none}
.input-group input:focus{border-color:var(--accent)}
.vendor-btn{width:100%;padding:12px;border-radius:10px;border:none;background:var(--purple);color:#fff;font-size:15px;font-weight:700;cursor:pointer;transition:opacity .15s}
.vendor-btn:hover{opacity:.9}
.vendor-btn:disabled{opacity:.5;cursor:not-allowed}
.error-msg{color:#ef4444;font-size:13px;margin-top:8px;display:none}
.footer{text-align:center;margin-top:20px}
.footer p{font-size:11px;color:#475569;line-height:1.6}
@keyframes shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-6px)}75%{transform:translateX(6px)}}
.shake{animation:shake .3s ease}
</style>
</head>
<body>
<div class="container">
  <div class="logo-area">
    <h1>🍸 <span>POS</span> Start</h1>
    <p>ガールズバー専用 POSシステム</p>
  </div>

  <div class="card">
    <h2><span class="icon">💳</span>ご利用プラン</h2>
    <div class="plan-box">
      <div class="plan-price">¥80,000<span> /月（税込）</span></div>
      <ul class="plan-detail">
        <li>全機能利用可能</li>
        <li>複数端末対応（リアルタイム同期）</li>
        <li>顧客管理・売上分析・Zレポート</li>
        <li>自動バックアップ・監査ログ</li>
      </ul>
    </div>
    <div class="methods">
      <button class="method-btn" onclick="payStripe()">
        <div class="m-icon">💳</div>
        <div class="m-label">
          クレジットカードで申し込む
          <div class="m-sub">VISA / Mastercard / AMEX 対応</div>
        </div>
        <div class="arrow">→</div>
      </button>
      <button class="method-btn" onclick="payBank()">
        <div class="m-icon">🏦</div>
        <div class="m-label">
          口座振込で申し込む
          <div class="m-sub">請求書を発行します</div>
        </div>
        <div class="arrow">→</div>
      </button>
    </div>
  </div>

  <div class="card vendor-section">
    <div class="divider"><span>POS業者の方はこちら</span></div>
    <button class="vendor-toggle" onclick="toggleVendor()">パスワードでログイン</button>
    <div class="vendor-form" id="vendorForm">
      <div class="input-group">
        <input id="vendorPw" type="password" placeholder="業者パスワードを入力" autocomplete="off">
      </div>
      <button class="vendor-btn" id="vendorBtn" onclick="vendorLogin()">ログイン</button>
      <div class="error-msg" id="vendorError"></div>
    </div>
  </div>

  <div class="footer">
    <p>© 2024 POS Start — お問い合わせ: support@posstart.jp</p>
  </div>
</div>

<script>
function payStripe(){
  window.location.href='/signup';
}
function payBank(){
  alert('口座振込のご案内をメールでお送りします。\n\n振込先:\n三菱UFJ銀行 ○○支店\n普通 1234567\nカ）ポススタート\n\n月額: ¥80,000（税込）\n\nお振込確認後、アカウントを有効化いたします。');
}
function toggleVendor(){
  const f=document.getElementById('vendorForm');
  f.classList.toggle('show');
  if(f.classList.contains('show')) document.getElementById('vendorPw').focus();
}
async function vendorLogin(){
  const pw=document.getElementById('vendorPw').value;
  const btn=document.getElementById('vendorBtn');
  const err=document.getElementById('vendorError');
  if(!pw){err.textContent='パスワードを入力してください';err.style.display='block';return;}
  btn.disabled=true; btn.textContent='確認中...';
  try{
    const r=await fetch('/auth/vendor-login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});
    if(r.ok){
      sessionStorage.setItem('pos_auth','vendor');
      window.location.href='/ui';
    }else{
      const d=await r.json();
      err.textContent=d.detail||'パスワードが正しくありません';
      err.style.display='block';
      document.getElementById('vendorPw').classList.add('shake');
      setTimeout(()=>document.getElementById('vendorPw').classList.remove('shake'),400);
    }
  }catch(e){
    err.textContent='通信エラーが発生しました';err.style.display='block';
  }finally{
    btn.disabled=false; btn.textContent='ログイン';
  }
}
document.getElementById('vendorPw').addEventListener('keydown',(e)=>{
  if(e.key==='Enter') vendorLogin();
});
</script>
</body>
</html>""")

# ---------- WebSocket Manager (複数端末同期) ----------
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        for ws in self.active[:]:
            try:
                await ws.send_json(message)
            except Exception:
                self.active.remove(ws)

ws_manager = ConnectionManager()

async def notify_clients(event: str, data: dict = None):
    """全端末にイベントを通知"""
    try:
        await ws_manager.broadcast({"event": event, "data": data or {}})
    except Exception:
        pass

def _safe_notify(event: str, data: dict = None):
    """同期関数からWebSocket通知を安全に発火（スレッドセーフ）"""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(notify_clients(event, data))
    except RuntimeError:
        pass  # イベントループなし（ワーカースレッド）→スキップ

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)

# ---------- 監査ログミドルウェア ----------
@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    response = await call_next(request)
    # POST/PUT/PATCH/DELETE のみ記録
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        try:
            db = SessionLocal()
            body_str = ""
            try:
                # bodyは既に消費されている可能性があるので、パスとメソッドのみ記録
                body_str = f"query={dict(request.query_params)}"
            except Exception:
                pass
            role_val = request.headers.get("X-Role", "")
            ip = request.client.host if request.client else ""
            log = AuditLog(
                actor_role=role_val,
                path=str(request.url.path),
                method=request.method,
                payload=body_str[:500],
                ip=ip,
                hash=hashlib.sha256(f"{role_val}{request.url.path}{request.method}".encode()).hexdigest()[:16],
            )
            db.add(log); db.commit()
            db.close()
        except Exception:
            pass
    return response

# ---------- Auth / Role ----------
Role = Literal["owner", "manager", "cashier", "staff"]
def require_role(role_header: Optional[str], allowed: List[str]):
    if not role_header:
        raise HTTPException(401, "Missing X-Role")
    if role_header not in allowed:
        raise HTTPException(403, f"Role '{role_header}' not allowed for this action")

def check_closing_lock(db, session_obj):
    """本締め済みの営業日に属するセッションへの変更をブロック"""
    try:
        from closing import Closing
        jst = ZoneInfo("Asia/Tokyo")
        st = session_obj.start_time.replace(tzinfo=timezone.utc).astimezone(jst)
        biz_date = st.strftime("%Y-%m-%d")
        c = db.query(Closing).filter_by(store_id=session_obj.store_id, business_date=biz_date, status="final").first()
        if c:
            raise HTTPException(423, f"本締め済み（{biz_date}）のため変更できません。管理者に解除を依頼してください。")
    except ImportError:
        pass

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
    cast_id = Column(Integer, ForeignKey("casts.id"), nullable=True)  # ドリンクバック対象キャスト
    qty = Column(Integer, default=1)
    unit_price = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    session = relationship("Session", back_populates="orders")
    item = relationship("Item")
    cast = relationship("Cast")

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
    cast_id: Optional[int] = None  # ドリンクバック対象キャスト（nullならバックなし）

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
    from closing import Closing
    from bottle_keep import BottleKeep
    from customer_crm import CustomerProfile, VisitLog
    from tab_management import TabRecord, TabPayment
    from management import CastGoal
    Base.metadata.create_all(engine)
except Exception as _ext_err:
    print(f"[warn] 拡張モジュール読み込み: {_ext_err}")

# --- orders.cast_id カラム追加マイグレーション ---
try:
    with engine.connect() as conn:
        from sqlalchemy import text, inspect
        cols = [c["name"] for c in inspect(engine).get_columns("orders")]
        if "cast_id" not in cols:
            conn.execute(text("ALTER TABLE orders ADD COLUMN cast_id INTEGER"))
            conn.commit()
            print("[migrate] orders.cast_id added")
except Exception:
    pass

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
    eu = max(1, int(s.extend_unit or 30))
    extends   = (remaining + eu - 1) // eu if remaining > 0 else 0

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
        t = Table(**(payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()))
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

@app.get("/casts", response_model=List[CastOut])
def list_casts(store_id: int, x_role: Optional[Role]=Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        return db.query(Cast).filter_by(store_id=store_id, is_active=True).all()
    finally:
        db.close()

@app.post("/items", response_model=ItemOut)
def create_item(payload: ItemIn, x_role: Optional[Role] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager"])
    db=SessionLocal()
    try:
        it=Item(**(payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict())); db.add(it); db.commit(); db.refresh(it); return it
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
        _safe_notify("checkin", {"session_id": s.id})
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
        check_closing_lock(db, s)
        item = db.get(Item, payload.item_id)
        if not item: raise HTTPException(404, "Item not found")
        o = Order(store_id=s.store_id, session_id=session_id,
                  item_id=payload.item_id, qty=payload.qty, unit_price=item.price,
                  cast_id=payload.cast_id)
        db.add(o); db.commit(); db.refresh(o)
        _safe_notify("order", {"session_id": session_id})

        # ドリンクバック自動記録（キャスト指定 + ドリンクカテゴリの場合）
        if payload.cast_id and item.category == "drink":
            try:
                from cast_salary import CastSalaryConfig, DrinkBackRecord
                cfg = db.query(CastSalaryConfig).filter_by(cast_id=payload.cast_id).first()
                rate = cfg.drink_back_rate if cfg else 0
                if rate > 0:
                    back_amount = item.price * payload.qty * rate
                    rec = DrinkBackRecord(
                        store_id=s.store_id, cast_id=payload.cast_id,
                        session_id=session_id, order_id=o.id, amount=back_amount)
                    db.add(rec); db.commit()
            except Exception:
                pass  # モジュール未読み込みなら無視

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
        check_closing_lock(db, s)
        p = Payment(store_id=s.store_id, session_id=session_id,
                    method=payload.method, amount=payload.amount)
        db.add(p); db.commit()
        _safe_notify("payment", {"session_id": session_id})
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
        check_closing_lock(db, s)
        s.set_minutes = int(s.set_minutes or 60) + int(s.extend_unit or 30)
        db.commit()
        _safe_notify("extend", {"session_id": session_id})
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
        check_closing_lock(db, s)
        s.status = "closed"
        s.end_time = datetime.utcnow()
        inv = issue_invoice(db, s)
        db.commit()
        _safe_notify("checkout", {"session_id": session_id})
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
        check_closing_lock(db, s)
        s.set_minutes = max(s.extend_unit, int(s.set_minutes or 60) - int(s.extend_unit or 30))
        db.commit()
        return {"ok": True, "set_minutes": s.set_minutes}
    finally:
        db.close()


class GuestCountIn(BaseModel):
    guest_count: int

@app.patch("/sessions/{session_id}/guest-count")
def change_guest_count(session_id: int, payload: GuestCountIn, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """人数変更API"""
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s or s.status != "open":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found or closed")
        check_closing_lock(db, s)
        s.guest_count = max(1, payload.guest_count)
        db.commit()
        _safe_notify("guest_count", {"session_id": session_id, "guest_count": s.guest_count})
        return {"ok": True, "guest_count": s.guest_count}
    finally:
        db.close()

class MoveTableIn(BaseModel):
    new_table_id: int

@app.post("/sessions/{session_id}/move")
def move_table(session_id: int, payload: MoveTableIn, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """席変更API：セッションを別テーブルに移動"""
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s or s.status != "open":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found or closed")
        check_closing_lock(db, s)
        new_table = db.get(Table, payload.new_table_id)
        if not new_table:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Table not found")
        # 移動先が使用中でないか確認
        existing = db.query(Session).filter(Session.table_id == payload.new_table_id, Session.status == "open").first()
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, f"{new_table.name} は使用中です")
        old_table_id = s.table_id
        s.table_id = payload.new_table_id
        db.commit()
        _safe_notify("move_table", {"session_id": session_id, "old_table_id": old_table_id, "new_table_id": payload.new_table_id})
        return {"ok": True, "old_table_id": old_table_id, "new_table_id": payload.new_table_id, "new_table_name": new_table.name}
    finally:
        db.close()

class StartTimeIn(BaseModel):
    start_time: str  # HH:MM形式

@app.patch("/sessions/{session_id}/start-time")
def change_start_time(session_id: int, payload: StartTimeIn, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """スタート時間変更API"""
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s or s.status != "open":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found or closed")
        check_closing_lock(db, s)
        # HH:MM → 今日の日付でdatetimeを構築
        hm = payload.start_time.split(":")
        if len(hm) != 2:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "HH:MM形式で入力してください")
        h, m = int(hm[0]), int(hm[1])
        now = datetime.utcnow()
        new_start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        # 深夜帯(0-6時)で入力が昼の場合の補正は不要（そのまま使う）
        s.start_time = new_start
        db.commit()
        _safe_notify("start_time", {"session_id": session_id})
        return {"ok": True, "start_time": new_start.isoformat()}
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
        check_closing_lock(db, s)
        db.delete(s)
        db.commit()
        _safe_notify("cancel_session", {"session_id": session_id})
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
        check_closing_lock(db, s)
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
        _safe_notify("cancel_order", {"session_id": session_id})
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

        # 確定売上（closedセッション）のみ集計
        closed_sessions = (
            db.query(Session)
            .options(
                joinedload(Session.orders).joinedload(Order.item),
                joinedload(Session.payments),
                joinedload(Session.table),
            )
            .filter(Session.store_id == store_id, Session.start_time >= start_utc, Session.status == "closed")
            .all()
        )
        # 見込み売上（openセッション含む）も別途計算
        open_sessions = (
            db.query(Session)
            .options(
                joinedload(Session.orders).joinedload(Order.item),
                joinedload(Session.payments),
                joinedload(Session.table),
            )
            .filter(Session.store_id == store_id, Session.start_time >= start_utc, Session.status == "open")
            .all()
        )

        confirmed_sales = 0
        for s in closed_sessions:
            bill = compute_bill(db, s)
            confirmed_sales += int(bill.get("total", 0))

        projected_sales = confirmed_sales
        for s in open_sessions:
            bill = compute_bill(db, s)
            projected_sales += int(bill.get("total", 0))

        return {
            "store_id": store_id,
            "total_sales": int(projected_sales),
            "confirmed_sales": int(confirmed_sales),
            "session_count": len(closed_sessions) + len(open_sessions),
            "closed_count": len(closed_sessions),
            "open_count": len(open_sessions),
            "period": {
                "start_jst": start_jst.isoformat(),
                "as_of_jst": now_jst.isoformat(),
            },
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

# ---------- 監査ログAPI ----------
@app.get("/audit-logs")
def list_audit_logs(limit: int = 100, x_role: Optional[Role] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner", "manager"])
    db = SessionLocal()
    try:
        rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()
        return [{
            "id": r.id, "ts": r.ts.isoformat() if r.ts else "",
            "role": r.actor_role, "method": r.method, "path": r.path,
            "payload": r.payload, "ip": r.ip,
        } for r in rows]
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
try:
    from management import router as _mgmt_router
    app.include_router(_mgmt_router)
except Exception as e:
    print(f"[warn] management router: {e}")
try:
    from closing import router as _closing_router
    app.include_router(_closing_router)
except Exception as e:
    print(f"[warn] closing router: {e}")
try:
    from bottle_keep import router as _bottle_router
    app.include_router(_bottle_router)
except Exception as e:
    print(f"[warn] bottle_keep router: {e}")
try:
    from customer_crm import router as _crm_router
    app.include_router(_crm_router)
except Exception as e:
    print(f"[warn] customer_crm router: {e}")
try:
    from tab_management import router as _tab_router
    app.include_router(_tab_router)
except Exception as e:
    print(f"[warn] tab_management router: {e}")
try:
    from backup_service import router as _backup_router
    app.include_router(_backup_router)
except Exception as e:
    print(f"[warn] backup_service router: {e}")
try:
    from point_mail import router as _mail_router, MailConfig, MailRecipient, MailLog
    app.include_router(_mail_router)
except Exception as e:
    print(f"[warn] point_mail router: {e}")

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

/* タイマーカード */
#timerCard{border:2px solid transparent;transition:border-color .3s}
#timerCard .cbody{background:linear-gradient(135deg,#0c1626,#0a1423)}

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

<!-- サブスク未払い警告モーダル -->
<div id="subModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;align-items:center;justify-content:center">
<div style="background:#1a0e12;border:2px solid #ef4444;border-radius:16px;padding:28px;max-width:440px;text-align:center">
  <div style="font-size:32px;margin-bottom:10px">⚠️</div>
  <h2 style="margin:0 0 10px;color:#fca5a5">サブスクリプション未払い</h2>
  <p style="color:#b0bec5;font-size:14px;margin:0 0 16px">ご利用を続けるにはサブスクリプションの設定が必要です。</p>
  <div style="display:flex;gap:10px;justify-content:center">
    <a href="/ui/subscription" class="btn solid" style="text-decoration:none;font-size:15px;padding:10px 20px">サブスク設定へ</a>
    <button class="btn" onclick="document.getElementById('subModal').style.display='none'" style="font-size:15px;padding:10px 20px">後で</button>
  </div>
</div></div>

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
  <label style="display:flex;align-items:center;gap:6px;">
    <input id="editToggle" type="checkbox"> 配置編集
  </label>
  <div id="adminNav" style="display:none;gap:6px;margin-left:8px;flex-wrap:wrap">
    <a href="/ui/management" target="_blank" style="color:#f59e0b;font-size:12px;padding:5px 8px;border-radius:8px;border:1px solid #f59e0b44;text-decoration:none;font-weight:700">分析</a>
    <a href="/ui/closing" target="_blank" style="color:#22c55e;font-size:12px;padding:5px 8px;border-radius:8px;border:1px solid #22c55e44;text-decoration:none;font-weight:700">締め</a>
    <a href="/ui/customers" target="_blank" style="color:#a855f7;font-size:12px;padding:5px 8px;border-radius:8px;border:1px solid #a855f744;text-decoration:none">顧客台帳</a>
    <a href="/ui/bottles" target="_blank" style="color:#ec4899;font-size:12px;padding:5px 8px;border-radius:8px;border:1px solid #ec489944;text-decoration:none">ボトルキープ</a>
    <a href="/ui/tabs" target="_blank" style="color:#ef4444;font-size:12px;padding:5px 8px;border-radius:8px;border:1px solid #ef444444;text-decoration:none">伝票</a>
    <a href="/ui/pricing" target="_blank" style="color:#0ea5e9;font-size:12px;padding:5px 8px;border-radius:8px;border:1px solid #1f2937;text-decoration:none">料金</a>
    <a href="/ui/salary" target="_blank" style="color:#0ea5e9;font-size:12px;padding:5px 8px;border-radius:8px;border:1px solid #1f2937;text-decoration:none">給与</a>
    <a href="/ui/backup" target="_blank" style="color:#64748b;font-size:12px;padding:5px 8px;border-radius:8px;border:1px solid #64748b44;text-decoration:none">DB</a>
    <a href="/ui/audit" target="_blank" style="color:#64748b;font-size:12px;padding:5px 8px;border-radius:8px;border:1px solid #64748b44;text-decoration:none">監査</a>
    <a href="/ui/weather" target="_blank" style="color:#0ea5e9;font-size:12px;padding:5px 8px;border-radius:8px;border:1px solid #1f2937;text-decoration:none">天気</a>
    <a href="/ui/mail" target="_blank" style="color:#f59e0b;font-size:12px;padding:5px 8px;border-radius:8px;border:1px solid #f59e0b44;text-decoration:none">メール</a>
    <a href="/ui/subscription" target="_blank" style="color:#0ea5e9;font-size:12px;padding:5px 8px;border-radius:8px;border:1px solid #1f2937;text-decoration:none">サブスク</a>
  </div>
  <div class="muted" style="margin-left:auto;white-space:nowrap">
    <span id="selTable" class="mono" style="font-size:16px;font-weight:700;color:var(--accent)">-</span>
    <span style="margin:0 4px">/</span>
    SS:<span id="selSess" class="mono">-</span>
  </div>
</header>

<div class="page">
  <section class="panel">
    <h2>フロア <span id="floorClock" class="mono muted" style="float:right;font-size:14px"></span></h2>
    <div class="p"><div class="floor-wrap" id="floor"></div></div>
  </section>

  <aside class="side">
    <!-- タイマーカード（常時上部に表示） -->
    <div class="card" id="timerCard" style="display:none">
      <div class="cbody" style="padding:12px">
        <div style="display:flex;align-items:center;gap:12px">
          <div style="flex:1">
            <div style="display:flex;align-items:baseline;gap:8px">
              <div class="mono" id="timerRemain" style="font-size:32px;font-weight:900;color:#22c55e">--</div>
              <span class="muted" style="font-size:13px">分</span>
            </div>
            <div class="muted" id="timerDetail" style="font-size:12px">経過 - / 予約 -</div>
          </div>
          <div id="autoExtendBadge" class="badge off" style="font-size:10px">自動延長: OFF</div>
        </div>
        <div id="timerBar" style="height:6px;border-radius:3px;background:#1e293b;margin-top:8px;overflow:hidden">
          <div id="timerBarFill" style="height:100%;border-radius:3px;background:#22c55e;width:0%;transition:width .5s"></div>
        </div>
      </div>
    </div>

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
            <!-- 入店エリア -->
            <div style="display:flex;gap:6px;align-items:center;margin-bottom:8px">
              <label class="muted" style="font-size:12px;white-space:nowrap">人数</label>
              <select id="guestCount" style="width:70px;font-size:15px;padding:6px">
                <option value="1">1名</option><option value="2">2名</option><option value="3">3名</option>
                <option value="4">4名</option><option value="5">5名</option><option value="6">6名</option>
              </select>
              <button class="bigbtn" id="btnCheckin" style="flex:1;text-align:center;background:#0e7490;border-color:#0e7490;color:#fff;font-weight:700">入店</button>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px">
              <button class="bigbtn" id="btnExtend30" style="text-align:center;font-size:14px">延長 +30分</button>
              <button class="bigbtn" id="btnUnextend" style="text-align:center;font-size:14px">延長取消 -30分</button>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:8px">
              <button class="bigbtn" id="btnAutoExtend" style="text-align:center;font-size:12px">自動延長 OFF</button>
              <button class="bigbtn" id="btnChangeGuest" style="text-align:center;font-size:12px;background:#1a2744;border-color:#3b82f6;color:#93c5fd">人数変更</button>
              <button class="bigbtn" id="btnMoveTable" style="text-align:center;font-size:12px;background:#1a2744;border-color:#3b82f6;color:#93c5fd">席変更</button>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px">
              <button class="bigbtn" id="btnChangeStart" style="text-align:center;font-size:12px;background:#1a2744;border-color:#3b82f6;color:#93c5fd">時間変更</button>
              <button class="bigbtn" id="btnCancelCheckin" style="text-align:center;font-size:12px;color:#fca5a5;border-color:#7f1d1d">入店取消</button>
            </div>
            <hr>
            <!-- 支払いエリア -->
            <div style="margin-bottom:8px">
              <div class="muted" style="font-size:11px;margin-bottom:4px">お支払い</div>
              <div style="display:flex;gap:6px;margin-bottom:6px">
                <input id="payAmount" style="flex:1;font-size:18px;padding:10px;text-align:right;font-weight:700" placeholder="金額" type="number">
              </div>
              <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px">
                <button class="bigbtn" id="btnPayCash" style="text-align:center;font-size:14px;background:#14532d;border-color:#22c55e;color:#4ade80">現金</button>
                <button class="bigbtn" id="btnPayCard" style="text-align:center;font-size:14px;background:#1e3a5f;border-color:#3b82f6;color:#93c5fd">カード</button>
                <button class="bigbtn" id="btnPayQR" style="text-align:center;font-size:14px;background:#3b0764;border-color:#a855f7;color:#c084fc">QR</button>
              </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
              <button class="bigbtn solid" id="btnCheckout" style="text-align:center;font-weight:700;font-size:16px">会計確定</button>
              <button class="bigbtn" id="btnReceipt" style="text-align:center;font-size:14px">領収書</button>
            </div>
            <hr>
            <!-- 伝票（内訳） -->
            <div id="billBox" class="muted mono" style="font-size:12px;line-height:1.6"></div>
          </div>

          <!-- 数量管理：各カテゴリ 0スタート + / - -->
          <div id="pane-drink">
            <div style="margin-bottom:10px;padding:8px;border:1px solid #2a384f;border-radius:10px;background:#0a1624">
              <div style="font-size:12px;color:var(--muted);margin-bottom:4px">ドリンクバック対象キャスト</div>
              <select id="drinkCastSelect" style="width:100%;font-size:15px;padding:8px 10px;border-radius:8px;border:1px solid #263244;background:var(--card);color:var(--text)">
                <option value="">なし（お客様用）</option>
              </select>
            </div>
            <div id="listDrink" class="grid"></div>
            <div class="row" style="justify-content:flex-end;margin-top:10px">
              <button class="btn solid" id="applyDrink" style="font-size:15px;padding:10px 20px">注文確定</button>
            </div>
          </div>
          <div id="pane-bottle">
            <div id="listBottle" class="grid"></div>
            <div class="row" style="justify-content:flex-end;margin-top:10px">
              <button class="btn solid" id="applyBottle" style="font-size:15px;padding:10px 20px">注文確定</button>
            </div>
          </div>
          <div id="pane-food">
            <div id="listFood" class="grid"></div>
            <div class="row" style="justify-content:flex-end;margin-top:10px">
              <button class="btn solid" id="applyFood" style="font-size:15px;padding:10px 20px">注文確定</button>
            </div>
          </div>

        </div>
      </div>
    </div>

    <div class="card">
      <h3>今日の売上</h3>
      <div class="cbody">
        <div class="kv"><div class="muted">確定</div><div class="mono" id="salesConfirmed" style="color:#22c55e;font-size:18px;font-weight:700">-</div></div>
        <div class="kv"><div class="muted">見込み(含open)</div><div class="mono" id="salesToday">-</div></div>
        <div class="kv"><div class="muted">組数</div><div class="mono" id="salesSessions">-</div></div>
        <div style="font-size:10px;color:var(--muted);margin-top:4px" id="wsStatus">WS: 接続中...</div>
      </div>
    </div>
  </aside>
</div>

<div id="toasts"></div>

<script>
/* ====== 認証チェック ====== */
if (!sessionStorage.getItem('pos_auth')) {
  window.location.href = '/';
}

/* ====== 共通 ====== */
const $ = (id)=>document.getElementById(id);
const role = ()=> $('role').value;
const store = ()=> parseInt($('storeId').value||'1',10);

/* 管理ナビの表示切替（owner/managerのみ） */
function updateAdminNav(){
  const nav=$('adminNav');
  if(!nav)return;
  const r=role();
  nav.style.display=(r==='owner'||r==='manager')?'flex':'none';
}
document.addEventListener('DOMContentLoaded',()=>{
  updateAdminNav();
  $('role')?.addEventListener('change', updateAdminNav);
});

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
  if (bill && bill.status === 'closed') return 't-paid';
  if (bill && (bill.due||0) <= 0 && bill.paid > 0) return 't-paid';
  if (remain < 0) return 't-over';
  if (remain <= 10) return 't-warn';
  return 't-ok';
}

/* フロア時計 */
function updateFloorClock(){
  const el=$('floorClock');
  if(el) el.textContent=new Date().toLocaleTimeString('ja-JP',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
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
      const guests=b?(b.guest_count||1):1;
      const total=b?Math.round(b.total||0):0;
      center=`<div class="ttime mono" id="ttime-${t.id}">${remain>=0?`残り ${remain}分`:`超過 ${Math.abs(remain)}分`}</div>
              <div class="small mono" id="tdetail-${t.id}">${guests}名 ¥${total.toLocaleString()}</div>`;
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
    const guests=b.guest_count||1;
    const total=Math.round(b.total||0);
    if(tt) tt.textContent= remain>=0?`残り ${remain}分`:`超過 ${Math.abs(remain)}分`;
    if(td) td.textContent= `${guests}名 ¥${total.toLocaleString()}`;
    el.classList.remove('t-free','t-ok','t-warn','t-over','t-paid');
    el.classList.add(colorClass(remain,b));
  });
}

/* キャスト読み込み（ドリンクバック用セレクト） */
async function loadCasts(){
  try{
    const sel=$('drinkCastSelect'); if(!sel) return;
    const casts = await api(`/casts?store_id=${store()}`);
    sel.innerHTML='<option value="">なし（お客様用）</option>';
    casts.forEach(c=>{ sel.innerHTML+=`<option value="${c.id}">${c.name}</option>`; });
  }catch{}
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

  // ドリンクの場合はキャスト選択を取得
  let castId = null;
  if (cat === 'drink') {
    const sel = $('drinkCastSelect');
    castId = sel ? (parseInt(sel.value) || null) : null;
  }

  // 反映：ここでは「指定数を新規で追加」＋ 減算は cancel API を試行
  for (const [itemIdStr, qty] of entries){
    const itemId = parseInt(itemIdStr,10);
    if (qty>0){
      for (let i=0;i<qty;i++){
        const body = {store_id:store(), item_id:itemId, qty:1};
        if (castId) body.cast_id = castId;
        await api(`/sessions/${currentSessionId}/orders`, {method:'POST', body});
      }
    }else if (qty===0){
    // 0は何もしない
    }
  }

  // 反映後にカウンタをリセット
  Object.keys(qtyState[cat]).forEach(k=>{ qtyState[cat][k]=0; const el=$('q-'+cat+'-'+k); if(el) el.textContent='0'; });
  toast('注文を反映しました'); await refreshBill(); await loadFloor(); refreshSales();
}

/* 支払い（方法指定） */
async function payMethod(method){
  if (!currentSessionId) throw new Error('セッションがありません');
  const v=$('payAmount').value;
  let amt;
  if(v){ amt=parseInt(v.replace(/,/g,''),10); }
  else if(currentBill){ amt=Math.round(currentBill.due||0); }
  else{ amt=0; }
  if(!amt||amt<=0) return toast('金額を入力してください','err');
  await api(`/sessions/${currentSessionId}/payments`,{method:'POST', body:{store_id:store(), method:method, amount:amt}});
  toast(`${method==='cash'?'現金':method==='card'?'カード':'QR'} ¥${amt.toLocaleString()} を記録`);
  $('payAmount').value=''; await refreshBill(); await loadFloor(); refreshSales();
}

/* 入店/延長/取消/支払い/会計 */
async function checkin(){
  if (!selectedTableId){
    const t = await api(`/tables?store_id=${store()}`);
    if (t.length){ selectedTableId=t[0].id; $('selTable').textContent=t[0].name; }
  }
  if (!selectedTableId) throw new Error('テーブルを選択してください');
  const gc=parseInt($('guestCount')?.value||'1',10)||1;
  const s = await api('/sessions',{method:'POST', body:{store_id:store(), table_id:selectedTableId, guest_count:gc, set_minutes:60, extend_unit:30}});
  currentSessionId=s.id; $('selSess').textContent=s.id;
  autoExtendBySession[s.id]=false; reflectAutoExtendBtn();
  toast('入店しました'); await refreshBill(); await loadFloor(); refreshSales(); startLoops();
}
async function extend30(){
  if (!currentSessionId) throw new Error('セッションがありません');
  await api(`/sessions/${currentSessionId}/extend`, {method:'POST'});
  toast('+30分 延長しました'); await refreshBill(); await loadFloor(); refreshSales();
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
  currentSessionId=null; currentBill=null; $('selSess').textContent='-'; reflectAutoExtendBtn();
  renderTimer(null); renderBill(null); await loadFloor(); refreshSales();
}

async function payCash(amount){
  if (!currentSessionId) throw new Error('セッションがありません');
  await api(`/sessions/${currentSessionId}/payments`,{method:'POST', body:{store_id:store(), method:'cash', amount:Number(amount)}});
  toast('支払いを記録しました'); $('payAmount').value=''; await refreshBill(); await loadFloor(); refreshSales();
}
async function checkout(){
  if (!currentSessionId) throw new Error('セッションがありません');
  try{ await api(`/sessions/${currentSessionId}/checkout`, {method:'POST'}); toast('会計を確定しました'); }
  catch(e){ console.warn(e); toast('会計API未実装の可能性（UIは続行）','err'); }
  currentSessionId=null; currentBill=null; $('selSess').textContent='-'; reflectAutoExtendBtn();
  renderTimer(null); renderBill(null); await loadFloor(); refreshSales();
}

/* 人数変更 */
async function changeGuestCount(){
  if (!currentSessionId) return toast('テーブルを選択してください','err');
  const current = currentBill?.guest_count || 1;
  const input = prompt(`現在 ${current}名です。新しい人数を入力:`, current);
  if (!input) return;
  const n = parseInt(input, 10);
  if (isNaN(n) || n < 1 || n > 99) return toast('正しい人数を入力してください','err');
  await api(`/sessions/${currentSessionId}/guest-count`, {method:'PATCH', body:{guest_count: n}});
  toast(`人数を ${n}名 に変更しました`); await refreshBill(); await loadFloor(); refreshSales();
}

/* 席変更 */
async function moveTable(){
  if (!currentSessionId) return toast('テーブルを選択してください','err');
  const tables = floorModel.tables || [];
  const freeT = tables.filter(t => !floorModel.sessionByTable[t.id]);
  if (!freeT.length) return toast('空席がありません','err');
  const list = freeT.map(t => `${t.id}: ${t.name}`).join('\\n');
  const input = prompt('移動先の空席:\\n'+list+'\\n\\nテーブル番号(ID)を入力:', freeT[0].id);
  if (!input) return;
  const tid = parseInt(input, 10);
  if (isNaN(tid)) return toast('正しいIDを入力してください','err');
  try {
    const r = await api(`/sessions/${currentSessionId}/move`, {method:'POST', body:{new_table_id: tid}});
    selectedTableId = tid;
    const tbl = tables.find(t=>t.id===tid);
    if(tbl) $('selTable').textContent = tbl.name;
    toast(`${r.new_table_name || tbl?.name} に移動しました`);
    await refreshBill(); await loadFloor(); refreshSales();
  } catch(e) { toast(e.message||'席変更に失敗しました','err'); }
}

/* スタート時間変更 */
async function changeStartTime(){
  if (!currentSessionId) return toast('テーブルを選択してください','err');
  const now = new Date();
  const hh = String(now.getHours()).padStart(2,'0');
  const mm = String(now.getMinutes()).padStart(2,'0');
  const input = prompt(`新しいスタート時間を HH:MM 形式で入力:`, `${hh}:${mm}`);
  if (!input) return;
  if (!/^\d{1,2}:\d{2}$/.test(input)) return toast('HH:MM形式で入力してください','err');
  await api(`/sessions/${currentSessionId}/start-time`, {method:'PATCH', body:{start_time: input}});
  toast(`スタート時間を ${input} に変更しました`); await refreshBill(); await loadFloor(); refreshSales();
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
  const card=$('timerCard');
  if (!b){ if(card) card.style.display='none'; return; }
  if(card) card.style.display='block';
  const base=b.elapsed_minutes ?? b?.time_breakdown?.total_minutes ?? 0;
  const elapsed=liveElapsed(base, b._fetchedAt);
  const booked=b.booked_minutes ?? 60;
  const remain=booked-elapsed;
  const tr=$('timerRemain');
  if(tr){
    tr.textContent=remain>=0?remain:`-${Math.abs(remain)}`;
    tr.style.color=remain<0?'#ef4444':remain<=10?'#facc15':'#22c55e';
  }
  const td=$('timerDetail');
  if(td) td.textContent=`経過 ${elapsed}分 / 予約 ${booked}分`;
  // プログレスバー
  const pct=Math.min(100, Math.max(0, elapsed/booked*100));
  const fill=$('timerBarFill');
  if(fill){
    fill.style.width=pct+'%';
    fill.style.background=pct>=100?'#ef4444':pct>=80?'#facc15':'#22c55e';
  }
  // 残り5分以下で点滅アラート
  if(remain<=5 && remain>0 && remain%1===0){
    if(card) card.style.borderColor=Date.now()%2000<1000?'#ef4444':'transparent';
  }else if(remain<0){
    if(card) card.style.borderColor='#ef4444';
  }else{
    if(card) card.style.borderColor='transparent';
  }
}
function renderBill(b){
  const box=$('billBox'); if(!box) return;
  if (!b){ box.innerHTML=''; return; }
  const td=b.time_breakdown||{};
  const row=(l,r,cls)=>`<div class="kv"><span>${l}</span><span class="mono${cls?' '+cls:''}">${r}</span></div>`;
  const lines = [];

  // 注文一覧
  if(b.orders&&b.orders.length){
    lines.push(`<div style="margin-bottom:6px">`);
    b.orders.forEach(o=>{
      lines.push(`<div style="display:flex;justify-content:space-between;font-size:11px;padding:2px 0;border-bottom:1px dotted #1f2937">
        <span>${o.name}${o.qty>1?' x'+o.qty:''}</span><span>¥${Math.round(o.amount).toLocaleString()}</span></div>`);
    });
    lines.push(`</div>`);
  }

  lines.push(row('セット/延長', toYen(td.time_amount||0)));
  if(b.order_subtotal>0) lines.push(row('オーダー小計', toYen(b.order_subtotal)));
  if (b.table_charge > 0) lines.push(row('お通し/TC', toYen(b.table_charge)));
  if (b.vip_fee > 0)      lines.push(row('VIP席料', toYen(b.vip_fee)));
  if (b.night_surcharge > 0) lines.push(row('深夜加算', toYen(b.night_surcharge)));
  lines.push(`<hr>`);
  lines.push(row('小計', toYen(b.subtotal)));
  if(b.service_fee) lines.push(row('SC', toYen(b.service_fee)));
  lines.push(row('税', toYen(b.tax)));
  lines.push(`<div class="kv" style="font-size:16px;font-weight:900;margin:8px 0"><span>合計</span><span class="mono">${toYen(b.total)}</span></div>`);
  if(b.paid>0) lines.push(row('支払済', toYen(b.paid)));
  if(b.due>0) lines.push(`<div class="kv" style="font-weight:700"><span style="color:#ef4444">未収</span><span class="mono" style="color:#ef4444;font-size:16px">${toYen(b.due)}</span></div>`);
  box.innerHTML = lines.join('');

  // 支払額にデフォルト値をセット
  const pa=$('payAmount');
  if(pa && !pa.value && b.due>0) pa.placeholder=`¥${Math.round(b.due).toLocaleString()}`;
}

/* 今日の売上（注文/取消/会計で即時反映） */
async function refreshSales(){
  try{
    const d=await api(`/closing?store_id=${store()}`);
    $('salesConfirmed').textContent=(d.confirmed_sales||0).toLocaleString()+' 円';
    $('salesToday').textContent=(d.total_sales||0).toLocaleString()+' 円';
    $('salesSessions').textContent=`${d.closed_count||0}組確定 / ${d.open_count||0}組open`;
  }catch{}
}

/* WebSocket: 他端末からの変更通知で自動リフレッシュ */
let _ws=null;
function connectWS(){
  try{
    const proto=location.protocol==='https:'?'wss:':'ws:';
    _ws=new WebSocket(`${proto}//${location.host}/ws`);
    _ws.onopen=()=>{ if($('wsStatus')) $('wsStatus').textContent='WS: 接続済 ✓'; };
    _ws.onclose=()=>{ if($('wsStatus')) $('wsStatus').textContent='WS: 切断'; setTimeout(connectWS,3000); };
    _ws.onerror=()=>{ _ws.close(); };
    _ws.onmessage=(ev)=>{
      try{
        const msg=JSON.parse(ev.data);
        // 他端末からの変更通知 → 売上&フロアを即時更新
        if(['order','cancel_order','payment','checkout','cancel_session','extend','checkin','guest_count','move_table','start_time'].includes(msg.event)){
          refreshSales();
          loadFloor();
          if(currentSessionId) refreshBill();
        }
      }catch{}
    };
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
  loops.tick=setInterval(()=>{ if(currentBill) renderTimer(currentBill); autoExtendTick(); updateFloorClock(); },1000);
  loops.bill=setInterval(()=>{ if(currentSessionId) refreshBill().catch(()=>{}); },5000);
  loops.sales=setInterval(refreshSales,10000); // WS補完用（10秒ごと）
  loops.floor=setInterval(()=>loadFloor().catch(()=>{}),5000);
  loops.floorTick=setInterval(floorTick,1000);
}

/* 初期化 */
async function initUI(){
  // 入店/延長関係
  $('btnCheckin').addEventListener('click', ()=>checkin().catch(e=>toast(e.message,'err')));
  $('btnExtend30').addEventListener('click', ()=>extend30().catch(e=>toast(e.message,'err')));
  $('btnUnextend').addEventListener('click', ()=>unextend30().catch(e=>toast(e.message,'err')));
  $('btnAutoExtend').addEventListener('click', toggleAutoExtend);
  $('btnCancelCheckin').addEventListener('click', ()=>{
    if(!currentSessionId) return toast('セッションがありません','err');
    if(!confirm('この入店を取り消しますか？注文も全て削除されます。')) return;
    cancelCheckin().catch(e=>toast(e.message,'err'));
  });

  // 人数変更・席変更・スタート時間変更
  $('btnChangeGuest').addEventListener('click', ()=>changeGuestCount().catch(e=>toast(e.message,'err')));
  $('btnMoveTable').addEventListener('click', ()=>moveTable().catch(e=>toast(e.message,'err')));
  $('btnChangeStart').addEventListener('click', ()=>changeStartTime().catch(e=>toast(e.message,'err')));

  // 支払い（3方法）
  $('btnPayCash').addEventListener('click', ()=>payMethod('cash').catch(e=>toast(e.message,'err')));
  $('btnPayCard').addEventListener('click', ()=>payMethod('card').catch(e=>toast(e.message,'err')));
  $('btnPayQR').addEventListener('click', ()=>payMethod('qr').catch(e=>toast(e.message,'err')));
  // 会計確定（確認ダイアログ付き）
  $('btnCheckout').addEventListener('click', ()=>{
    if(!currentSessionId) return toast('セッションがありません','err');
    const due=currentBill?Math.round(currentBill.due||0):0;
    if(due>0){ if(!confirm(`未収 ¥${due.toLocaleString()} がありますが、会計を確定しますか？`)) return; }
    checkout().catch(e=>toast(e.message,'err'));
  });

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
      if(!w){ toast('ポップアップがブロックされています。許可してください。','err'); return; }
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
  await loadCasts();

  try{
    const sess=await api(`/sessions?store_id=${store()}&status=open`);
    if(Array.isArray(sess)&&sess.length){
      currentSessionId=sess[0].id; $('selSess').textContent=currentSessionId; autoExtendBySession[currentSessionId]=false; reflectAutoExtendBtn(); await refreshBill();
    }else{ renderTimer(null); renderBill(null); reflectAutoExtendBtn(); }
  }catch{ renderTimer(null); reflectAutoExtendBtn(); }

  startLoops();
  refreshSales();
  connectWS();

  // サブスク状態チェック（未払いなら警告）
  try{
    const sub=await fetch('/stripe/status?store_id='+store(),{headers:{'X-Role':role()}});
    if(sub.ok){
      const sd=await sub.json();
      if(sd.status&&sd.status!=='active'&&sd.status!=='trialing'){
        $('subModal').style.display='flex';
      }
    }
  }catch{}

  // キーボードショートカット
  document.addEventListener('keydown',(e)=>{
    if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'||e.target.tagName==='SELECT') return;
    if(e.key==='1') document.querySelector('[data-tab="entry"]')?.click();
    if(e.key==='2') document.querySelector('[data-tab="drink"]')?.click();
    if(e.key==='3') document.querySelector('[data-tab="bottle"]')?.click();
    if(e.key==='4') document.querySelector('[data-tab="food"]')?.click();
    if(e.key==='Enter'&&e.ctrlKey){e.preventDefault(); $('btnCheckout')?.click();}
  });
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

# ---------- 監査ログ UI ----------
@app.get("/ui/audit", response_class=HTMLResponse)
def ui_audit():
    return HTMLResponse("""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>監査ログ</title>
<style>
:root{--bg:#0b1220;--card:#0f172a;--line:#1f2937;--text:#e5e7eb;--muted:#b0bec5;--accent:#0ea5e9}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text);padding:20px}
h1{font-size:22px;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left}
th{background:#111827;font-size:11px;color:var(--muted);position:sticky;top:0}
.method{display:inline-block;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:700}
.POST{background:#14532d;color:#4ade80}.DELETE{background:#7f1d1d;color:#fca5a5}
.PATCH{background:#713f12;color:#fcd34d}.PUT{background:#1e3a5f;color:#93c5fd}
a{color:var(--accent)}
.toolbar{display:flex;gap:10px;margin-bottom:16px;align-items:center}
</style></head><body>
<h1>📋 監査ログ</h1>
<div class="toolbar">
  <span style="color:var(--muted);font-size:13px">変更操作の全記録（POST/PUT/PATCH/DELETE）</span>
  <a href="/ui" style="margin-left:auto;font-size:13px">← POS に戻る</a>
</div>
<table>
<thead><tr><th>日時</th><th>ロール</th><th>メソッド</th><th>パス</th><th>詳細</th><th>IP</th></tr></thead>
<tbody id="list"></tbody>
</table>
<script>
async function load(){
  const r=await fetch('/audit-logs?limit=200',{headers:{'X-Role':'owner'}});
  const data=await r.json();
  function esc(s){ const d=document.createElement('div'); d.textContent=s||''; return d.innerHTML; }
  document.getElementById('list').innerHTML=data.map(l=>`<tr>
    <td style="white-space:nowrap;font-size:11px">${esc(l.ts)}</td>
    <td>${esc(l.role||'-')}</td>
    <td><span class="method ${esc(l.method)}">${esc(l.method)}</span></td>
    <td style="font-family:monospace;font-size:11px">${esc(l.path)}</td>
    <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;font-size:11px;color:var(--muted)">${esc(l.payload||'')}</td>
    <td style="font-size:11px">${esc(l.ip||'')}</td>
  </tr>`).join('');
}
load(); setInterval(load,10000);
</script></body></html>""")