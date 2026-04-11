"""stripe_service.py — Stripeサブスク管理 + /ui/subscription"""
import os
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text

from db_shared import Base, SessionLocal, require_role

router = APIRouter(tags=["stripe"])
ADMIN_ROLES = ["owner", "manager"]

# ─────────────────────────── DB Models ───────────────────────────

class StripeSubscription(Base):
    __tablename__ = "stripe_subscriptions"
    id                  = Column(Integer, primary_key=True)
    store_id            = Column(Integer, unique=True)
    stripe_customer_id  = Column(String, default="")
    stripe_sub_id       = Column(String, default="")
    plan_name           = Column(String, default="")
    status              = Column(String, default="inactive")  # active/inactive/canceled/past_due
    current_period_end  = Column(DateTime, nullable=True)
    cancel_at_end       = Column(Boolean, default=False)
    metadata_json       = Column(Text, default="{}")
    updated_at          = Column(DateTime, default=datetime.utcnow)

class StripeConfig(Base):
    __tablename__ = "stripe_configs"
    id               = Column(Integer, primary_key=True)
    store_id         = Column(Integer, unique=True)
    publishable_key  = Column(String, default="")
    secret_key       = Column(String, default="")   # ★本番では暗号化推奨
    webhook_secret   = Column(String, default="")
    price_id_monthly = Column(String, default="")   # Stripe Price ID
    price_id_yearly  = Column(String, default="")

# ─────────────────────────── Pydantic ───────────────────────────

class StripeConfigIn(BaseModel):
    publishable_key: str = ""
    secret_key: str = ""
    webhook_secret: str = ""
    price_id_monthly: str = ""
    price_id_yearly: str = ""

class CreateSessionIn(BaseModel):
    store_id: int
    plan: str = "monthly"   # monthly / yearly
    success_url: str = "/ui/subscription?success=1"
    cancel_url: str  = "/ui/subscription?canceled=1"
    base_url: str = ""  # フロントから渡す（例: https://myapp.com）

# ─────────────────────────── Helpers ───────────────────────────

def get_stripe_client(db, store_id: int):
    try:
        import stripe
    except ImportError:
        raise HTTPException(500, "stripe ライブラリが未インストールです。pip install stripe")

    cfg = db.query(StripeConfig).filter_by(store_id=store_id).first()
    key = (cfg.secret_key if cfg else "") or os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise HTTPException(400, "Stripe シークレットキーが未設定です")
    stripe.api_key = key
    return stripe, cfg

# ─────────────────────────── API Routes ───────────────────────────

