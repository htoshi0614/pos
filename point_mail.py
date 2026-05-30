"""point_mail.py — ポイントメール（締め後に自動送信）
本締め完了後、キャスト別売上・指名数・ドリンクバック等をメールで一斉送信
"""

import smtplib, os, threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text

from db_shared import Base, SessionLocal, require_role

router = APIRouter(tags=["point_mail"])
ADMIN_ROLES = ["owner", "manager"]

# ---------- Models ----------
class MailConfig(Base):
    __tablename__ = "mail_configs"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, unique=True)
    smtp_host = Column(String, default="smtp.gmail.com")
    smtp_port = Column(Integer, default=587)
    smtp_user = Column(String, default="")
    smtp_password = Column(String, default="")  # 本番では暗号化推奨
    from_name = Column(String, default="POS Start")
    from_email = Column(String, default="")
    enabled = Column(Boolean, default=True)

class MailRecipient(Base):
    __tablename__ = "mail_recipients"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, index=True)
    email = Column(String)
    name = Column(String, default="")
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class MailLog(Base):
    __tablename__ = "mail_logs"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, index=True)
    business_date = Column(String, default="")
    recipients = Column(Text, default="")
    status = Column(String, default="")  # sent / failed
    error = Column(Text, default="")
    sent_at = Column(DateTime, default=datetime.utcnow)

# ---------- Schemas ----------
class MailConfigIn(BaseModel):
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_name: str = "POS Start"
    from_email: str = ""
    enabled: bool = True

class RecipientIn(BaseModel):
    email: str
    name: str = ""

