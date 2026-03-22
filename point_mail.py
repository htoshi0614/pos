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
:root{--bg:#0b1220;--card:#0f172a;--line:#1f2937;--text:#e5e7eb;--muted:#94a3b8;--accent:#0ea5e9;--green:#22c55e;--err:#ef4444}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text);padding:20px}
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
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left}
th{background:#111827;font-size:12px;color:var(--muted)}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px}
.badge.sent{background:#14532d;color:#4ade80}
.badge.failed{background:#7f1d1d;color:#fca5a5}
a{color:var(--accent)}
.note{font-size:12px;color:var(--muted);margin-top:6px;line-height:1.6}
</style></head><body>
<div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
  <h1>📧 ポイントメール設定</h1>
  <a href="/ui" style="font-size:13px;margin-left:auto">← POS に戻る</a>
</div>

<div class="card">
  <h2>SMTP設定（メール送信サーバー）</h2>
  <div class="note" style="margin-top:0;margin-bottom:12px">
    Gmailの場合: smtp.gmail.com / ポート587 / アプリパスワードを使用してください
  </div>
  <div class="grid2">
    <label>SMTPホスト<input id="smtpHost" value="smtp.gmail.com"></label>
    <label>ポート<input id="smtpPort" type="number" value="587"></label>
    <label>ユーザー（メールアドレス）<input id="smtpUser" placeholder="your@gmail.com"></label>
    <label>パスワード<input id="smtpPass" type="password" placeholder="アプリパスワード"></label>
    <label>送信者名<input id="fromName" value="POS Start"></label>
    <label>送信元メール（空欄でユーザーと同じ）<input id="fromEmail" placeholder=""></label>
  </div>
  <div class="row" style="margin-top:12px;justify-content:flex-end">
    <button class="btn solid" onclick="saveConfig()">設定保存</button>
  </div>
</div>

<div class="card">
  <h2>送信先メールアドレス</h2>
  <div class="note" style="margin-top:0;margin-bottom:12px">
    本締め完了時に、ここに登録されたアドレス全てにデイリーレポートが自動送信されます。
  </div>
  <div class="row" style="margin-bottom:12px">
    <input id="newName" placeholder="名前（任意）" style="width:140px">
    <input id="newEmail" placeholder="メールアドレス" style="flex:1">
    <button class="btn solid" onclick="addRecipient()">追加</button>
  </div>
  <table>
    <thead><tr><th>名前</th><th>メールアドレス</th><th>操作</th></tr></thead>
    <tbody id="recipientList"></tbody>
  </table>
</div>

<div class="card">
  <h2>テスト送信</h2>
  <div class="note" style="margin-top:0;margin-bottom:12px">
    設定と送信先が正しいか確認するために、ダミーデータでテストメールを送信します。
  </div>
  <button class="btn green" onclick="testSend()">テストメールを送信</button>
</div>

<div class="card">
  <h2>送信履歴</h2>
  <table>
    <thead><tr><th>営業日</th><th>送信先</th><th>状態</th><th>送信日時</th></tr></thead>
    <tbody id="logList"></tbody>
  </table>
</div>

<script>
const storeId=1, H={'Content-Type':'application/json','X-Role':'owner'};

async function api(path,opt={}){
  const o={method:'GET',headers:H,...opt};
  if(o.body&&typeof o.body!=='string') o.body=JSON.stringify(o.body);
  const r=await fetch(path,o);
  if(!r.ok) throw new Error(await r.text());
  const ct=r.headers.get('content-type')||'';
  return ct.includes('json')?r.json():r.text();
}

async function loadConfig(){
  try{
    const c=await api(`/mail-config/${storeId}`);
    document.getElementById('smtpHost').value=c.smtp_host||'smtp.gmail.com';
    document.getElementById('smtpPort').value=c.smtp_port||587;
    document.getElementById('smtpUser').value=c.smtp_user||'';
    document.getElementById('fromName').value=c.from_name||'POS Start';
    document.getElementById('fromEmail').value=c.from_email||'';
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
    alert('設定を保存しました');
  }catch(e){alert('エラー: '+e.message)}
}

async function loadRecipients(){
  try{
    const list=await api(`/mail-recipients/${storeId}`);
    document.getElementById('recipientList').innerHTML=list.map(r=>`<tr>
      <td>${r.name||'-'}</td><td>${r.email}</td>
      <td><button class="btn danger" onclick="delRecipient(${r.id})">削除</button></td>
    </tr>`).join('');
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
  if(!confirm('登録済みの送信先にテストメールを送信しますか？'))return;
  try{
    const r=await api(`/mail/test-send/${storeId}`,{method:'POST'});
    alert(r.message||'テスト送信しました');
    loadLogs();
  }catch(e){alert('エラー: '+e.message)}
}

async function loadLogs(){
  try{
    const list=await api(`/mail-logs/${storeId}`);
    document.getElementById('logList').innerHTML=list.map(r=>`<tr>
      <td>${r.business_date}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${r.recipients}</td>
      <td><span class="badge ${r.status}">${r.status==='sent'?'✅ 送信済':'❌ 失敗'}</span></td>
      <td style="font-size:11px">${r.sent_at?new Date(r.sent_at).toLocaleString('ja-JP'):''}</td>
    </tr>`).join('')||'<tr><td colspan="4" style="color:var(--muted)">履歴なし</td></tr>';
  }catch{}
}

loadConfig(); loadRecipients(); loadLogs();
</script></body></html>""")