@router.get("/stripe-config/{store_id}")
def get_stripe_config(store_id: int,
                      x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        cfg = db.query(StripeConfig).filter_by(store_id=store_id).first()
        if not cfg:
            return None
        # シークレットキーは末尾4文字だけ返す
        masked = ("*" * (len(cfg.secret_key) - 4) + cfg.secret_key[-4:]) if len(cfg.secret_key) > 4 else "****"
        return {
            "store_id": store_id,
            "publishable_key": cfg.publishable_key,
            "secret_key_masked": masked,
            "webhook_secret_set": bool(cfg.webhook_secret),
            "price_id_monthly": cfg.price_id_monthly,
            "price_id_yearly": cfg.price_id_yearly,
        }
    finally:
        db.close()

@router.post("/stripe-config/{store_id}")
def save_stripe_config(store_id: int, payload: StripeConfigIn,
                       x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        cfg = db.query(StripeConfig).filter_by(store_id=store_id).first()
        if cfg:
            for k, v in (payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()).items():
                if v:  # 空文字は既存値を保持
                    setattr(cfg, k, v)
        else:
            cfg = StripeConfig(store_id=store_id, **(payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()))
            db.add(cfg)
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@router.get("/subscription/{store_id}")
def get_subscription(store_id: int,
                     x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        sub = db.query(StripeSubscription).filter_by(store_id=store_id).first()
        if not sub:
            return {"status": "inactive", "store_id": store_id}
        return {k: v for k, v in sub.__dict__.items() if not k.startswith("_")}
    finally:
        db.close()

# ─────────────────────────── 共通ヘルパー（killスイッチ用） ───────────────────────────

ACTIVE_STATUSES = ("active", "trialing")

def is_pos_locked(db) -> tuple[bool, str]:
    """サブスク状態に基づきPOSをロックすべきか判定。
    返り値: (ロックすべき, 理由)

    判定ルール：
    - サブスクが一度も作成されていない → ロックしない（新規導入の猶予）
    - status が active/trialing → ロックしない
    - status がそれ以外でも、current_period_end が未来（= 既払い期間内）→ ロックしない（猶予）
    - 上記以外（期間終了済 or 期間情報なし） → ロック
    """
    sub = db.query(StripeSubscription).first()
    if not sub:
        return False, "no_subscription"
    if sub.status in ACTIVE_STATUSES:
        return False, sub.status
    # 解約済み・支払い遅延でも、支払い済み期間が残っていれば使える
    if sub.current_period_end and sub.current_period_end > datetime.utcnow():
        return False, f"{sub.status or 'inactive'}_grace"
    return True, sub.status or "inactive"

@router.get("/stripe/status")
def stripe_status(store_id: int = 1):
    """サブスク状態（認証不要・middleware用）"""
    db = SessionLocal()
    try:
        sub = db.query(StripeSubscription).filter_by(store_id=store_id).first()
        if not sub:
            sub = db.query(StripeSubscription).first()
        if not sub:
            return {"status": "none", "locked": False}
        locked, reason = is_pos_locked(db)
        in_grace = (not locked) and sub.status not in ACTIVE_STATUSES
        return {
            "status": sub.status or "inactive",
            "locked": locked,
            "in_grace": in_grace,
            "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
            "cancel_at_end": bool(sub.cancel_at_end),
            "plan_name": sub.plan_name or "",
        }
    finally:
        db.close()

@router.post("/subscription/create-checkout")
def create_checkout_session(payload: CreateSessionIn,
                             x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        stripe, cfg = get_stripe_client(db, payload.store_id)
        price_id = (cfg.price_id_monthly if payload.plan == "monthly"
                    else cfg.price_id_yearly) if cfg else ""
        if not price_id:
            raise HTTPException(400, "Price ID が未設定です（Stripe設定で入力してください）")

        base = payload.base_url or os.environ.get("BASE_URL", "http://localhost:8000")
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{base}{payload.success_url}",
            cancel_url=f"{base}{payload.cancel_url}",
            metadata={"store_id": str(payload.store_id)},
        )
        return {"checkout_url": session.url, "session_id": session.id}
    finally:
        db.close()

@router.post("/subscription/portal")
def customer_portal(store_id: int,
                    x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        stripe_client, _ = get_stripe_client(db, store_id)
        sub = db.query(StripeSubscription).filter_by(store_id=store_id).first()
        if not sub or not sub.stripe_customer_id:
            raise HTTPException(400, "サブスクリプションが見つかりません")
        return_base = os.environ.get("BASE_URL", "http://localhost:8000")
        session = stripe_client.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=f"{return_base}/ui/subscription",
        )
        return {"portal_url": session.url}
    finally:
        db.close()

@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """Stripe Webhook エンドポイント（subscriptionイベント処理）"""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    db = SessionLocal()
    try:
        import stripe, json

        # webhook_secret はどれかの設定から取る
        cfg = db.query(StripeConfig).first()
        webhook_secret = (cfg.webhook_secret if cfg else "") or os.environ.get("STRIPE_WEBHOOK_SECRET", "")

        if webhook_secret:
            try:
                event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
            except Exception as e:
                raise HTTPException(400, f"Webhook error: {e}")
        else:
            event = json.loads(payload)

        ev_type = event.get("type", "")
        obj = event.get("data", {}).get("object", {})
        store_id = int(obj.get("metadata", {}).get("store_id", 0))

        if ev_type in ("customer.subscription.created",
                       "customer.subscription.updated",
                       "customer.subscription.deleted"):
            sub_status = obj.get("status", "inactive")
            if ev_type == "customer.subscription.deleted":
                sub_status = "canceled"

            period_end = obj.get("current_period_end")
            period_dt  = datetime.utcfromtimestamp(period_end) if period_end else None

            plan_name = ""
            items = obj.get("items", {}).get("data", [])
            if items:
                plan_name = items[0].get("price", {}).get("nickname", "")

            sub = db.query(StripeSubscription).filter_by(store_id=store_id).first()
            if not sub:
                sub = StripeSubscription(store_id=store_id)
                db.add(sub)
            sub.stripe_customer_id = obj.get("customer", "")
            sub.stripe_sub_id      = obj.get("id", "")
            sub.status             = sub_status
            sub.plan_name          = plan_name
            sub.current_period_end = period_dt
            sub.cancel_at_end      = bool(obj.get("cancel_at_period_end", False))
            sub.updated_at         = datetime.utcnow()
            db.commit()

        return {"received": True}
    finally:
        db.close()

# ─────────────────────────── Subscription UI ───────────────────────────

@router.get("/ui/subscription", response_class=HTMLResponse)
def ui_subscription():
    return HTMLResponse(r"""
<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>サブスク - Girls Bar POS</title>
<style>
:root{--bg:#0b1220;--card:#0f172a;--line:#1f2937;--text:#e5e7eb;--muted:#94a3b8;--accent:#0ea5e9}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text)}
header{position:sticky;top:0;z-index:40;display:flex;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);background:rgba(11,18,32,.95)}
header h1{margin:0;font-size:17px}
.nav a{color:var(--accent);text-decoration:none;font-size:14px;padding:6px 10px;border-radius:8px;border:1px solid var(--line)}
.container{max-width:800px;margin:0 auto;padding:20px 16px;display:flex;flex-direction:column;gap:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px}
.card h2{margin:0 0 14px;font-size:15px;border-bottom:1px solid var(--line);padding-bottom:10px}
label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--muted)}
input{font-size:14px;padding:7px 10px;border-radius:8px;border:1px solid #263244;background:#0a1220;color:var(--text)}
.btn{cursor:pointer;font-size:14px;padding:10px 20px;border-radius:10px;border:1px solid #334155;background:#111827;color:var(--text)}
.btn.solid{background:var(--accent);border-color:var(--accent);color:#001018;font-weight:700}
.btn.purple{background:#4c1d95;border-color:#7c3aed;color:#e9d5ff;font-weight:700}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.status-badge{display:inline-block;padding:4px 12px;border-radius:999px;font-size:13px;font-weight:700}
.active{background:#14532d;color:#86efac;border:1px solid #22c55e}
.inactive{background:#1c1c2e;color:var(--muted);border:1px solid var(--line)}
.canceled{background:#7f1d1d;color:#fca5a5;border:1px solid #ef4444}
.past_due{background:#78350f;color:#fcd34d;border:1px solid #f59e0b}
.alert{padding:12px 16px;border-radius:10px;margin-bottom:16px;font-size:14px}
.alert.success{background:#0f2615;border:1px solid #22c55e;color:#86efac}
.alert.error{background:#1a0e12;border:1px solid #ef4444;color:#fca5a5}
@media(max-width:700px){
  .grid2{grid-template-columns:1fr}
  .container{padding:12px 10px}
  header{flex-wrap:wrap;gap:8px}
  .row{flex-direction:column;align-items:stretch}
}
</style></head><body>
<header>
  <h1>サブスクリプション管理</h1>
  <div class="nav" style="display:flex;gap:8px;margin-left:auto">
    <a href="/ui">← フロア</a>
    <a href="/ui/pricing">料金設定</a>
    <a href="/ui/salary">給与管理</a>
    <a href="/ui/weather">天気/シフト</a>
  </div>
</header>

<div class="container">
  <div class="row">
    <label style="flex-direction:row;align-items:center;gap:6px">店舗 <input id="storeId" type="number" value="1" style="width:70px"></label>
    <button class="btn solid" onclick="loadAll()">読み込み</button>
  </div>

  <!-- アラート -->
  <div id="alertBox" style="display:none"></div>

  <!-- サブスク状況 -->
  <div class="card">
    <h2>現在のサブスク状況</h2>
    <div class="row" style="margin-bottom:12px">
      <span id="statusBadge" class="status-badge inactive">未加入</span>
      <span id="planName" style="color:var(--muted)"></span>
    </div>
    <div style="font-size:13px;color:var(--muted)" id="periodEnd"></div>
    <div id="subActions" class="row" style="margin-top:16px">
      <button class="btn purple" onclick="subscribe('monthly')">月額プラン申し込み</button>
      <button class="btn purple" onclick="subscribe('yearly')">年額プラン申し込み</button>
      <button class="btn" id="portalBtn" onclick="openPortal()" style="display:none">プラン変更・解約</button>
    </div>
  </div>

  <!-- Stripe設定 -->
  <div class="card">
    <h2>Stripe API 設定</h2>
    <div style="font-size:13px;color:var(--muted);margin-bottom:14px">
      Stripe ダッシュボードから取得したキーを設定してください。
      Webhook URL: <code>http://あなたのサーバー/stripe/webhook</code>
    </div>
    <div class="grid2" style="gap:12px;margin-bottom:12px">
      <label>公開可能キー (pk_...)
        <input id="pk_key" placeholder="pk_live_xxx または pk_test_xxx"></label>
      <label>シークレットキー (sk_...)
        <input id="sk_key" type="password" placeholder="sk_live_xxx または sk_test_xxx"></label>
      <label>Webhook シークレット (whsec_...)
        <input id="wh_secret" type="password" placeholder="whsec_xxx"></label>
      <label>月額 Price ID
        <input id="price_monthly" placeholder="price_xxx"></label>
      <label>年額 Price ID
        <input id="price_yearly" placeholder="price_xxx（任意）"></label>
    </div>
    <div class="row" style="justify-content:flex-end">
      <button class="btn solid" onclick="saveStripeConfig()">Stripe設定を保存</button>
    </div>
  </div>
</div>

<script>
const $ = id=>document.getElementById(id);
const params = new URLSearchParams(location.search);
if(params.get('success')==='1'){
  const a=$('alertBox');
  a.style.display='';
  a.innerHTML='<div class="alert success">✅ サブスクリプションの申し込みが完了しました！</div>';
}
if(params.get('canceled')==='1'){
  const a=$('alertBox');
  a.style.display='';
  a.innerHTML='<div class="alert error">キャンセルされました。</div>';
}

async function api(path,opt={}){
  const tk=sessionStorage.getItem('pos_token')||'';
  const o={method:'GET',headers:{'Content-Type':'application/json','X-Role':'owner','X-Token':tk},...opt};
  if(o.body&&typeof o.body!=='string') o.body=JSON.stringify(o.body);
  const r=await fetch(path,o);
  if(r.status===401){sessionStorage.clear();window.location.href='/';return;}
  if(!r.ok) throw new Error(await r.text());
  const ct=r.headers.get('content-type')||'';
  return ct.includes('json')?r.json():r.text();
}

async function loadAll(){
  const s=$('storeId').value;
  try{
    const sub=await api(`/subscription/${s}`);
    const badge=$('statusBadge');
    const status=sub.status||'inactive';
    badge.textContent={active:'✅ 有効',inactive:'未加入',canceled:'解約済',past_due:'⚠️ 支払い遅延'}[status]||status;
    badge.className='status-badge '+status;
    $('planName').textContent=sub.plan_name||'';
    if(sub.current_period_end){
      const d=new Date(sub.current_period_end);
      $('periodEnd').textContent=`次回更新: ${d.toLocaleDateString('ja-JP')}${sub.cancel_at_end?' (解約予定)':''}`;
    }
    $('portalBtn').style.display=(status==='active')?'':'none';
  }catch(e){}

  try{
    const cfg=await api(`/stripe-config/${s}`);
    if(cfg){
      $('pk_key').value=cfg.publishable_key||'';
      $('price_monthly').value=cfg.price_id_monthly||'';
      $('price_yearly').value=cfg.price_id_yearly||'';
    }
  }catch{}
}

async function subscribe(plan){
  const s=$('storeId').value;
  try{
    const res=await api('/subscription/create-checkout',{method:'POST',body:{
      store_id:parseInt(s), plan,
      base_url:window.location.origin
    }});
    if(res.checkout_url) window.location.href=res.checkout_url;
  }catch(e){alert('エラー: '+e.message)}
}

async function openPortal(){
  const s=$('storeId').value;
  try{
    const res=await api(`/subscription/portal?store_id=${s}`,{method:'POST'});
    if(res.portal_url) window.open(res.portal_url,'_blank');
  }catch(e){alert('エラー: '+e.message)}
}

async function saveStripeConfig(){
  const s=$('storeId').value;
  const body={};
  const pk=$('pk_key').value; if(pk) body.publishable_key=pk;
  const sk=$('sk_key').value; if(sk) body.secret_key=sk;
  const wh=$('wh_secret').value; if(wh) body.webhook_secret=wh;
  const pm=$('price_monthly').value; if(pm) body.price_id_monthly=pm;
  const py=$('price_yearly').value; if(py) body.price_id_yearly=py;
  try{
    await api(`/stripe-config/${s}`,{method:'POST',body});
    alert('Stripe設定を保存しました');
  }catch(e){alert(e.message)}
}

loadAll();
</script>
</body></html>
""")