# ---------- メール本文生成 ----------
def _build_report_html(report: dict, business_date: str) -> str:
    """Zレポートからメール本文HTMLを生成"""
    total = report.get("total_sales", 0)
    guests = report.get("guest_count", 0)
    sessions = report.get("session_count", 0)
    by_cast = report.get("by_cast", {})
    by_item = report.get("by_item", {})
    by_payment = report.get("by_payment", {})

    # キャスト別テーブル
    cast_rows = ""
    for name, d in sorted(by_cast.items(), key=lambda x: x[1].get("total", 0), reverse=True):
        nomi = d.get("nomi_count", 0)
        sales = d.get("total", 0)
        cast_rows += f"""<tr>
            <td style="padding:8px 12px;border-bottom:1px solid #eee">{name}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">{nomi}件</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">¥{int(sales):,}</td>
        </tr>"""

    if not cast_rows:
        cast_rows = '<tr><td colspan="3" style="padding:8px 12px;color:#999">データなし</td></tr>'

    # 支払方法
    payment_rows = ""
    for method, amount in by_payment.items():
        label = {"cash": "現金", "card": "カード", "qr": "QR"}.get(method, method)
        payment_rows += f"""<tr>
            <td style="padding:6px 12px;border-bottom:1px solid #eee">{label}</td>
            <td style="padding:6px 12px;border-bottom:1px solid #eee;text-align:right">¥{int(amount):,}</td>
        </tr>"""

    # 商品トップ10
    item_sorted = sorted(by_item.items(), key=lambda x: x[1].get("amount", 0), reverse=True)[:10]
    item_rows = ""
    for iname, d in item_sorted:
        item_rows += f"""<tr>
            <td style="padding:6px 12px;border-bottom:1px solid #eee">{iname}</td>
            <td style="padding:6px 12px;border-bottom:1px solid #eee;text-align:right">{d['qty']}個</td>
            <td style="padding:6px 12px;border-bottom:1px solid #eee;text-align:right">¥{int(d['amount']):,}</td>
        </tr>"""

    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;color:#333">
        <div style="background:#0b1220;color:#fff;padding:20px;border-radius:12px 12px 0 0;text-align:center">
            <h1 style="margin:0;font-size:20px">🍸 POS Start デイリーレポート</h1>
            <p style="margin:8px 0 0;color:#94a3b8;font-size:14px">{business_date}</p>
        </div>

        <div style="background:#f8fafc;padding:20px;border:1px solid #e2e8f0">
            <div style="display:flex;gap:16px;text-align:center">
                <div style="flex:1;background:#fff;border-radius:8px;padding:12px;border:1px solid #e2e8f0">
                    <div style="font-size:12px;color:#94a3b8">売上</div>
                    <div style="font-size:24px;font-weight:800;color:#0ea5e9">¥{int(total):,}</div>
                </div>
                <div style="flex:1;background:#fff;border-radius:8px;padding:12px;border:1px solid #e2e8f0">
                    <div style="font-size:12px;color:#94a3b8">来客数</div>
                    <div style="font-size:24px;font-weight:800">{guests}名</div>
                </div>
                <div style="flex:1;background:#fff;border-radius:8px;padding:12px;border:1px solid #e2e8f0">
                    <div style="font-size:12px;color:#94a3b8">組数</div>
                    <div style="font-size:24px;font-weight:800">{sessions}組</div>
                </div>
            </div>
        </div>

        <div style="background:#fff;padding:20px;border:1px solid #e2e8f0;border-top:0">
            <h2 style="font-size:15px;margin:0 0 12px;color:#0b1220">👑 キャスト別成績</h2>
            <table style="width:100%;border-collapse:collapse;font-size:14px">
                <tr style="background:#f1f5f9">
                    <th style="padding:8px 12px;text-align:left">キャスト名</th>
                    <th style="padding:8px 12px;text-align:right">指名数</th>
                    <th style="padding:8px 12px;text-align:right">売上</th>
                </tr>
                {cast_rows}
            </table>
        </div>

        <div style="background:#fff;padding:20px;border:1px solid #e2e8f0;border-top:0">
            <h2 style="font-size:15px;margin:0 0 12px;color:#0b1220">💰 支払方法別</h2>
            <table style="width:100%;border-collapse:collapse;font-size:14px">
                {payment_rows}
            </table>
        </div>

        <div style="background:#fff;padding:20px;border:1px solid #e2e8f0;border-top:0">
            <h2 style="font-size:15px;margin:0 0 12px;color:#0b1220">🍹 売れ筋商品 TOP10</h2>
            <table style="width:100%;border-collapse:collapse;font-size:14px">
                <tr style="background:#f1f5f9">
                    <th style="padding:6px 12px;text-align:left">商品名</th>
                    <th style="padding:6px 12px;text-align:right">数量</th>
                    <th style="padding:6px 12px;text-align:right">金額</th>
                </tr>
                {item_rows}
            </table>
        </div>

        <div style="background:#f1f5f9;padding:16px;border-radius:0 0 12px 12px;text-align:center;font-size:11px;color:#94a3b8;border:1px solid #e2e8f0;border-top:0">
            POS Start — 自動送信メール（配信停止は管理画面から）
        </div>
    </div>
    """
    return html


def _send_emails(store_id: int, business_date: str, report: dict):
    """メール送信（バックグラウンド実行）"""
    db = SessionLocal()
    try:
        cfg = db.query(MailConfig).filter_by(store_id=store_id).first()
        if not cfg or not cfg.enabled or not cfg.smtp_user:
            return

        recipients = db.query(MailRecipient).filter_by(store_id=store_id, active=True).all()
        if not recipients:
            return

        to_list = [r.email for r in recipients if r.email]
        if not to_list:
            return

        html_body = _build_report_html(report, business_date)
        subject = f"【POS Start】デイリーレポート {business_date}"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{cfg.from_name} <{cfg.from_email or cfg.smtp_user}>"
        msg["To"] = ", ".join(to_list)
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
                server.starttls()
                server.login(cfg.smtp_user, cfg.smtp_password)
                server.sendmail(cfg.from_email or cfg.smtp_user, to_list, msg.as_string())

            log = MailLog(store_id=store_id, business_date=business_date,
                          recipients=", ".join(to_list), status="sent")
            db.add(log)
            db.commit()
            print(f"[point_mail] Sent to {len(to_list)} recipients for {business_date}")
        except Exception as e:
            log = MailLog(store_id=store_id, business_date=business_date,
                          recipients=", ".join(to_list), status="failed", error=str(e))
            db.add(log)
            db.commit()
            print(f"[point_mail] Send failed: {e}")
    finally:
        db.close()


def trigger_point_mail(store_id: int, business_date: str, report: dict):
    """本締め完了後に呼ばれる。バックグラウンドでメール送信"""
    t = threading.Thread(target=_send_emails, args=(store_id, business_date, report), daemon=True)
    t.start()


# ---------- API ----------
@router.get("/mail-config/{store_id}")
def get_mail_config(store_id: int, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        cfg = db.query(MailConfig).filter_by(store_id=store_id).first()
        if not cfg:
            return {"smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_user": "",
                    "from_name": "POS Start", "from_email": "", "enabled": True}
        return {"smtp_host": cfg.smtp_host, "smtp_port": cfg.smtp_port,
                "smtp_user": cfg.smtp_user, "from_name": cfg.from_name,
                "from_email": cfg.from_email, "enabled": cfg.enabled}
    finally:
        db.close()


@router.post("/mail-config/{store_id}")
def save_mail_config(store_id: int, payload: MailConfigIn,
                     x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        cfg = db.query(MailConfig).filter_by(store_id=store_id).first()
        if not cfg:
            cfg = MailConfig(store_id=store_id)
            db.add(cfg)
        cfg.smtp_host = payload.smtp_host
        cfg.smtp_port = payload.smtp_port
        if payload.smtp_user:
            cfg.smtp_user = payload.smtp_user
        if payload.smtp_password:
            cfg.smtp_password = payload.smtp_password
        cfg.from_name = payload.from_name
        cfg.from_email = payload.from_email
        cfg.enabled = payload.enabled
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.get("/mail-recipients/{store_id}")
def list_recipients(store_id: int, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        rows = db.query(MailRecipient).filter_by(store_id=store_id).order_by(MailRecipient.id).all()
        return [{"id": r.id, "email": r.email, "name": r.name, "active": r.active} for r in rows]
    finally:
        db.close()


@router.post("/mail-recipients/{store_id}")
def add_recipient(store_id: int, payload: RecipientIn,
                  x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        r = MailRecipient(store_id=store_id, email=payload.email, name=payload.name)
        db.add(r)
        db.commit()
        db.refresh(r)
        return {"ok": True, "id": r.id}
    finally:
        db.close()


@router.delete("/mail-recipients/{recipient_id}")
def delete_recipient(recipient_id: int, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        r = db.get(MailRecipient, recipient_id)
        if not r:
            raise HTTPException(404, "Not found")
        db.delete(r)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.get("/mail-logs/{store_id}")
def list_mail_logs(store_id: int, limit: int = 20,
                   x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        rows = db.query(MailLog).filter_by(store_id=store_id).order_by(MailLog.id.desc()).limit(limit).all()
        return [{"id": r.id, "business_date": r.business_date, "recipients": r.recipients,
                 "status": r.status, "error": r.error,
                 "sent_at": r.sent_at.isoformat() if r.sent_at else ""} for r in rows]
    finally:
        db.close()


@router.post("/mail/test-send/{store_id}")
def test_send(store_id: int, x_role: Optional[str] = Header(None, alias="X-Role")):
    """テスト送信（ダミーレポートで送信確認）"""
    require_role(x_role, ADMIN_ROLES)
    test_report = {
        "total_sales": 150000, "guest_count": 12, "session_count": 5,
        "by_cast": {"テスト花子": {"nomi_count": 3, "total": 45000},
                    "テスト美咲": {"nomi_count": 1, "total": 22000}},
        "by_payment": {"cash": 80000, "card": 50000, "qr": 20000},
        "by_item": {"生ビール": {"category": "drink", "qty": 15, "amount": 12000},
                    "シャンパン": {"category": "bottle", "qty": 2, "amount": 30000}},
    }
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        _send_emails(store_id, f"{today}（テスト）", test_report)
        return {"ok": True, "message": "テスト送信を実行しました"}
    except Exception as e:
        raise HTTPException(500, f"送信エラー: {e}")


# ---------- UI ----------
@router.get("/ui/mail", response_class=HTMLResponse)
def ui_mail():
    return HTMLResponse(r"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ポイントメール設定</title>
<style>
:root{--bg:#fafafa;--card:#ffffff;--card2:#ffffff;--line:#eaeaef;--border:#eaeaef;--text:#0a0a0f;--ink:#0a0a0f;--muted:#8a8a95;--body:#4a4a55;--accent:#d64583;--accent-soft:#fdf0f7;--accent-dark:#b03468;--gold:#c9a96e;--gold-soft:#faf3e3;--warn:#f59e0b;--amber:#f59e0b;--err:#ef4444;--red:#ef4444;--ok:#22c55e;--green:#22c55e;--blue:#3b82f6;--purple:#a855f7;}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text);padding:20px}
.container{max-width:640px;margin:0 auto}
h1{font-size:20px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;margin-bottom:16px}
.card h2{margin:0 0 14px;font-size:15px;border-bottom:1px solid var(--line);padding-bottom:10px}
label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--muted);margin-bottom:10px}
input,select{font-size:14px;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:#0a1423;color:var(--text)}
.btn{cursor:pointer;font-size:14px;padding:8px 16px;border-radius:8px;border:1px solid var(--line);background:#111827;color:var(--text)}
.btn.solid{background:var(--accent);border-color:var(--accent);color:#001018;font-weight:700}
.btn.green{background:#14532d;border-color:var(--green);color:#4ade80}
.btn.danger{background:#7f1d1d;border-color:var(--err);color:#fca5a5;font-size:12px;padding:4px 10px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left}
th{background:#111827;font-size:12px;color:var(--muted)}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px}
.badge.sent{background:#14532d;color:#4ade80}
.badge.failed{background:#7f1d1d;color:#fca5a5}
a{color:var(--accent)}
.note{font-size:12px;color:var(--muted);line-height:1.6}
.preset-btn{cursor:pointer;font-size:13px;padding:10px 16px;border-radius:10px;border:2px solid var(--line);background:#111827;color:var(--text);text-align:center;transition:all .15s}
.preset-btn:hover{border-color:var(--accent);background:#0c1a2e}
.preset-btn.active{border-color:var(--accent);background:#0c1a2e}
.preset-btn .icon{font-size:24px;display:block;margin-bottom:4px}
.step{display:flex;align-items:center;gap:10px;margin-bottom:16px}
.step-num{width:28px;height:28px;border-radius:50%;background:var(--accent);color:#001018;font-weight:800;font-size:14px;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.step-label{font-size:14px;font-weight:600}
.status-bar{display:flex;gap:12px;padding:12px 16px;border-radius:10px;font-size:13px;margin-bottom:16px}
.status-bar.ok{background:#14532d33;border:1px solid #22c55e44;color:#4ade80}
.status-bar.ng{background:#7f1d1d33;border:1px solid #ef444444;color:#fca5a5}
.advanced-toggle{cursor:pointer;font-size:12px;color:var(--muted);background:none;border:none;text-decoration:underline;padding:0;margin-top:8px}
.advanced-toggle:hover{color:var(--text)}
.hidden{display:none}
.recipient-item{display:flex;align-items:center;gap:8px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:8px;background:#0a1423}
.recipient-item .email{flex:1;font-size:14px}
.recipient-item .name{font-size:12px;color:var(--muted)}

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
.badge.sent,.badge.success,.badge.ok{background:#f0fdf4 !important;color:#15803d !important;border:1px solid #86efac !important}
.badge.failed,.badge.error,.badge.ng{background:#fef2f2 !important;color:#b91c1c !important;border:1px solid #fca5a5 !important}
.status-bar{border-radius:10px}
.status-bar.ok{background:#f0fdf4 !important;border:1px solid #86efac !important;color:#15803d !important}
.status-bar.ng,.status-bar.err,.status-bar.warning{background:#fef2f2 !important;border:1px solid #fca5a5 !important;color:#b91c1c !important}
.recipient-item{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.recipient-item .name{color:#8a8a95 !important}
.note,.help,.hint{color:#4a4a55 !important}
input,select,textarea{background:#ffffff !important;color:#0a0a0f !important;border:1px solid #eaeaef !important}
input:focus,select:focus,textarea:focus{border-color:#d64583 !important;box-shadow:0 0 0 3px #fdf0f7 !important}
.tab,.tab-btn{color:#8a8a95 !important}
.tab.active,.tab-btn.active{color:#d64583 !important;background:#fdf0f7 !important;border-color:#d64583 !important}
.tab-body{background:#ffffff !important;border-color:#eaeaef !important}
/* 残ダーク背景の inline / クラスを一掃 */
[style*="#0a1423"],[style*="#0a1624"],[style*="#0a1220"],[style*="#0c1a2e"],[style*="#0c2a3d"],[style*="#1a2438"],[style*="#1c1c2e"],[style*="#0c1d2e"]{background:#ffffff !important;color:#0a0a0f !important;border-color:#eaeaef !important}
[style*="#0ea5e9"]{color:#d64583 !important}

</style></head><body>
<div class="container">
<div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
  <h1>ポイントメール設定</h1>
  <a href="/ui" style="font-size:13px;margin-left:auto">POS に戻る</a>
</div>

<!-- ステータス -->
<div class="status-bar ng" id="statusBar">設定が必要です</div>

<!-- STEP 1: メールサービス選択 -->
<div class="card">
  <div class="step"><div class="step-num">1</div><div class="step-label">メールサービスを選択</div></div>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">
    <button class="preset-btn" onclick="selectPreset('gmail')" id="pre_gmail">
      <span class="icon">G</span>Gmail
    </button>
    <button class="preset-btn" onclick="selectPreset('yahoo')" id="pre_yahoo">
      <span class="icon">Y!</span>Yahoo
    </button>
    <button class="preset-btn" onclick="selectPreset('outlook')" id="pre_outlook">
      <span class="icon">O</span>Outlook
    </button>
  </div>
  <div style="margin-top:12px">
    <label>送信に使うメールアドレス<input id="smtpUser" placeholder="your@gmail.com"></label>
    <label id="passLabel">アプリパスワード（Gmailの通常パスワードではありません）<input id="smtpPass" type="password" placeholder="16桁の英字（例: abcd efgh ijkl mnop）"></label>
    <div class="note" id="presetNote" style="line-height:1.8"></div>
    <div id="helpBox" style="margin-top:8px;padding:12px;background:#0a1423;border:1px solid var(--line);border-radius:10px;font-size:12px;line-height:1.8;color:var(--muted)"></div>
  </div>
  <button class="advanced-toggle" onclick="toggleAdvanced()">詳細設定を表示</button>
  <div class="hidden" id="advancedSection" style="margin-top:12px">
    <div style="display:grid;grid-template-columns:2fr 1fr;gap:10px">
      <label>SMTPホスト<input id="smtpHost" value="smtp.gmail.com"></label>
      <label>ポート<input id="smtpPort" type="number" value="587"></label>
    </div>
    <label>送信者名<input id="fromName" value="POS Start"></label>
    <label>送信元メール（空欄=ログインと同じ）<input id="fromEmail" placeholder=""></label>
  </div>
  <div style="margin-top:14px;text-align:right">
    <button class="btn solid" onclick="saveConfig()">保存</button>
  </div>
</div>

<!-- STEP 2: 送信先 -->
<div class="card">
  <div class="step"><div class="step-num">2</div><div class="step-label">送信先を追加</div></div>
  <div class="note" style="margin-bottom:12px">本締め完了時に自動送信されます</div>
  <div class="row" style="margin-bottom:12px">
    <input id="newEmail" placeholder="メールアドレス" style="flex:1">
    <input id="newName" placeholder="名前（任意）" style="width:120px">
    <button class="btn solid" onclick="addRecipient()">追加</button>
  </div>
  <div id="recipientList"></div>
</div>

<!-- STEP 3: テスト -->
<div class="card">
  <div class="step"><div class="step-num">3</div><div class="step-label">テスト送信で確認</div></div>
  <div class="note" style="margin-bottom:12px">ダミーデータでテストメールを送ります</div>
  <button class="btn green" onclick="testSend()" style="width:100%;padding:12px">テストメールを送信</button>
</div>

<!-- 送信履歴（折りたたみ） -->
<div class="card">
  <button class="advanced-toggle" onclick="toggleLogs()" style="font-size:14px;font-weight:600;color:var(--text);text-decoration:none">送信履歴 ▼</button>
  <div class="hidden" id="logSection" style="margin-top:12px">
    <table>
      <thead><tr><th>営業日</th><th>送信先</th><th>状態</th><th>日時</th></tr></thead>
      <tbody id="logList"></tbody>
    </table>
  </div>
</div>
</div>

<script>
const storeId=1, H={'Content-Type':'application/json','X-Role':'owner','X-Token':sessionStorage.getItem('pos_token')||''};
const PRESETS={
  gmail:{
    host:'smtp.gmail.com',port:587,
    placeholder:'your@gmail.com',
    passLabel:'Gmailアプリパスワード（通常のGoogleパスワードでは送れません）',
    passPlaceholder:'16桁の英字（例: abcd efgh ijkl mnop）',
    note:'Gmailで送信するには「アプリパスワード」が必要です。',
    help:'<b>取得手順（1分）:</b><br>1. <a href="https://myaccount.google.com/apppasswords" target="_blank" style="color:#0ea5e9">Google アプリパスワード設定</a> を開く<br>2. Googleにログイン（2段階認証が必要）<br>3. アプリ名に「POS」と入力して「作成」をクリック<br>4. 表示された16桁のパスワードをコピーして上に貼り付け'
  },
  yahoo:{
    host:'smtp.mail.yahoo.co.jp',port:465,
    placeholder:'your@yahoo.co.jp',
    passLabel:'Yahoo!メールパスワード',
    passPlaceholder:'Yahoo!メールのパスワード',
    note:'Yahoo!メールのSMTP送信を有効にする必要があります。',
    help:'<b>設定手順:</b><br>1. <a href="https://mail.yahoo.co.jp/" target="_blank" style="color:#0ea5e9">Yahoo!メール</a> にログイン<br>2. 設定 > IMAP/POP/SMTPアクセス を開く<br>3.「Yahoo! JAPAN公式サービス以外からのアクセスも有効にする」をON<br>4. Yahoo!メールのパスワードを上に入力'
  },
  outlook:{
    host:'smtp.office365.com',port:587,
    placeholder:'your@outlook.com',
    passLabel:'Outlookパスワード',
    passPlaceholder:'Outlookアカウントのパスワード',
    note:'Outlookアカウントのパスワードで送信できます。',
    help:'<b>設定手順:</b><br>1. Outlookのメールアドレスを上に入力<br>2. Outlookアカウントのパスワードを入力<br>※ 2段階認証を有効にしている場合はアプリパスワードが必要です'
  }
};
let currentPreset='gmail';

function selectPreset(key){
  currentPreset=key;
  const p=PRESETS[key];
  document.getElementById('smtpHost').value=p.host;
  document.getElementById('smtpPort').value=p.port;
  document.getElementById('smtpUser').placeholder=p.placeholder;
  document.getElementById('passLabel').childNodes[0].textContent=p.passLabel;
  document.getElementById('smtpPass').placeholder=p.passPlaceholder;
  document.getElementById('presetNote').textContent=p.note;
  document.getElementById('helpBox').innerHTML=p.help;
  document.querySelectorAll('.preset-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('pre_'+key).classList.add('active');
}

function toggleAdvanced(){
  const s=document.getElementById('advancedSection');
  s.classList.toggle('hidden');
}
function toggleLogs(){
  const s=document.getElementById('logSection');
  s.classList.toggle('hidden');
  if(!s.classList.contains('hidden')) loadLogs();
}

async function api(path,opt={}){
  const o={method:'GET',headers:H,...opt};
  if(o.body&&typeof o.body!=='string') o.body=JSON.stringify(o.body);
  const r=await fetch(path,o);
  if(r.status===401){sessionStorage.clear();window.location.href='/';return;}
  if(!r.ok) throw new Error(await r.text());
  const ct=r.headers.get('content-type')||'';
  return ct.includes('json')?r.json():r.text();
}

function updateStatus(cfg,recipients){
  const bar=document.getElementById('statusBar');
  if(cfg.smtp_user && recipients.length>0){
    bar.className='status-bar ok';
    bar.textContent=`設定OK — ${recipients.length}件の送信先に本締め後に自動送信します`;
  } else if(!cfg.smtp_user){
    bar.className='status-bar ng';
    bar.textContent='メールアドレスとパスワードを設定してください';
  } else {
    bar.className='status-bar ng';
    bar.textContent='送信先を追加してください';
  }
}

let cachedCfg={}, cachedRecipients=[];

async function loadConfig(){
  try{
    const c=await api(`/mail-config/${storeId}`);
    cachedCfg=c;
    document.getElementById('smtpUser').value=c.smtp_user||'';
    document.getElementById('fromName').value=c.from_name||'POS Start';
    document.getElementById('fromEmail').value=c.from_email||'';
    // detect preset
    if(c.smtp_host){
      document.getElementById('smtpHost').value=c.smtp_host;
      document.getElementById('smtpPort').value=c.smtp_port||587;
      for(const [k,v] of Object.entries(PRESETS)){
        if(c.smtp_host===v.host) selectPreset(k);
      }
    } else { selectPreset('gmail'); }
    updateStatus(cachedCfg, cachedRecipients);
  }catch{}
}

async function saveConfig(){
  try{
    await api(`/mail-config/${storeId}`,{method:'POST',body:{
      smtp_host:document.getElementById('smtpHost').value,
      smtp_port:parseInt(document.getElementById('smtpPort').value),
      smtp_user:document.getElementById('smtpUser').value,
      smtp_password:document.getElementById('smtpPass').value,
      from_name:document.getElementById('fromName').value,
      from_email:document.getElementById('fromEmail').value,
      enabled:true
    }});
    cachedCfg.smtp_user=document.getElementById('smtpUser').value;
    updateStatus(cachedCfg, cachedRecipients);
    alert('保存しました');
  }catch(e){alert('エラー: '+e.message)}
}

async function loadRecipients(){
  try{
    const list=await api(`/mail-recipients/${storeId}`);
    cachedRecipients=list;
    const el=document.getElementById('recipientList');
    if(!list.length){
      el.innerHTML='<div class="note" style="text-align:center;padding:16px">送信先が登録されていません</div>';
    } else {
      el.innerHTML=list.map(r=>`<div class="recipient-item">
        <div style="flex:1"><div class="email">${r.email}</div>${r.name?`<div class="name">${r.name}</div>`:''}</div>
        <button class="btn danger" onclick="delRecipient(${r.id})">削除</button>
      </div>`).join('');
    }
    updateStatus(cachedCfg, cachedRecipients);
  }catch{}
}

async function addRecipient(){
  const email=document.getElementById('newEmail').value.trim();
  const name=document.getElementById('newName').value.trim();
  if(!email){alert('メールアドレスを入力してください');return;}
  try{
    await api(`/mail-recipients/${storeId}`,{method:'POST',body:{email,name}});
    document.getElementById('newEmail').value='';
    document.getElementById('newName').value='';
    loadRecipients();
  }catch(e){alert('エラー: '+e.message)}
}

async function delRecipient(id){
  if(!confirm('この送信先を削除しますか？'))return;
  try{
    await api(`/mail-recipients/${id}`,{method:'DELETE'});
    loadRecipients();
  }catch(e){alert('エラー: '+e.message)}
}

async function testSend(){
  if(!confirm('テストメールを送信しますか？'))return;
  try{
    const r=await api(`/mail/test-send/${storeId}`,{method:'POST'});
    alert(r.message||'テスト送信しました');
  }catch(e){alert('エラー: '+e.message)}
}

async function loadLogs(){
  try{
    const list=await api(`/mail-logs/${storeId}`);
    document.getElementById('logList').innerHTML=list.map(r=>`<tr>
      <td>${r.business_date}</td>
      <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis">${r.recipients}</td>
      <td><span class="badge ${r.status}">${r.status==='sent'?'送信済':'失敗'}</span></td>
      <td style="font-size:11px">${r.sent_at?new Date(r.sent_at).toLocaleString('ja-JP'):''}</td>
    </tr>`).join('')||'<tr><td colspan="4" style="color:var(--muted)">履歴なし</td></tr>';
  }catch{}
}

loadConfig(); loadRecipients();
</script></body></html>""")
