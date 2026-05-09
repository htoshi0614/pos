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
    payment_method      = Column(String, default="card")   # card / bank / manual
    metadata_json       = Column(Text, default="{}")
    updated_at          = Column(DateTime, default=datetime.utcnow)

class BankSignup(Base):
    """口座振込の申し込み受付（入金確認待ち）"""
    __tablename__ = "bank_signups"
    id            = Column(Integer, primary_key=True)
    shop_name     = Column(String, default="")
    contact_name  = Column(String, default="")
    contact_phone = Column(String, default="")
    contact_email = Column(String, default="")
    status        = Column(String, default="pending")  # pending / paid / canceled
    store_id      = Column(Integer, nullable=True)     # 有効化時に発行
    period_end    = Column(DateTime, nullable=True)    # 有効化時の次回更新日
    note          = Column(Text, default="")
    created_at    = Column(DateTime, default=datetime.utcnow)
    paid_at       = Column(DateTime, nullable=True)

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
        meta = obj.get("metadata", {})
        store_id = int(meta.get("store_id", 0))

        # ── checkout.session.completed: 新規申し込みのカード決済完了 ──
        if ev_type == "checkout.session.completed":
            # client_reference_id: "123" (新規signup) / "store_5" (既存店舗の更新)
            ref = str(obj.get("client_reference_id") or "")
            customer_id = obj.get("customer", "")
            stripe_sub_id = obj.get("subscription", "")
            plan_label = meta.get("plan", "monthly")

            if ref.startswith("store_"):
                # 既存店舗の更新 → サブスクを延長
                try:
                    target_store_id = int(ref.replace("store_", ""))
                    from datetime import timedelta
                    period_end_dt = datetime.utcnow() + timedelta(days=31)
                    sub = db.query(StripeSubscription).filter_by(store_id=target_store_id).first()
                    if not sub:
                        sub = StripeSubscription(store_id=target_store_id)
                        db.add(sub)
                    sub.stripe_customer_id = customer_id
                    sub.stripe_sub_id      = stripe_sub_id
                    sub.status             = "active"
                    sub.current_period_end = period_end_dt
                    sub.cancel_at_end      = False
                    sub.payment_method     = "card"
                    sub.updated_at         = datetime.utcnow()
                    db.commit()
                    print(f"[stripe] checkout完了 store_id={target_store_id} 更新")
                except Exception as e:
                    print(f"[stripe] store更新エラー: {e}")
                signup_id = 0  # 後段の signup処理はスキップ
            else:
                # 新規申し込み: Payment Linkの場合は client_reference_id、API作成の場合は metadata.signup_id
                signup_id = int(ref or meta.get("signup_id", 0) or 0)
            if signup_id:
                signup = db.query(BankSignup).filter_by(id=signup_id).first()
                if signup and signup.status == "pending":
                    # store_id = signup_id をそのまま流用
                    new_store_id = signup_id
                    from datetime import timedelta
                    period_end_dt = datetime.utcnow() + timedelta(days=31)
                    sub = db.query(StripeSubscription).filter_by(store_id=new_store_id).first()
                    if not sub:
                        sub = StripeSubscription(store_id=new_store_id)
                        db.add(sub)
                    sub.stripe_customer_id = customer_id
                    sub.stripe_sub_id      = stripe_sub_id
                    sub.status             = "active"
                    sub.plan_name          = plan_label
                    sub.current_period_end = period_end_dt
                    sub.cancel_at_end      = False
                    sub.payment_method     = "card"
                    sub.updated_at         = datetime.utcnow()
                    signup.status   = "paid"
                    signup.store_id = new_store_id
                    signup.period_end = period_end_dt
                    signup.paid_at  = datetime.utcnow()
                    db.commit()
                    print(f"[stripe] checkout完了 signup_id={signup_id} → store_id={new_store_id}")

        # ── customer.subscription.* : 既存サブスクの更新・解約 ──
        elif ev_type in ("customer.subscription.created",
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

            # store_id がメタデータにない場合は stripe_customer_id / stripe_sub_id で逆引き
            if not store_id:
                cid = obj.get("customer", "")
                sid = obj.get("id", "")
                found = (
                    (db.query(StripeSubscription).filter_by(stripe_customer_id=cid).first() if cid else None)
                    or
                    (db.query(StripeSubscription).filter_by(stripe_sub_id=sid).first() if sid else None)
                )
                if found:
                    store_id = found.store_id

            if store_id:
                sub = db.query(StripeSubscription).filter_by(store_id=store_id).first()
                if not sub:
                    sub = StripeSubscription(store_id=store_id)
                    db.add(sub)
                sub.stripe_customer_id = obj.get("customer", "")
                sub.stripe_sub_id      = obj.get("id", "")
                sub.status             = sub_status
                sub.plan_name          = plan_name or sub.plan_name or ""
                sub.current_period_end = period_dt
                sub.cancel_at_end      = bool(obj.get("cancel_at_period_end", False))
                sub.updated_at         = datetime.utcnow()
                db.commit()
                print(f"[stripe] subscription {ev_type} store_id={store_id} status={sub_status} period_end={period_dt}")

        return {"received": True}
    finally:
        db.close()

# ─────────────────────────── 振込申し込み・手動有効化 ───────────────────────────

# ─────────────────────────── 新規申し込み（Stripe Checkout、公開） ───────────────────────────

class SignupForStripeIn(BaseModel):
    shop_name: str
    contact_name: str
    contact_phone: str = ""
    contact_email: str
    plan: str = "monthly"   # monthly / yearly
    base_url: str = ""

# Stripe Payment Link（固定URL・¥50,000/月）
# このURLは公開してOK（Stripeがホストする決済ページ）
PAYMENT_LINK_URL = os.environ.get(
    "STRIPE_PAYMENT_LINK_URL",
    "https://buy.stripe.com/3cIcMY5vA4nKfT82KN2sM01"
)

@router.post("/signup/stripe")
def create_stripe_signup(payload: SignupForStripeIn):
    """新規申し込み: 申込情報をDBに保存しPayment LinkへリダイレクトするURLを返す
    （Stripe APIを呼ばないので、Secret Keyが無くても動作する）"""
    if not payload.shop_name.strip() or not payload.contact_name.strip() or not payload.contact_email.strip():
        raise HTTPException(400, "店舗名・担当者名・メールアドレスは必須です")

    db = SessionLocal()
    try:
        # 申し込み情報を保存
        signup = BankSignup(
            shop_name=payload.shop_name.strip(),
            contact_name=payload.contact_name.strip(),
            contact_phone=payload.contact_phone.strip(),
            contact_email=payload.contact_email.strip(),
            note=f"stripe_{payload.plan}",
            status="pending",
        )
        db.add(signup)
        db.commit()
        db.refresh(signup)

        # Stripe Payment Link にパラメータを付けてリダイレクト
        # client_reference_id でwebhook時にこのsignupと紐付けできる
        from urllib.parse import quote
        url = (
            f"{PAYMENT_LINK_URL}"
            f"?client_reference_id={signup.id}"
            f"&prefilled_email={quote(payload.contact_email.strip())}"
        )
        return {"checkout_url": url}
    except Exception as e:
        raise HTTPException(400, f"申し込みエラー: {str(e)}")
    finally:
        db.close()

# ─────────────────────────── 振込申し込み・手動有効化 ───────────────────────────

class BankSignupIn(BaseModel):
    shop_name: str
    contact_name: str
    contact_phone: str = ""
    contact_email: str
    note: str = ""

class ManualActivateIn(BaseModel):
    store_id: int
    period_end: str  # "YYYY-MM-DD"
    payment_method: str = "bank"  # bank / manual
    plan_name: str = "monthly_bank"

@router.post("/signup/bank")
def create_bank_signup(payload: BankSignupIn):
    """口座振込の申し込み受付（公開エンドポイント・認証不要）"""
    if not payload.shop_name or not payload.contact_name or not payload.contact_email:
        raise HTTPException(400, "店舗名・担当者名・メールアドレスは必須です")
    db = SessionLocal()
    try:
        signup = BankSignup(
            shop_name=payload.shop_name.strip(),
            contact_name=payload.contact_name.strip(),
            contact_phone=payload.contact_phone.strip(),
            contact_email=payload.contact_email.strip(),
            note=payload.note.strip(),
            status="pending",
        )
        db.add(signup)
        db.commit()
        db.refresh(signup)
        return {"ok": True, "signup_id": signup.id}
    finally:
        db.close()

@router.get("/admin/signups")
def list_signups(status: Optional[str] = None,
                 x_role: Optional[str] = Header(None, alias="X-Role")):
    """振込申し込み一覧（admin限定）"""
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        q = db.query(BankSignup)
        if status:
            q = q.filter_by(status=status)
        rows = q.order_by(BankSignup.created_at.desc()).all()
        return [{
            "id": r.id,
            "shop_name": r.shop_name,
            "contact_name": r.contact_name,
            "contact_phone": r.contact_phone,
            "contact_email": r.contact_email,
            "status": r.status,
            "store_id": r.store_id,
            "period_end": r.period_end.isoformat() if r.period_end else None,
            "note": r.note,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "paid_at": r.paid_at.isoformat() if r.paid_at else None,
        } for r in rows]
    finally:
        db.close()

@router.post("/admin/signups/{signup_id}/activate")
def activate_signup(signup_id: int, payload: ManualActivateIn,
                     x_role: Optional[str] = Header(None, alias="X-Role")):
    """振込入金確認 → サブスクを手動で有効化"""
    require_role(x_role, ADMIN_ROLES)
    try:
        period_dt = datetime.strptime(payload.period_end, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "period_end は YYYY-MM-DD 形式で指定してください")
    db = SessionLocal()
    try:
        signup = db.query(BankSignup).filter_by(id=signup_id).first()
        if not signup:
            raise HTTPException(404, "申し込みが見つかりません")
        # サブスクを作成 or 更新
        sub = db.query(StripeSubscription).filter_by(store_id=payload.store_id).first()
        if not sub:
            sub = StripeSubscription(store_id=payload.store_id)
            db.add(sub)
        sub.status             = "active"
        sub.payment_method     = payload.payment_method
        sub.plan_name          = payload.plan_name
        sub.current_period_end = period_dt
        sub.cancel_at_end      = False
        sub.updated_at         = datetime.utcnow()
        # 申し込みを入金済みに
        signup.status     = "paid"
        signup.store_id   = payload.store_id
        signup.period_end = period_dt
        signup.paid_at    = datetime.utcnow()
        db.commit()
        return {"ok": True, "store_id": payload.store_id, "period_end": payload.period_end}
    finally:
        db.close()

class PaymentLinkIn(BaseModel):
    store_id: int = 1

@router.post("/subscription/payment-link")
def get_payment_link(payload: PaymentLinkIn,
                     x_role: Optional[str] = Header(None, alias="X-Role")):
    """既存店舗向けのPayment Link URL（管理画面の「カード支払い」ボタン用）"""
    require_role(x_role, ADMIN_ROLES)
    from urllib.parse import quote
    db = SessionLocal()
    try:
        # 既存サブスクから顧客メールを推定（あれば事前入力）
        sub = db.query(StripeSubscription).filter_by(store_id=payload.store_id).first()
        prefill_email = ""
        # client_reference_id は store_id（既存の更新としてwebhook側で扱う）
        url = f"{PAYMENT_LINK_URL}?client_reference_id=store_{payload.store_id}"
        if prefill_email:
            url += f"&prefilled_email={quote(prefill_email)}"
        return {"checkout_url": url}
    finally:
        db.close()

@router.post("/subscription/manual-activate")
def manual_activate(payload: ManualActivateIn,
                    x_role: Optional[str] = Header(None, alias="X-Role")):
    """既存店舗を手動で有効化（振込再入金など、申し込み経由しないケース用）"""
    require_role(x_role, ADMIN_ROLES)
    try:
        period_dt = datetime.strptime(payload.period_end, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "period_end は YYYY-MM-DD 形式で指定してください")
    db = SessionLocal()
    try:
        sub = db.query(StripeSubscription).filter_by(store_id=payload.store_id).first()
        if not sub:
            sub = StripeSubscription(store_id=payload.store_id)
            db.add(sub)
        sub.status             = "active"
        sub.payment_method     = payload.payment_method
        sub.plan_name          = payload.plan_name
        sub.current_period_end = period_dt
        sub.cancel_at_end      = False
        sub.updated_at         = datetime.utcnow()
        db.commit()
        return {"ok": True, "store_id": payload.store_id, "period_end": payload.period_end}
    finally:
        db.close()

# ─────────────────────────── Subscription UI ───────────────────────────

@router.get("/ui/subscription", response_class=HTMLResponse)
def ui_subscription():
    return HTMLResponse(r"""
<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>サブスク - POS Start</title>
<style>
:root{
  --bg:#0b1220;--card:#0f172a;--line:#1f2937;--text:#e5e7eb;--muted:#94a3b8;
  --accent:#0ea5e9;--green:#22c55e;--red:#ef4444;--amber:#f59e0b;--purple:#a855f7;
}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text);min-height:100vh}
header{position:sticky;top:0;z-index:40;display:flex;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);background:rgba(11,18,32,.95);backdrop-filter:blur(8px)}
header h1{margin:0;font-size:17px}
.nav{display:flex;gap:6px;margin-left:auto;flex-wrap:wrap}
.nav a{color:var(--muted);text-decoration:none;font-size:13px;padding:6px 10px;border-radius:8px}
.nav a:hover{color:var(--text);background:#1a2438}

.container{max-width:680px;margin:0 auto;padding:20px 16px;display:flex;flex-direction:column;gap:18px}

/* ---- ヒーロー（メイン状況） ---- */
.hero{
  background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);
  border:1px solid var(--line);border-radius:20px;padding:28px 24px;text-align:center;
  position:relative;overflow:hidden;
}
.hero.active{border-color:#1e7a4a;background:linear-gradient(135deg,#0a2418 0%,#0d3624 100%)}
.hero.warning{border-color:#7c5a1a;background:linear-gradient(135deg,#241a08 0%,#3a2810 100%)}
.hero.danger{border-color:#7c1d1d;background:linear-gradient(135deg,#1f0a0a 0%,#321212 100%)}

.hero-status{font-size:13px;color:var(--muted);margin-bottom:8px;letter-spacing:1px;text-transform:uppercase}
.hero-icon{font-size:48px;line-height:1;margin-bottom:6px}
.hero-title{font-size:32px;font-weight:800;margin-bottom:4px}
.hero-title.green{color:#86efac}
.hero-title.amber{color:#fcd34d}
.hero-title.red{color:#fca5a5}
.hero-title.gray{color:#cbd5e1}
.hero-sub{font-size:14px;color:var(--muted)}
.hero-sub strong{color:var(--text);font-weight:700}

.days-pill{
  display:inline-block;margin-top:14px;padding:8px 18px;border-radius:999px;
  background:rgba(255,255,255,.08);font-size:14px;font-weight:600
}
.days-pill.warn{background:rgba(245,158,11,.15);color:#fcd34d}
.days-pill.danger{background:rgba(239,68,68,.15);color:#fca5a5}

/* ---- メインCTA ---- */
.pay-card{
  background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:22px;display:flex;flex-direction:column;gap:14px
}
.pay-head{display:flex;justify-content:space-between;align-items:center}
.pay-plan{font-size:13px;color:var(--muted)}
.pay-price{font-size:28px;font-weight:800}
.pay-price span{font-size:13px;color:var(--muted);font-weight:400;margin-left:4px}

.btn{
  cursor:pointer;font-size:15px;padding:13px 20px;border-radius:12px;
  border:1px solid #334155;background:#111827;color:var(--text);
  font-weight:600;transition:.15s;text-decoration:none;display:inline-flex;
  align-items:center;justify-content:center;gap:8px
}
.btn:hover{transform:translateY(-1px)}
.btn.primary{background:linear-gradient(135deg,#0ea5e9 0%,#0284c7 100%);border-color:#0ea5e9;color:#fff}
.btn.purple{background:linear-gradient(135deg,#a855f7 0%,#7c3aed 100%);border-color:#a855f7;color:#fff}
.btn.bank{background:#0f3a26;border-color:#1e7a4a;color:#86efac}
.btn.full{width:100%}
.btn.sm{padding:8px 14px;font-size:13px}
.btn.ghost{background:transparent;border-color:var(--line);color:var(--muted)}
.btn.ghost:hover{color:var(--text);border-color:var(--accent)}

/* ---- 折りたたみ管理メニュー ---- */
details{
  background:var(--card);border:1px solid var(--line);border-radius:14px;
  overflow:hidden
}
details summary{
  cursor:pointer;padding:14px 18px;font-size:14px;font-weight:600;
  display:flex;justify-content:space-between;align-items:center;list-style:none;
  user-select:none
}
details summary::after{content:'▼';color:var(--muted);font-size:11px;transition:.2s}
details[open] summary::after{transform:rotate(180deg)}
details summary:hover{background:#1a2438}
details > div{padding:0 18px 18px}

.section-help{font-size:12px;color:var(--muted);margin-bottom:12px;line-height:1.6}
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:10px}
.field label{font-size:12px;color:var(--muted)}
.field input,.field select{
  font-size:14px;padding:10px 12px;border-radius:8px;
  border:1px solid #263244;background:#0a1220;color:var(--text)
}
.field input:focus{outline:none;border-color:var(--accent)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}

.preset-btns{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
.preset-btn{
  cursor:pointer;font-size:12px;padding:6px 12px;border-radius:6px;
  border:1px solid var(--line);background:#0a1220;color:var(--muted)
}
.preset-btn:hover{border-color:var(--accent);color:var(--text)}
.preset-btn.on{background:var(--accent);color:#001018;border-color:var(--accent);font-weight:700}

/* ---- アラート ---- */
.alert{padding:14px 16px;border-radius:12px;font-size:14px;display:flex;gap:10px;align-items:flex-start}
.alert.success{background:#0f2615;border:1px solid #22c55e;color:#86efac}
.alert.error{background:#1a0e12;border:1px solid #ef4444;color:#fca5a5}
.alert.info{background:#0c1d2e;border:1px solid #0ea5e9;color:#7dd3fc}

@media(max-width:600px){
  .grid2{grid-template-columns:1fr}
  .container{padding:14px 10px;gap:14px}
  .hero{padding:22px 18px}
  .hero-title{font-size:26px}
  .hero-icon{font-size:40px}
  .pay-head{flex-direction:column;align-items:flex-start;gap:6px}
  .pay-price{font-size:24px}
}
</style></head><body>
<header>
  <h1>💎 サブスクリプション</h1>
  <div class="nav">
    <a href="/ui">← フロア</a>
    <a href="/ui/pricing">料金</a>
    <a href="/ui/salary">給与</a>
  </div>
</header>

<div class="container">
  <!-- 通知エリア -->
  <div id="alertBox" style="display:none"></div>

  <!-- 店舗切替（複数店舗対応） -->
  <div id="storeSwitch" class="row" style="display:none;justify-content:flex-end">
    <span style="font-size:12px;color:var(--muted)">店舗:</span>
    <input id="storeId" type="number" value="1" style="width:60px;padding:4px 8px;border-radius:6px;border:1px solid var(--line);background:#0a1220;color:var(--text);font-size:13px">
    <button class="btn ghost sm" onclick="loadAll()">↻</button>
  </div>

  <!-- ヒーロー: 状況を一目で -->
  <div class="hero" id="heroBox">
    <div class="hero-status" id="heroStatus">読み込み中...</div>
    <div class="hero-icon" id="heroIcon">⏳</div>
    <div class="hero-title gray" id="heroTitle">確認中</div>
    <div class="hero-sub" id="heroSub"></div>
    <div id="daysPillBox"></div>
  </div>

  <!-- 支払いカード -->
  <div class="pay-card">
    <div class="pay-head">
      <div>
        <div class="pay-plan">スタンダードプラン（月額）</div>
        <div class="pay-price">¥50,000<span>/月（税込）</span></div>
      </div>
    </div>
    <button class="btn primary full" onclick="goPay()">
      💳 クレジットカードで支払う
    </button>
    <button class="btn bank full" onclick="showBankInfo()">
      🏦 銀行振込で支払う
    </button>
    <div style="font-size:11px;color:var(--muted);text-align:center">
      決済はStripeにより安全に処理されます
    </div>
  </div>

  <!-- 管理者メニュー（折りたたみ） -->
  <details>
    <summary>🔧 管理者メニュー</summary>
    <div>
      <div class="section-help">
        振込確認後の有効化や、期限の手動延長を行えます。
      </div>

      <!-- 期限延長 -->
      <div style="border-top:1px solid var(--line);padding-top:14px;margin-top:8px">
        <div style="font-size:13px;font-weight:600;margin-bottom:10px">📅 期限を延長する</div>

        <div class="preset-btns">
          <button class="preset-btn" onclick="addPeriod(1)">+ 1ヶ月</button>
          <button class="preset-btn" onclick="addPeriod(3)">+ 3ヶ月</button>
          <button class="preset-btn" onclick="addPeriod(6)">+ 6ヶ月</button>
          <button class="preset-btn" onclick="addPeriod(12)">+ 1年</button>
        </div>

        <div class="grid2">
          <div class="field">
            <label>有効期限（この日まで）</label>
            <input id="ma_period" type="date">
          </div>
          <div class="field">
            <label>支払い方法</label>
            <select id="ma_method">
              <option value="bank">口座振込</option>
              <option value="card">クレジットカード</option>
              <option value="manual">手動（その他）</option>
            </select>
          </div>
        </div>
        <button class="btn primary" onclick="manualActivate()" style="margin-top:6px">
          この内容で有効化
        </button>
      </div>

      <!-- リンク集 -->
      <div style="border-top:1px solid var(--line);padding-top:14px;margin-top:18px;display:flex;flex-direction:column;gap:8px">
        <a class="btn ghost sm" href="/ui/admin/signups">📋 振込申し込み一覧</a>
        <a class="btn ghost sm" id="portalBtn" href="#" onclick="event.preventDefault();openPortal()">⚙️ Stripe顧客ポータル（プラン変更・解約）</a>
      </div>
    </div>
  </details>

  <!-- 振込先情報モーダル -->
  <div id="bankModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:100;align-items:center;justify-content:center;padding:20px">
    <div style="background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px;max-width:420px;width:100%">
      <h3 style="margin:0 0 16px;font-size:17px">🏦 銀行振込のご案内</h3>
      <div style="font-size:14px;line-height:1.8;background:#0a1423;border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:14px">
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">お振込先</div>
        <div>GMOあおぞらネット銀行 うみ支店</div>
        <div>普通 3234569</div>
        <div>カ）ポススタート</div>
        <div style="margin-top:10px;font-size:11px;color:var(--muted)">月額</div>
        <div style="font-weight:700">¥50,000（税込）</div>
      </div>
      <div style="font-size:12px;color:var(--muted);margin-bottom:16px">
        お振込確認後、こちらで「期限延長」より有効化させていただきます。
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn" onclick="closeModal()">閉じる</button>
      </div>
    </div>
  </div>
</div>

<script>
const $ = id=>document.getElementById(id);
const params = new URLSearchParams(location.search);

// 戻ってきた時の通知
if(params.get('checkout')==='success' || params.get('success')==='1'){
  showAlert('success','✅ お支払いが完了しました！ 反映まで少々お待ちください。');
}
if(params.get('checkout')==='canceled' || params.get('canceled')==='1'){
  showAlert('info','支払いがキャンセルされました。');
}

function showAlert(type, msg){
  const a=$('alertBox');
  a.style.display='';
  a.innerHTML=`<div class="alert ${type}"><div>${msg}</div></div>`;
  setTimeout(()=>{a.style.display='none'}, 8000);
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

// ---- ヒーロー描画 ----
function renderHero(sub){
  const status = sub.status || 'inactive';
  const hero = $('heroBox');
  const icon = $('heroIcon');
  const title = $('heroTitle');
  const sub_el = $('heroSub');
  const stat = $('heroStatus');
  const pillBox = $('daysPillBox');

  // 残り日数計算
  let daysLeft = null;
  if(sub.current_period_end){
    const end = new Date(sub.current_period_end);
    const now = new Date();
    daysLeft = Math.ceil((end - now) / (1000*60*60*24));
  }

  // ステータスごとの表示
  hero.className = 'hero';
  title.className = 'hero-title';
  pillBox.innerHTML = '';

  if(status === 'active' && daysLeft !== null && daysLeft > 7){
    hero.classList.add('active');
    icon.textContent = '✅';
    title.textContent = 'ご利用中';
    title.classList.add('green');
    stat.textContent = 'STATUS';
    sub_el.innerHTML = `次回更新: <strong>${formatDate(sub.current_period_end)}</strong>${sub.cancel_at_end?'（解約予定）':''}`;
    pillBox.innerHTML = `<div class="days-pill">あと ${daysLeft} 日</div>`;
  } else if(status === 'active' && daysLeft !== null && daysLeft <= 7 && daysLeft >= 0){
    hero.classList.add('warning');
    icon.textContent = '⚠️';
    title.textContent = 'まもなく更新';
    title.classList.add('amber');
    stat.textContent = '更新間近';
    sub_el.innerHTML = `次回更新: <strong>${formatDate(sub.current_period_end)}</strong>`;
    pillBox.innerHTML = `<div class="days-pill warn">あと ${daysLeft} 日</div>`;
  } else if(status === 'active' || status === 'trialing'){
    hero.classList.add('active');
    icon.textContent = '✅';
    title.textContent = 'ご利用中';
    title.classList.add('green');
    stat.textContent = 'STATUS';
    sub_el.textContent = sub.plan_name || '';
  } else if(status === 'past_due'){
    hero.classList.add('danger');
    icon.textContent = '⚠️';
    title.textContent = '支払い遅延';
    title.classList.add('red');
    stat.textContent = '要確認';
    sub_el.textContent = 'お支払い情報をご確認ください';
  } else if(status === 'canceled'){
    hero.classList.add('danger');
    icon.textContent = '🚫';
    title.textContent = '解約済み';
    title.classList.add('red');
    stat.textContent = 'STATUS';
    sub_el.textContent = 'ご利用を再開するには、再度お申し込みください';
  } else {
    icon.textContent = '🆕';
    title.textContent = '未加入';
    title.classList.add('gray');
    stat.textContent = 'STATUS';
    sub_el.textContent = '下のボタンからお申し込みいただけます';
  }

  // ポータルボタン表示
  $('portalBtn').style.display = (status === 'active' || status === 'past_due') ? '' : 'none';
}

function formatDate(iso){
  if(!iso) return '-';
  const d = new Date(iso);
  return `${d.getFullYear()}年${d.getMonth()+1}月${d.getDate()}日`;
}

// ---- データ読み込み ----
async function loadAll(){
  const s = $('storeId').value || '1';
  // デフォルト期限: 1ヶ月後
  if(!$('ma_period').value){
    const d = new Date(); d.setMonth(d.getMonth()+1);
    $('ma_period').value = d.toISOString().slice(0,10);
  }
  try{
    const sub = await api(`/subscription/${s}`);
    renderHero(sub);
  }catch(e){
    renderHero({status:'inactive'});
  }
}

// ---- 期限プリセット ----
function addPeriod(months){
  const d = new Date();
  d.setMonth(d.getMonth() + months);
  $('ma_period').value = d.toISOString().slice(0,10);
}

// ---- 支払い ----
function goPay(){
  // Payment Link経由（コードで設定済みの固定URL）
  // /signup/stripe を経由してDB登録 + Payment LinkにリダイレクトでもOK
  // ここでは管理画面なので /subscription/payment-link を使う
  fetch('/subscription/payment-link', {
    method:'POST',
    headers:{'Content-Type':'application/json','X-Role':'owner','X-Token':sessionStorage.getItem('pos_token')||''},
    body: JSON.stringify({store_id: parseInt($('storeId').value||'1')})
  }).then(r=>r.json()).then(data=>{
    if(data.checkout_url) window.location.href=data.checkout_url;
    else alert('決済URLを取得できませんでした');
  }).catch(e=>alert('エラー: '+e.message));
}

function showBankInfo(){
  $('bankModal').style.display='flex';
}
function closeModal(){
  $('bankModal').style.display='none';
}

// ---- 管理者: 手動有効化 ----
async function manualActivate(){
  const store_id = parseInt($('storeId').value||'1');
  const period_end = $('ma_period').value;
  const payment_method = $('ma_method').value;
  if(!period_end){alert('有効期限を入力してください');return;}
  if(!confirm(`${formatDate(period_end)} まで有効化します。よろしいですか？`)) return;
  try{
    await api('/subscription/manual-activate',{method:'POST',body:{
      store_id,period_end,payment_method,plan_name:'monthly_'+payment_method
    }});
    showAlert('success','✅ 有効化しました');
    loadAll();
  }catch(e){alert('エラー: '+e.message)}
}

async function openPortal(){
  const s = $('storeId').value || '1';
  try{
    const res = await api(`/subscription/portal?store_id=${s}`,{method:'POST'});
    if(res.portal_url) window.open(res.portal_url,'_blank');
  }catch(e){alert('Stripe顧客ポータルを開けませんでした: '+e.message)}
}

loadAll();
</script>
</body></html>
""")

# ─────────────────────────── 振込申し込み一覧 UI ───────────────────────────

@router.get("/ui/admin/signups", response_class=HTMLResponse)
def ui_admin_signups():
    return HTMLResponse(r"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>振込申し込み管理 - POS Start</title>
<style>
:root{--bg:#0b1220;--card:#0f172a;--line:#1f2937;--text:#e5e7eb;--muted:#94a3b8;--accent:#0ea5e9;--green:#22c55e;--red:#ef4444}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text)}
header{position:sticky;top:0;z-index:40;display:flex;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);background:rgba(11,18,32,.95)}
header h1{margin:0;font-size:17px}
.nav a{color:var(--accent);text-decoration:none;font-size:14px;padding:6px 10px;border-radius:8px;border:1px solid var(--line)}
.container{max-width:1100px;margin:0 auto;padding:20px 16px}
.tabs{display:flex;gap:8px;margin-bottom:16px}
.tab{padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:#111827;color:var(--muted);cursor:pointer;font-size:13px}
.tab.active{background:var(--accent);color:#001018;font-weight:700;border-color:var(--accent)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:13px}
th,td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:#111827;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
tr:last-child td{border-bottom:none}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700}
.b-pending{background:#78350f;color:#fcd34d}
.b-paid{background:#14532d;color:#86efac}
.b-canceled{background:#7f1d1d;color:#fca5a5}
.btn{cursor:pointer;font-size:12px;padding:6px 12px;border-radius:6px;border:1px solid #334155;background:#111827;color:var(--text)}
.btn.solid{background:var(--accent);border-color:var(--accent);color:#001018;font-weight:700}
.empty{text-align:center;padding:40px;color:var(--muted)}
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:100;align-items:center;justify-content:center}
.modal.show{display:flex}
.modal-card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px;max-width:440px;width:90%}
.modal-card h3{margin:0 0 14px;font-size:16px}
.modal-card label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px;margin-top:10px}
.modal-card input{width:100%;padding:8px 10px;border-radius:8px;border:1px solid #263244;background:#0a1220;color:var(--text);font-size:14px}
.modal-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}
</style></head><body>
<header>
  <h1>📋 振込申し込み管理</h1>
  <div class="nav" style="margin-left:auto;display:flex;gap:8px">
    <a href="/ui/subscription">← サブスク管理</a>
    <a href="/ui">フロア</a>
  </div>
</header>

<div class="container">
  <div class="tabs">
    <button class="tab active" data-status="pending" onclick="switchTab('pending')">入金待ち</button>
    <button class="tab" data-status="paid" onclick="switchTab('paid')">入金確認済</button>
    <button class="tab" data-status="" onclick="switchTab('')">すべて</button>
  </div>

  <div id="tableWrap"></div>
</div>

<div class="modal" id="actModal">
  <div class="modal-card">
    <h3>有効化する</h3>
    <div style="font-size:13px;color:var(--muted);margin-bottom:8px" id="actSubject"></div>
    <label>店舗ID（新規発行 or 既存ID）<input id="actStore" type="number" value="1"></label>
    <label>次回更新日<input id="actPeriod" type="date"></label>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">キャンセル</button>
      <button class="btn solid" onclick="doActivate()">有効化</button>
    </div>
  </div>
</div>

<script>
const $=id=>document.getElementById(id);
let currentStatus='pending';
let currentSignupId=null;

async function api(path,opt={}){
  const tk=sessionStorage.getItem('pos_token')||'';
  const o={method:'GET',headers:{'Content-Type':'application/json','X-Role':'owner','X-Token':tk},...opt};
  if(o.body&&typeof o.body!=='string') o.body=JSON.stringify(o.body);
  const r=await fetch(path,o);
  if(r.status===401){sessionStorage.clear();window.location.href='/';return;}
  if(r.status===402){window.location.href='/ui/subscription';return;}
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}

function switchTab(s){
  currentStatus=s;
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active', t.dataset.status===s));
  load();
}

async function load(){
  const url='/admin/signups'+(currentStatus?`?status=${currentStatus}`:'');
  try{
    const rows=await api(url);
    const wrap=$('tableWrap');
    if(!rows||!rows.length){
      wrap.innerHTML='<div class="empty">該当する申し込みはありません</div>';
      return;
    }
    const labels={pending:'入金待ち',paid:'入金確認済',canceled:'キャンセル'};
    wrap.innerHTML=`<table>
      <thead><tr><th>受付日時</th><th>状態</th><th>店舗名</th><th>担当者</th><th>連絡先</th><th>店舗ID</th><th>有効期限</th><th></th></tr></thead>
      <tbody>${rows.map(r=>`
        <tr>
          <td>${r.created_at?new Date(r.created_at).toLocaleString('ja-JP'):'—'}</td>
          <td><span class="badge b-${r.status}">${labels[r.status]||r.status}</span></td>
          <td>${escapeHtml(r.shop_name)}</td>
          <td>${escapeHtml(r.contact_name)}</td>
          <td style="font-size:11px">${escapeHtml(r.contact_email)}<br>${escapeHtml(r.contact_phone||'')}</td>
          <td>${r.store_id||'—'}</td>
          <td>${r.period_end?new Date(r.period_end).toLocaleDateString('ja-JP'):'—'}</td>
          <td>${r.status==='pending'?`<button class="btn solid" onclick="openModal(${r.id},'${escapeHtml(r.shop_name)}')">有効化</button>`:''}</td>
        </tr>`).join('')}
      </tbody></table>`;
  }catch(e){
    $('tableWrap').innerHTML=`<div class="empty">読み込みエラー: ${e.message}</div>`;
  }
}

function escapeHtml(s){return (s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

function openModal(id, name){
  currentSignupId=id;
  $('actSubject').textContent=name+' の入金が確認できたら、店舗IDと次回更新日を入力してください';
  if(!$('actPeriod').value){
    const d=new Date(); d.setMonth(d.getMonth()+1);
    $('actPeriod').value=d.toISOString().slice(0,10);
  }
  $('actModal').classList.add('show');
}

function closeModal(){$('actModal').classList.remove('show');}

async function doActivate(){
  const store_id=parseInt($('actStore').value||'0');
  const period_end=$('actPeriod').value;
  if(!store_id||!period_end){alert('店舗IDと次回更新日は必須です');return;}
  try{
    await api(`/admin/signups/${currentSignupId}/activate`,{method:'POST',body:{store_id,period_end,payment_method:'bank',plan_name:'monthly_bank'}});
    closeModal();
    alert('✅ 有効化しました');
    load();
  }catch(e){alert('エラー: '+e.message)}
}

load();
</script>
</body></html>""")
