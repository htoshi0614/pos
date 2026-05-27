# app.py
from datetime import datetime, date
from typing import List, Optional, Dict, Literal
import json, hashlib, asyncio, os, secrets, time
from contextvars import ContextVar
from fastapi import FastAPI, HTTPException, Header, WebSocket, WebSocketDisconnect, Request, Cookie
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Float, ForeignKey, Boolean, Text, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.orm import joinedload
from zoneinfo import ZoneInfo
from datetime import datetime, timezone, timedelta
from fastapi import status

# ---------- DB (共有モジュールから) ----------
from db_shared import Base, engine, SessionLocal

app = FastAPI(title="Cabaret POS Full")

# ---------- セキュリティ ----------
_FIXED_PASSWORD = "posstart2024"

def _verify_pw_role(pw: str) -> Optional[str]:
    """パスワードを検証し、一致したロールを返す（不一致はNone）"""
    if pw == _FIXED_PASSWORD:
        return "owner"
    return None

# リクエストごとのトークンロール（ContextVar）
_current_token_role: ContextVar[str] = ContextVar("token_role", default="")

# トークン管理（サーバー側セッション）
_active_tokens: Dict[str, dict] = {}  # token -> {"role": str, "created": float}
def _create_token(role: str = "owner") -> str:
    token = secrets.token_urlsafe(32)
    _active_tokens[token] = {"role": role, "created": time.time()}
    return token

def _validate_token(token: str) -> Optional[dict]:
    if not token:
        return None
    info = _active_tokens.get(token)
    if not info:
        return None
    # 24時間で期限切れ
    if time.time() - info["created"] > 86400:
        del _active_tokens[token]
        return None
    return info

# ログイン試行制限
_login_attempts: Dict[str, list] = {}  # ip -> [timestamp, ...]
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300  # 5分

def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # 古い試行を削除
    attempts = [t for t in attempts if now - t < LOCKOUT_SECONDS]
    _login_attempts[ip] = attempts
    return len(attempts) < MAX_ATTEMPTS

def _record_attempt(ip: str):
    now = time.time()
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    _login_attempts[ip].append(now)

# ---------- バージョン情報・アップデート通知 ----------
POSSTART_VERSION = "1.0.5"

@app.get("/api/version")
def api_version():
    """現在のバージョン情報 + アップデート利用可否（launcher.py が環境変数経由で渡す）"""
    return {
        "current": POSSTART_VERSION,
        "update_available": os.environ.get("POSSTART_UPDATE_AVAILABLE") == "1",
        "latest_version": os.environ.get("POSSTART_UPDATE_LATEST", ""),
        "download_url": os.environ.get("POSSTART_UPDATE_URL", ""),
    }

@app.post("/auth/vendor-login")
def vendor_login(payload: dict, request: Request):
    """業者パスワード認証（レート制限・ロール別トークン発行）"""
    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip):
        raise HTTPException(429, "ログイン試行回数が上限に達しました。5分後にお試しください。")
    pw = payload.get("password", "")
    matched_role = _verify_pw_role(pw)
    if matched_role:
        token = _create_token(matched_role)
        resp = JSONResponse({"ok": True, "token": token, "role": matched_role})
        resp.set_cookie("pos_token", token, httponly=True, samesite="lax", max_age=86400)
        return resp
    _record_attempt(ip)
    remaining = MAX_ATTEMPTS - len(_login_attempts.get(ip, []))
    raise HTTPException(401, f"パスワードが正しくありません（残り{remaining}回）")

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
:root{--bg:#fafafa;--card:#ffffff;--card2:#ffffff;--line:#eaeaef;--border:#eaeaef;--text:#0a0a0f;--ink:#0a0a0f;--muted:#8a8a95;--body:#4a4a55;--accent:#d64583;--accent-soft:#fdf0f7;--accent-dark:#b03468;--gold:#c9a96e;--gold-soft:#faf3e3;--warn:#f59e0b;--amber:#f59e0b;--err:#ef4444;--red:#ef4444;--ok:#22c55e;--green:#22c55e;--blue:#3b82f6;--purple:#a855f7;}
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

/* === Premium Pink Theme Override (auto-injected) === */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=Inter:wght@400;500;600;700;800&display=swap');
body{font-family:'Inter','Noto Sans JP',-apple-system,system-ui,Segoe UI,Roboto,sans-serif !important;background:#fafafa !important;color:#0a0a0f !important;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4{color:#0a0a0f}
a{color:#d64583}
.card,section,article{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.stat,.kpi,.tile,.metric{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f}
table{border-color:#eaeaef !important}
th{background:#fafafa !important;color:#4a4a55 !important;border-color:#eaeaef !important;font-weight:700 !important}
td{border-color:#f3f3f6 !important;color:#0a0a0f !important;background:#ffffff}
tr:nth-child(even) td{background:#fafafa}
.btn{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important;font-weight:600 !important;transition:all .2s}
.btn:hover{border-color:#d64583 !important;color:#d64583 !important}
.btn.primary,.btn.solid{background:#d64583 !important;border-color:#d64583 !important;color:#ffffff !important}
.btn.danger{background:#ef4444 !important;border-color:#ef4444 !important;color:#ffffff !important}
input,select,textarea{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important}
input:focus,select:focus{border-color:#d64583 !important;box-shadow:0 0 0 3px #fdf0f7 !important;outline:none}
.method.POST{background:#f0fdf4 !important;color:#15803d !important}
.method.DELETE{background:#fef2f2 !important;color:#b91c1c !important}
.method.PATCH{background:#fff7ed !important;color:#c2410c !important}
.method.PUT{background:#eff6ff !important;color:#1d4ed8 !important}
.cast-card{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.cast-card.in .status{background:#f0fdf4 !important;color:#15803d !important}
.cast-card.out .status{background:#fafafa !important;color:#8a8a95 !important}
.bar{background:#ffffff !important;border-color:#eaeaef}
.notice{background:#fff7ed !important;color:#7c2d12 !important;border-left:3px solid #f59e0b !important}
.modal-card{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.toast.ok{background:#f0fdf4 !important;color:#14532d !important;border-color:#86efac !important}
.toast.err{background:#fef2f2 !important;color:#7f1d1d !important;border-color:#fca5a5 !important}
[style*="background:#111827"],[style*="background:#0f172a"],[style*="background:#0a1220"],[style*="background:#0e1a26"]{background:#ffffff !important;color:#0a0a0f !important}
[style*="border:1px solid #263244"],[style*="border:1px solid #334155"]{border-color:#eaeaef !important}

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
      <div class="price">¥30,000<span> /月（税込）</span></div>
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
      <div style="font-size:13px;color:var(--muted);margin-bottom:16px;line-height:1.7">
        Stripe の安全な決済ページに移動します。<br>
        カード情報は Stripe が直接処理するため、当サービスには保存されません。
      </div>
      <button class="submit-btn card-pay" id="stripeBtn" onclick="submitCard()">💳 Stripe で申し込む →</button>
      <div class="secure">🔒 Stripe による PCI DSS 準拠の暗号化決済</div>
      <div class="error-msg" id="cardErrMsg" style="margin-top:10px"></div>
    </div>

    <div class="bank-fields" id="bankSection">
      <div class="bank-info">
        <div class="label">振込先</div>
        <strong>GMOあおぞらネット銀行 うみ支店</strong><br>
        普通 3234569<br>
        カ）ポススタート<br><br>
        <div class="label">お振込金額</div>
        <strong>¥30,000（税込）</strong><br><br>
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
    <h2 id="successTitle">お申し込みありがとうございます！</h2>
    <p style="color:var(--muted);font-size:14px;margin-bottom:20px" id="successDetail"></p>
    <a href="/" style="color:var(--accent);font-size:14px">トップに戻る</a>
  </div>
</div>

<script>
/* Stripe Checkout 戻り処理 */
(function(){
  const p=new URLSearchParams(location.search);
  const co=p.get('checkout');
  if(co==='success'){
    document.getElementById('formArea').style.display='none';
    const s=document.getElementById('successMsg');
    s.style.display='block';
    document.getElementById('successTitle').textContent='お申し込みありがとうございます！';
    document.getElementById('successDetail').textContent=
      'カード決済が完了しました。\nアカウント情報をご登録のメールアドレスにお送りします。\n（数分かかる場合があります）';
  }else if(co==='canceled'){
    document.getElementById('errorMsg').textContent='決済がキャンセルされました。もう一度お試しください。';
    document.getElementById('errorMsg').style.display='block';
  }
})();

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

async function submitCard(){
  if(!validate()) return;
  const btn=document.getElementById('stripeBtn');
  const err=document.getElementById('cardErrMsg');
  btn.disabled=true; btn.textContent='Stripeへ移動中...';
  err.style.display='none';
  try{
    const r=await fetch('/signup/stripe',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        shop_name: document.getElementById('shopName').value.trim(),
        contact_name: document.getElementById('contactName').value.trim(),
        contact_phone: document.getElementById('contactPhone').value.trim(),
        contact_email: document.getElementById('contactEmail').value.trim(),
        plan: 'monthly',
        base_url: window.location.origin,
      })
    });
    if(!r.ok){
      const t=await r.text();
      let msg='エラーが発生しました';
      try{msg=JSON.parse(t).detail||msg;}catch{}
      err.textContent=msg; err.style.display='block';
      return;
    }
    const d=await r.json();
    if(d.checkout_url) window.location.href=d.checkout_url;
  }catch(e){
    err.textContent='ネットワークエラー: '+e.message; err.style.display='block';
  }finally{
    btn.disabled=false; btn.textContent='💳 Stripe で申し込む →';
  }
}

async function submitBank(){
  if(!validate()) return;
  const btn=document.querySelector('.bank-pay');
  const err=document.getElementById('errorMsg');
  btn.disabled=true; btn.textContent='送信中...';
  try{
    const r=await fetch('/signup/bank',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        shop_name: document.getElementById('shopName').value.trim(),
        contact_name: document.getElementById('contactName').value.trim(),
        contact_phone: document.getElementById('contactPhone').value.trim(),
        contact_email: document.getElementById('contactEmail').value.trim(),
      })
    });
    if(!r.ok){
      const t=await r.text();
      throw new Error(t);
    }
    document.getElementById('formArea').style.display='none';
    const s=document.getElementById('successMsg');
    s.style.display='block';
    document.getElementById('successDetail').textContent=
      '口座振込でのお申し込みを受け付けました。\n上記口座へのお振込をお願いいたします。\n入金確認後、1営業日以内にアカウントを有効化いたします。';
  }catch(e){
    err.textContent='送信に失敗しました: '+e.message;
    err.style.display='block';
    btn.disabled=false; btn.textContent='振込で申し込む';
  }
}
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
:root{--bg:#fafafa;--card:#ffffff;--card2:#ffffff;--line:#eaeaef;--border:#eaeaef;--text:#0a0a0f;--ink:#0a0a0f;--muted:#8a8a95;--body:#4a4a55;--accent:#d64583;--accent-soft:#fdf0f7;--accent-dark:#b03468;--gold:#c9a96e;--gold-soft:#faf3e3;--warn:#f59e0b;--amber:#f59e0b;--err:#ef4444;--red:#ef4444;--ok:#22c55e;--green:#22c55e;--blue:#3b82f6;--purple:#a855f7;}
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

/* === Premium Pink Theme Override (auto-injected) === */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=Inter:wght@400;500;600;700;800&display=swap');
body{font-family:'Inter','Noto Sans JP',-apple-system,system-ui,Segoe UI,Roboto,sans-serif !important;background:#fafafa !important;color:#0a0a0f !important;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4{color:#0a0a0f}
a{color:#d64583}
.card,section,article{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.stat,.kpi,.tile,.metric{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f}
table{border-color:#eaeaef !important}
th{background:#fafafa !important;color:#4a4a55 !important;border-color:#eaeaef !important;font-weight:700 !important}
td{border-color:#f3f3f6 !important;color:#0a0a0f !important;background:#ffffff}
tr:nth-child(even) td{background:#fafafa}
.btn{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important;font-weight:600 !important;transition:all .2s}
.btn:hover{border-color:#d64583 !important;color:#d64583 !important}
.btn.primary,.btn.solid{background:#d64583 !important;border-color:#d64583 !important;color:#ffffff !important}
.btn.danger{background:#ef4444 !important;border-color:#ef4444 !important;color:#ffffff !important}
input,select,textarea{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important}
input:focus,select:focus{border-color:#d64583 !important;box-shadow:0 0 0 3px #fdf0f7 !important;outline:none}
.method.POST{background:#f0fdf4 !important;color:#15803d !important}
.method.DELETE{background:#fef2f2 !important;color:#b91c1c !important}
.method.PATCH{background:#fff7ed !important;color:#c2410c !important}
.method.PUT{background:#eff6ff !important;color:#1d4ed8 !important}
.cast-card{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.cast-card.in .status{background:#f0fdf4 !important;color:#15803d !important}
.cast-card.out .status{background:#fafafa !important;color:#8a8a95 !important}
.bar{background:#ffffff !important;border-color:#eaeaef}
.notice{background:#fff7ed !important;color:#7c2d12 !important;border-left:3px solid #f59e0b !important}
.modal-card{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.toast.ok{background:#f0fdf4 !important;color:#14532d !important;border-color:#86efac !important}
.toast.err{background:#fef2f2 !important;color:#7f1d1d !important;border-color:#fca5a5 !important}
[style*="background:#111827"],[style*="background:#0f172a"],[style*="background:#0a1220"],[style*="background:#0e1a26"]{background:#ffffff !important;color:#0a0a0f !important}
[style*="border:1px solid #263244"],[style*="border:1px solid #334155"]{border-color:#eaeaef !important}

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
      <div class="plan-price">¥30,000<span> /月（税込）</span></div>
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
  alert('口座振込のご案内をメールでお送りします。\n\n振込先:\nGMOあおぞらネット銀行 うみ支店\n普通 3234569\nカ）ポススタート\n\n月額: ¥30,000（税込）\n\nお振込確認後、アカウントを有効化いたします。');
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
      const d=await r.json();
      sessionStorage.setItem('pos_auth','vendor');
      if(d.token) sessionStorage.setItem('pos_token', d.token);
      if(d.role) sessionStorage.setItem('pos_role', d.role);
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

# メインの asyncio loop を保持（sync ハンドラからの broadcast 用）
_main_loop: Optional[asyncio.AbstractEventLoop] = None

@app.on_event("startup")
async def _capture_main_loop():
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    # 自動バックアップをサーバー起動時に開始（6時間間隔）
    try:
        from backup_service import start_auto_backup_on_startup
        start_auto_backup_on_startup(interval_minutes=360)
        print("[backup] 自動バックアップ開始（6時間間隔）")
    except Exception as _be:
        print(f"[backup] 自動バックアップ起動失敗: {_be}")

async def notify_clients(event: str, data: dict = None):
    """全端末にイベントを通知"""
    try:
        await ws_manager.broadcast({"event": event, "data": data or {}})
    except Exception:
        pass

def _safe_notify(event: str, data: dict = None):
    """同期関数からWebSocket通知を安全に発火（スレッドセーフ）

    FastAPI の sync 'def' ハンドラは worker thread で実行され、その thread には
    running loop が存在しないため `asyncio.get_running_loop()` は RuntimeError を投げる。
    起動時に保持したメインループに `run_coroutine_threadsafe` で送る。
    """
    # まず running loop を試す（async ハンドラから呼ばれた場合）
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(notify_clients(event, data))
        return
    except RuntimeError:
        pass
    # worker thread から → メインループへスレッドセーフに投げる
    if _main_loop is not None and _main_loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(notify_clients(event, data), _main_loop)
        except Exception:
            pass

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)

# ---------- API認証ミドルウェア ----------
_PUBLIC_PATHS = {"/", "/auth/vendor-login", "/signup", "/signup/bank", "/ws", "/docs", "/openapi.json", "/favicon.ico", "/stripe/webhook", "/stripe/status", "/api/version"}

# サブスクが切れていてもアクセスを許可するパス（解約後も再契約できるように）
_SUBSCRIPTION_BYPASS_PREFIXES = (
    "/ui/subscription",
    "/ui/admin",
    "/subscription/",
    "/stripe-config/",
    "/stripe/",
    "/auth/",
    "/admin/",
)
_SUBSCRIPTION_BYPASS_PATHS = {"/", "/signup", "/signup/bank", "/favicon.ico", "/docs", "/openapi.json", "/ws"}

def _is_subscription_bypass(path: str) -> bool:
    if path in _SUBSCRIPTION_BYPASS_PATHS:
        return True
    return any(path.startswith(p) for p in _SUBSCRIPTION_BYPASS_PREFIXES)

def _check_pos_locked() -> tuple[bool, str]:
    """POSがサブスク切れでロックすべきか判定"""
    try:
        from stripe_service import is_pos_locked
        db = SessionLocal()
        try:
            return is_pos_locked(db)
        finally:
            db.close()
    except Exception:
        return False, "check_failed"

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # サブスク切れチェック（公開パス含めすべてに適用、ただしバイパス対象は素通り）
    if not _is_subscription_bypass(path):
        locked, reason = _check_pos_locked()
        if locked:
            # /ui/* へのアクセスはサブスク画面へリダイレクト
            if path.startswith("/ui"):
                return JSONResponse(
                    status_code=307,
                    content={"detail": "サブスクリプションが無効です"},
                    headers={"Location": "/ui/subscription"},
                )
            # APIは 402 Payment Required
            return JSONResponse(
                {"detail": "サブスクリプションが無効です。お支払い情報をご確認ください。", "reason": reason, "locked": True},
                status_code=402,
            )
    # 公開パス・UIページ・静的ファイルはトークン検証スキップ（/lp, /api/demo-request も含む）
    if path in _PUBLIC_PATHS or path.startswith("/ui") or path.startswith("/lp") or path == "/api/demo-request":
        return await call_next(request)
    # APIはトークン検証
    token = request.cookies.get("pos_token") or request.headers.get("X-Token", "")
    token_info = _validate_token(token)
    if not token_info:
        return JSONResponse({"detail": "認証が必要です"}, status_code=401)
    # トークンのロールをリクエストstateとContextVarに保存
    tr = token_info.get("role", "staff")
    request.state.token_role = tr
    _current_token_role.set(tr)
    return await call_next(request)

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
            role_val = _current_token_role.get("") or request.headers.get("X-Role", "")
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
    """ロール検証 — トークンロールで上書きしてクライアント偽装を防止"""
    token_role = _current_token_role.get("")
    if token_role and token_role != "owner":
        # owner以外はトークンのロールを強制（X-Role偽装防止）
        effective = token_role
    else:
        # ownerトークン or トークンなし（公開パス）はヘッダー値を許可
        effective = role_header or token_role or None
    if not effective:
        raise HTTPException(401, "Missing X-Role")
    if effective not in allowed:
        raise HTTPException(403, f"Role '{effective}' not allowed for this action")

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
    set_fee_override = Column(Float, nullable=True)  # 入店時コース選択で指定した料金（/人）
    status = Column(String, default="open")
    note = Column(Text, default="")
    discount_label = Column(String, default="")        # 適用中の割引名
    discount_type = Column(String, default="")         # fixed/rate/set_override/free_drink
    discount_value = Column(Float, default=0.0)        # 割引値
    table = relationship("Table")
    customer = relationship("Customer")
    orders = relationship("Order", back_populates="session", cascade="all,delete")
    nominations = relationship("Nomination", back_populates="session", cascade="all,delete")
    payments = relationship("Payment", back_populates="session", cascade="all,delete")

class ExtensionRecord(Base):
    """延長オプション選択で発生した延長履歴（料金追跡用）"""
    __tablename__ = "extension_records"
    id         = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), index=True)
    minutes    = Column(Integer, default=30)
    price_pp   = Column(Float, default=3000.0)   # 1人あたり料金（円）
    label      = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

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
    set_fee_override: Optional[float] = None  # 入店時コース選択
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
    amount: float = Field(gt=0, description="支払い金額（0より大きい値）")

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
    from pricing_engine import PricingConfig, TimeSlotRule, DiscountRule, SetPlanOption, ExtendOption
    from cast_salary import CastSalaryConfig, DrinkBackRecord
    from weather_service import WeatherConfig, StaffSchedule
    from stripe_service import StripeSubscription, StripeConfig, BankSignup
    from closing import Closing
    from bottle_keep import BottleKeep
    from customer_crm import CustomerProfile, VisitLog
    from tab_management import TabRecord, TabPayment
    from management import CastGoal
    Base.metadata.create_all(engine)
except Exception as _ext_err:
    print(f"[warn] 拡張モジュール読み込み: {_ext_err}")

# --- stripe_subscriptions.payment_method カラム追加マイグレーション ---
try:
    with engine.connect() as conn:
        from sqlalchemy import text, inspect as sa_inspect_pm
        cols_ss = [c["name"] for c in sa_inspect_pm(engine).get_columns("stripe_subscriptions")]
        if "payment_method" not in cols_ss:
            conn.execute(text("ALTER TABLE stripe_subscriptions ADD COLUMN payment_method VARCHAR DEFAULT 'card'"))
            conn.commit()
            print("[migrate] stripe_subscriptions.payment_method added")
except Exception:
    pass

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

# --- drink_back_records.back_type / cast_salary_configs.bottle_back_rate マイグレーション ---
try:
    with engine.connect() as conn:
        from sqlalchemy import text, inspect as sa_inspect
        # back_type カラム
        cols_db = [c["name"] for c in sa_inspect(engine).get_columns("drink_back_records")]
        if "back_type" not in cols_db:
            conn.execute(text("ALTER TABLE drink_back_records ADD COLUMN back_type VARCHAR DEFAULT 'drink'"))
            conn.commit()
            print("[migrate] drink_back_records.back_type added")
        # bottle_back_rate カラム
        cols_cfg = [c["name"] for c in sa_inspect(engine).get_columns("cast_salary_configs")]
        if "bottle_back_rate" not in cols_cfg:
            conn.execute(text("ALTER TABLE cast_salary_configs ADD COLUMN bottle_back_rate FLOAT DEFAULT 0.0"))
            conn.commit()
            print("[migrate] cast_salary_configs.bottle_back_rate added")
except Exception:
    pass

# --- sessions.discount_* マイグレーション ---
try:
    with engine.connect() as conn:
        from sqlalchemy import text, inspect as sa_inspect_disc
        cols_s = [c["name"] for c in sa_inspect_disc(engine).get_columns("sessions")]
        for col, dtype, default in [
            ("discount_label", "VARCHAR", "''"),
            ("discount_type", "VARCHAR", "''"),
            ("discount_value", "FLOAT", "0.0"),
        ]:
            if col not in cols_s:
                conn.execute(text(f"ALTER TABLE sessions ADD COLUMN {col} {dtype} DEFAULT {default}"))
                conn.commit()
                print(f"[migrate] sessions.{col} added")
except Exception:
    pass

# --- sessions.set_fee_override マイグレーション ---
try:
    with engine.connect() as conn:
        from sqlalchemy import text, inspect as sa_inspect_sfo
        cols_sfo = [c["name"] for c in sa_inspect_sfo(engine).get_columns("sessions")]
        if "set_fee_override" not in cols_sfo:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN set_fee_override FLOAT"))
            conn.commit()
            print("[migrate] sessions.set_fee_override added")
except Exception:
    pass

# --- pricing_configs.set_minutes / extend_unit マイグレーション ---
try:
    with engine.connect() as conn:
        from sqlalchemy import text, inspect as sa_inspect2
        try:
            cols_pc = [c["name"] for c in sa_inspect2(engine).get_columns("pricing_configs")]
            if "set_minutes" not in cols_pc:
                conn.execute(text("ALTER TABLE pricing_configs ADD COLUMN set_minutes INTEGER DEFAULT 60"))
                conn.commit()
                print("[migrate] pricing_configs.set_minutes added")
            if "extend_unit" not in cols_pc:
                conn.execute(text("ALTER TABLE pricing_configs ADD COLUMN extend_unit INTEGER DEFAULT 30"))
                conn.commit()
                print("[migrate] pricing_configs.extend_unit added")
        except Exception:
            pass
except Exception:
    pass

# ---------- 会計計算 ----------
def compute_bill(db, s: Session) -> Dict:
    # 料金ルールエンジンから設定を取得
    try:
        from pricing_engine import get_pricing_config, get_slot_rule, compute_night_surcharge, compute_totals
        config   = get_pricing_config(db, s.store_id)
        slot     = get_slot_rule(db, s.store_id, s.start_time)
        slot_set_fee   = slot.set_price    if slot else 6000.0
        extend_fee     = slot.extend_price if slot else 3000.0
    except Exception:
        config = None
        slot_set_fee = 6000.0
        extend_fee = 3000.0

    # コース選択で上書きされていればそちらを使用
    set_fee = s.set_fee_override if s.set_fee_override is not None else slot_set_fee

    end_time = s.end_time or datetime.utcnow()

    total_minutes  = max(0, int((end_time - s.start_time).total_seconds() // 60))
    booked_minutes = int(s.set_minutes or 60)
    sets      = 1

    # 延長オプション選択履歴があればそれを使用（明示的な料金追跡）
    try:
        ext_records = db.query(ExtensionRecord).filter_by(session_id=s.id).all()
    except Exception:
        ext_records = []

    if ext_records:
        extends       = len(ext_records)
        extend_amount = sum(r.price_pp * s.guest_count for r in ext_records)
    else:
        remaining = max(0, total_minutes - booked_minutes)
        eu = max(1, int(s.extend_unit or 30))
        extends       = (remaining + eu - 1) // eu if remaining > 0 else 0
        extend_amount = extends * extend_fee * s.guest_count

    set_amount    = sets * set_fee * s.guest_count
    time_amount   = set_amount + extend_amount

    # お通し/TC・VIP席料
    table_charge = 0.0
    vip_fee      = 0.0
    if config:
        table_charge = (config.table_charge_pp or 0) * s.guest_count
        vip_fee      = config.vip_seat_fee or 0

    order_subtotal = sum(o.unit_price * o.qty for o in s.orders)

    # 割引計算
    discount_amount = 0.0
    discount_label = s.discount_label or ""
    discount_type = s.discount_type or ""
    if discount_type == "set_override" and s.discount_value > 0:
        # セット料金を指定額に上書き（差額が割引）
        original_set = set_amount
        overridden = s.discount_value * s.guest_count
        discount_amount = max(0, original_set - overridden)
    elif discount_type == "free_drink" and s.discount_value > 0:
        # ドリンク○杯分を無料（安い順から適用）
        drink_orders = sorted(
            [o for o in s.orders if o.item and o.item.category == "drink"],
            key=lambda o: o.unit_price
        )
        free_left = int(s.discount_value)
        for o in drink_orders:
            take = min(free_left, o.qty)
            discount_amount += o.unit_price * take
            free_left -= take
            if free_left <= 0:
                break
    elif discount_type == "rate" and s.discount_value > 0:
        # 小計から○%引き
        pre_disc = time_amount + order_subtotal + table_charge + vip_fee
        discount_amount = pre_disc * min(s.discount_value, 1.0)
    elif discount_type == "fixed" and s.discount_value > 0:
        # 固定額引き
        discount_amount = s.discount_value

    # 指名料（本指名・場内指名・同伴）
    nomination_fee = 0.0
    nomination_breakdown = []
    try:
        _nomi_labels = {"hon": "本指名料", "jyonai": "場内指名料", "dohan": "同伴料"}
        for n in s.nominations:
            if n.fee and n.fee > 0:
                label = _nomi_labels.get(n.nomi_type, "指名料")
                cast_name = n.cast.name if n.cast else ""
                nomination_breakdown.append({
                    "label": f"{label}（{cast_name}）" if cast_name else label,
                    "amount": float(n.fee),
                })
                nomination_fee += n.fee
    except Exception:
        pass

    subtotal = time_amount + order_subtotal + table_charge + vip_fee + nomination_fee - discount_amount
    subtotal = max(0, subtotal)

    # 深夜加算
    try:
        night_add = compute_night_surcharge(config, subtotal, s.start_time, end_time)
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
        "nomination_fee": float(nomination_fee),
        "nominations": nomination_breakdown,
        "nomi_tags": [
            {"prefix": {"hon":"A","jyonai":"B","dohan":"D"}.get(n.nomi_type,"?"),
             "cast_name": n.cast.name if n.cast else "?",
             "nomi_type": n.nomi_type}
            for n in s.nominations
        ],
        "discount_label": discount_label,
        "discount_type": discount_type,
        "discount_amount": float(discount_amount),
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

@app.delete("/tables/{table_id}")
def delete_table(table_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager"])
    db = SessionLocal()
    try:
        t = db.query(Table).get(table_id)
        if not t:
            raise HTTPException(404, "table not found")
        # Check if table has active session
        active = db.query(Session).filter_by(table_id=table_id, status="open").first()
        if active:
            raise HTTPException(400, "active session exists on this table")
        db.delete(t); db.commit()
        return {"ok": True}
    finally:
        db.close()

def _is_cast_on_duty(db, cast_id: int) -> bool:
    """キャストが現在出勤中（出勤打刻あり、退勤打刻なし）かどうか判定"""
    active = db.query(Attendance).filter_by(
        person_type="cast", person_id=cast_id, clock_out=None
    ).filter(Attendance.clock_in != None).first()
    return active is not None

@app.get("/casts")
def list_casts(store_id: int, x_role: Optional[Role]=Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        casts = db.query(Cast).filter_by(store_id=store_id, is_active=True).all()
        return [{
            "id": c.id,
            "store_id": c.store_id,
            "name": c.name,
            "is_on_duty": _is_cast_on_duty(db, c.id),
        } for c in casts]
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
        # テーブル存在チェック
        tbl = db.query(Table).filter_by(id=payload.table_id, store_id=payload.store_id).first()
        if not tbl:
            raise HTTPException(404, "Table not found")

        # 同一テーブルへの重複入店チェック
        existing = db.query(Session).filter_by(
            table_id=payload.table_id, store_id=payload.store_id, status="open"
        ).first()
        if existing:
            raise HTTPException(400, f"このテーブルはすでに使用中です（セッション #{existing.id}）。先に会計を確定してください。")

        s = Session(
            store_id=payload.store_id,
            table_id=payload.table_id,
            guest_count=payload.guest_count,
            set_minutes=payload.set_minutes,
            extend_unit=payload.extend_unit,
            set_fee_override=payload.set_fee_override,
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
        if payload.cast_id:
            cast = db.get(Cast, payload.cast_id)
            if not cast: raise HTTPException(404, "Cast not found")
        o = Order(store_id=s.store_id, session_id=session_id,
                  item_id=payload.item_id, qty=payload.qty, unit_price=item.price,
                  cast_id=payload.cast_id)
        db.add(o); db.commit(); db.refresh(o)
        _safe_notify("order", {"session_id": session_id})

        # ドリンクバック／ボトルバック自動記録（キャスト指定の場合）
        if payload.cast_id and item.category in ("drink", "bottle"):
            try:
                from cast_salary import CastSalaryConfig, DrinkBackRecord
                cfg = db.query(CastSalaryConfig).filter_by(cast_id=payload.cast_id).first()
                if item.category == "drink":
                    rate = cfg.drink_back_rate if cfg else 0
                    back_type = "drink"
                else:  # bottle
                    rate = cfg.bottle_back_rate if cfg else 0
                    back_type = "bottle"
                if rate > 0:
                    back_amount = item.price * payload.qty * rate  # サービス料抜き小計
                    rec = DrinkBackRecord(
                        store_id=s.store_id, cast_id=payload.cast_id,
                        session_id=session_id, order_id=o.id,
                        back_type=back_type, amount=back_amount)
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
        if s.status != "open":
            raise HTTPException(400, "会計済みセッションへの支払いは追加できません")
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
def extend_session(
    session_id: int,
    option_id: Optional[int] = None,  # 延長オプションID（クエリパラメータ）
    x_role: Optional[Role] = Header(None, alias="X-Role"),
):
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s or s.status != "open":
            raise HTTPException(404, "Session not found or closed")
        check_closing_lock(db, s)

        if option_id is not None:
            # 延長オプション選択の場合：ExtensionRecord を作成して明示的に料金を追跡
            try:
                from pricing_engine import ExtendOption
                opt = db.get(ExtendOption, option_id)
            except Exception:
                opt = None
            if not opt:
                raise HTTPException(404, "延長オプションが見つかりません")
            s.set_minutes = int(s.set_minutes or 60) + opt.minutes
            rec = ExtensionRecord(
                session_id=session_id,
                minutes=opt.minutes,
                price_pp=opt.price,
                label=opt.label,
            )
            db.add(rec)
            add_label = f"{opt.label} (+{opt.minutes}分)"
        else:
            # 従来の動作（延長オプション未設定のデフォルト延長）
            s.set_minutes = int(s.set_minutes or 60) + int(s.extend_unit or 30)
            add_label = f"+{s.extend_unit or 30}分"

        db.commit()
        _safe_notify("extend", {"session_id": session_id})
        return {"ok": True, "set_minutes": s.set_minutes, "label": add_label}
    finally:
        db.close()

@app.post("/sessions/{session_id}/checkout")
def checkout(session_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner","manager","cashier"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s: raise HTTPException(404, "Session not found")
        if s.status == "closed":
            raise HTTPException(400, "すでに会計済みです")
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
    延長取消API：直前の延長を取り消す。
    延長オプション選択の場合は ExtensionRecord の最新1件を削除してその分を減算。
    従来の延長の場合は extend_unit 分だけ減算（最低限 extend_unit 分は維持）。
    """
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s or s.status != "open":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found or closed")
        check_closing_lock(db, s)

        # 延長オプション選択履歴があれば、最新を取り消す
        last_rec = None
        try:
            last_rec = (db.query(ExtensionRecord)
                         .filter_by(session_id=session_id)
                         .order_by(ExtensionRecord.created_at.desc())
                         .first())
        except Exception:
            pass

        if last_rec:
            s.set_minutes = max(1, int(s.set_minutes or 60) - last_rec.minutes)
            db.delete(last_rec)
        else:
            # 従来の動作
            eu = int(s.extend_unit or 30)
            s.set_minutes = max(eu, int(s.set_minutes or 60) - eu)

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
        try:
            h, m = int(hm[0]), int(hm[1])
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "HH:MM形式で入力してください")
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "00:00〜23:59 の範囲で入力してください")
        # JSTで入力された時刻をUTCに変換
        jst = timezone(timedelta(hours=9))
        now_jst = datetime.now(jst)
        try:
            new_start_jst = now_jst.replace(hour=h, minute=m, second=0, microsecond=0)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"無効な時刻です: {e}")
        new_start = new_start_jst.astimezone(timezone.utc).replace(tzinfo=None)
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


@app.post("/sessions/{session_id}/douhan")
def record_douhan(session_id: int, payload: dict, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """同伴登録API"""
    require_role(x_role, ["owner","manager","cashier","staff"])
    cast_id = payload.get("cast_id")
    if not cast_id:
        raise HTTPException(400, "cast_id is required")
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s or s.status != "open":
            raise HTTPException(404, "Session not found or closed")
        cast = db.get(Cast, cast_id)
        if not cast:
            raise HTTPException(404, "Cast not found")
        # 出勤中チェック（退勤中・未出勤のキャストは同伴登録不可）
        if not _is_cast_on_duty(db, cast_id):
            raise HTTPException(400, f"{cast.name} さんは現在退勤中（または未出勤）のため、同伴登録できません。先に出勤打刻をしてください。")
        # 既に同伴登録済みかチェック
        existing = db.query(Nomination).filter_by(session_id=session_id, nomi_type="dohan").first()
        if existing:
            raise HTTPException(400, "This session already has douhan registered")
        try:
            from cast_salary import CastSalaryConfig
            cfg = db.query(CastSalaryConfig).filter_by(cast_id=cast_id).first()
            dohan_fee = float(cfg.nom_fee_dohan) if cfg and cfg.nom_fee_dohan else 0.0
        except Exception:
            dohan_fee = 0.0
        nom = Nomination(store_id=s.store_id, session_id=session_id, cast_id=cast_id, nomi_type="dohan", fee=dohan_fee)
        db.add(nom)
        db.commit()
        _safe_notify("douhan", {"session_id": session_id, "cast_id": cast_id})
        return {"ok": True, "nomination_id": nom.id}
    finally:
        db.close()

@app.delete("/sessions/{session_id}/douhan")
def cancel_douhan(session_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """同伴取消API"""
    require_role(x_role, ["owner","manager","cashier"])
    db = SessionLocal()
    try:
        nom = db.query(Nomination).filter_by(session_id=session_id, nomi_type="dohan").first()
        if not nom:
            raise HTTPException(404, "No douhan found for this session")
        db.delete(nom)
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.post("/sessions/{session_id}/nominations")
def add_nomination(session_id: int, payload: dict, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """本指名 / 場内指名登録API（hon または jyonai）"""
    require_role(x_role, ["owner","manager","cashier","staff"])
    cast_id = payload.get("cast_id")
    nomi_type = payload.get("nomi_type")
    if nomi_type not in ("hon", "jyonai"):
        raise HTTPException(400, "nomi_type は 'hon' または 'jyonai' を指定してください")
    if not cast_id:
        raise HTTPException(400, "cast_id は必須です")
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s or s.status != "open":
            raise HTTPException(404, "Session not found or closed")
        cast = db.get(Cast, cast_id)
        if not cast:
            raise HTTPException(404, "Cast not found")
        # 出勤中チェック（退勤中・未出勤のキャストは指名不可）
        if not _is_cast_on_duty(db, cast_id):
            raise HTTPException(400, f"{cast.name} さんは現在退勤中（または未出勤）のため、指名できません。先に出勤打刻をしてください。")
        # 同セッション内で同キャスト×同種別の重複チェック
        existing_nom = db.query(Nomination).filter_by(
            session_id=session_id, cast_id=cast_id, nomi_type=nomi_type
        ).first()
        if existing_nom:
            raise HTTPException(400, "すでに同じ指名が登録されています")
        try:
            from cast_salary import CastSalaryConfig
            cfg = db.query(CastSalaryConfig).filter_by(cast_id=cast_id).first()
            if cfg:
                nom_fee = float(cfg.nom_fee_hon) if nomi_type == "hon" else float(cfg.nom_fee_jyonai)
            else:
                nom_fee = 0.0
        except Exception:
            nom_fee = 0.0
        nom = Nomination(store_id=s.store_id, session_id=session_id,
                         cast_id=cast_id, nomi_type=nomi_type, fee=nom_fee)
        db.add(nom)
        db.commit()
        _safe_notify("nomination", {"session_id": session_id, "cast_id": cast_id, "nomi_type": nomi_type})
        return {"ok": True, "nomination_id": nom.id}
    finally:
        db.close()

@app.delete("/sessions/{session_id}/nominations/{nomination_id}")
def delete_nomination(session_id: int, nomination_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """指名取消API"""
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        nom = db.query(Nomination).filter_by(id=nomination_id, session_id=session_id).first()
        if not nom:
            raise HTTPException(404, "指名記録が見つかりません")
        db.delete(nom)
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.get("/sessions/{session_id}/nominations")
def list_nominations(session_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """セッションの指名一覧取得"""
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        noms = db.query(Nomination).filter_by(session_id=session_id).all()
        return [{"id": n.id, "cast_id": n.cast_id, "nomi_type": n.nomi_type} for n in noms]
    finally:
        db.close()

@app.post("/sessions/{session_id}/discount")
def apply_discount(session_id: int, payload: dict, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """セッションに割引を適用"""
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s or s.status != "open":
            raise HTTPException(404, "Session not found or closed")
        disc_type = payload.get("disc_type", "")
        valid_disc_types = ("fixed", "rate", "set_override", "free_drink", "")
        if disc_type not in valid_disc_types:
            raise HTTPException(400, f"disc_type は {', '.join(t for t in valid_disc_types if t)} のいずれかを指定してください")
        s.discount_label = payload.get("label", "")
        s.discount_type = disc_type
        s.discount_value = float(payload.get("value", 0))
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.delete("/sessions/{session_id}/discount")
def remove_discount(session_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    """セッションの割引を解除"""
    require_role(x_role, ["owner","manager","cashier","staff"])
    db = SessionLocal()
    try:
        s = db.get(Session, session_id)
        if not s or s.status != "open":
            raise HTTPException(404, "Session not found or closed")
        s.discount_label = ""
        s.discount_type = ""
        s.discount_value = 0.0
        db.commit()
        return {"ok": True}
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
                # ドリンクバック / ボトルバック記録も一緒に削除
                try:
                    from cast_salary import DrinkBackRecord
                    db.query(DrinkBackRecord).filter_by(order_id=o.id).delete()
                except Exception:
                    pass
                db.delete(o)
            else:
                # 部分キャンセル: バック金額を比率で減算
                try:
                    from cast_salary import DrinkBackRecord
                    back = db.query(DrinkBackRecord).filter_by(order_id=o.id).first()
                    if back:
                        back.amount = back.amount * (o.qty - to_cancel) / o.qty
                except Exception:
                    pass
                o.qty -= to_cancel
                to_cancel = 0
        db.commit()
        _safe_notify("cancel_order", {"session_id": session_id})
        return {"ok": True, "remaining": to_cancel}
    finally:
        db.close()

# ---------- 出退勤API ----------
@app.post("/attendance/clock-in")
def clock_in(payload: dict, x_role: Optional[Role] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner"])
    store_id = payload.get("store_id")
    cast_id = payload.get("cast_id")
    if not store_id or not cast_id:
        raise HTTPException(400, "store_id and cast_id required")
    db = SessionLocal()
    try:
        cast = db.get(Cast, cast_id)
        if not cast:
            raise HTTPException(404, "Cast not found")
        # 既に出勤中かチェック
        existing = db.query(Attendance).filter_by(
            store_id=store_id, person_type="cast", person_id=cast_id, clock_out=None
        ).first()
        if existing:
            raise HTTPException(400, "Already clocked in")
        a = Attendance(store_id=store_id, person_type="cast", person_id=cast_id, clock_in=datetime.utcnow())
        db.add(a); db.commit(); db.refresh(a)
        return {"ok": True, "attendance_id": a.id}
    finally:
        db.close()

@app.post("/attendance/clock-out")
def clock_out(payload: dict, x_role: Optional[Role] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner"])
    store_id = payload.get("store_id")
    cast_id = payload.get("cast_id")
    if not store_id or not cast_id:
        raise HTTPException(400, "store_id and cast_id required")
    db = SessionLocal()
    try:
        a = db.query(Attendance).filter_by(
            store_id=store_id, person_type="cast", person_id=cast_id, clock_out=None
        ).order_by(Attendance.clock_in.desc()).first()
        if not a:
            raise HTTPException(400, "Not clocked in")
        a.clock_out = datetime.utcnow()
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.get("/attendance/status")
def attendance_status(store_id: int, x_role: Optional[Role] = Header(None, alias="X-Role")):
    require_role(x_role, ["owner"])
    db = SessionLocal()
    try:
        from cast_salary import CastSalaryConfig
        casts = db.query(Cast).filter_by(store_id=store_id, is_active=True).all()
        cfgs = {cfg.cast_id: cfg for cfg in db.query(CastSalaryConfig).filter_by(store_id=store_id).all()}
        result = []
        for c in casts:
            a = db.query(Attendance).filter_by(
                store_id=store_id, person_type="cast", person_id=c.id, clock_out=None
            ).first()
            cfg = cfgs.get(c.id)
            result.append({
                "cast_id": c.id, "cast_name": c.name,
                "clocked_in": a is not None,
                "clock_in_time": a.clock_in.isoformat() if a else None,
                "hourly_rate": float(cfg.hourly_rate) if cfg else 0.0,
            })
        return result
    finally:
        db.close()

# ---------- 出退勤記録の手動編集（オーナーのみ） ----------
def _parse_jst_iso(s: str) -> datetime:
    """ISO形式 (YYYY-MM-DDTHH:MM) を JST として解釈し、UTC naive datetime を返す"""
    if not s:
        raise HTTPException(400, "datetime required")
    try:
        # 末尾Zやタイムゾーンが含まれていればそのままfromisoformat
        s_clean = s.rstrip("Z")
        dt = datetime.fromisoformat(s_clean)
    except ValueError:
        raise HTTPException(400, f"invalid datetime: {s}")
    jst = ZoneInfo("Asia/Tokyo")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=jst)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)

@app.get("/attendance/records")
def attendance_records(store_id: int, year: int, month: int,
                       x_role: Optional[Role] = Header(None, alias="X-Role")):
    """月次の出退勤記録一覧（オーナー限定・手動編集用）"""
    require_role(x_role, ["owner"])
    db = SessionLocal()
    try:
        jst = ZoneInfo("Asia/Tokyo")
        rows = (db.query(Attendance)
                .filter_by(store_id=store_id, person_type="cast")
                .order_by(Attendance.clock_in.desc())
                .all())
        casts = {c.id: c.name for c in db.query(Cast).filter_by(store_id=store_id).all()}
        result = []
        for a in rows:
            if not a.clock_in:
                continue
            ci_jst = a.clock_in.replace(tzinfo=timezone.utc).astimezone(jst)
            if ci_jst.year != year or ci_jst.month != month:
                continue
            co_jst = a.clock_out.replace(tzinfo=timezone.utc).astimezone(jst) if a.clock_out else None
            hours = (a.clock_out - a.clock_in).total_seconds() / 3600 if a.clock_out else None
            result.append({
                "id": a.id,
                "cast_id": a.person_id,
                "cast_name": casts.get(a.person_id, f"#{a.person_id}"),
                "clock_in":  ci_jst.strftime("%Y-%m-%dT%H:%M"),
                "clock_out": co_jst.strftime("%Y-%m-%dT%H:%M") if co_jst else None,
                "hours": round(hours, 2) if hours is not None else None,
            })
        return result
    finally:
        db.close()

@app.post("/attendance/records")
def attendance_create(payload: dict,
                       x_role: Optional[Role] = Header(None, alias="X-Role")):
    """打刻忘れの手動追加（オーナー限定）"""
    require_role(x_role, ["owner"])
    store_id = payload.get("store_id")
    cast_id = payload.get("cast_id")
    clock_in_str = payload.get("clock_in")
    clock_out_str = payload.get("clock_out")
    if not (store_id and cast_id and clock_in_str):
        raise HTTPException(400, "store_id, cast_id, clock_in are required")
    ci = _parse_jst_iso(clock_in_str)
    co = _parse_jst_iso(clock_out_str) if clock_out_str else None
    if co and co <= ci:
        raise HTTPException(400, "退勤時刻は出勤時刻より後である必要があります")
    db = SessionLocal()
    try:
        a = Attendance(store_id=store_id, person_type="cast", person_id=cast_id,
                       clock_in=ci, clock_out=co)
        db.add(a); db.commit(); db.refresh(a)
        return {"ok": True, "id": a.id}
    finally:
        db.close()

@app.patch("/attendance/records/{rec_id}")
def attendance_update(rec_id: int, payload: dict,
                       x_role: Optional[Role] = Header(None, alias="X-Role")):
    """既存記録の修正（オーナー限定）"""
    require_role(x_role, ["owner"])
    db = SessionLocal()
    try:
        a = db.get(Attendance, rec_id)
        if not a:
            raise HTTPException(404, "記録が見つかりません")
        if "clock_in" in payload:
            a.clock_in = _parse_jst_iso(payload["clock_in"])
        if "clock_out" in payload:
            a.clock_out = _parse_jst_iso(payload["clock_out"]) if payload["clock_out"] else None
        if a.clock_out and a.clock_out <= a.clock_in:
            raise HTTPException(400, "退勤時刻は出勤時刻より後である必要があります")
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.delete("/attendance/records/{rec_id}")
def attendance_delete(rec_id: int,
                       x_role: Optional[Role] = Header(None, alias="X-Role")):
    """記録の削除（オーナー限定）"""
    require_role(x_role, ["owner"])
    db = SessionLocal()
    try:
        a = db.get(Attendance, rec_id)
        if not a:
            raise HTTPException(404, "記録が見つかりません")
        db.delete(a); db.commit()
        return {"ok": True}
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
try:
    from landing import router as _landing_router, DemoRequest
    app.include_router(_landing_router)
except Exception as e:
    print(f"[warn] landing router: {e}")
try:
    from data_import import router as _import_router
    app.include_router(_import_router)
except Exception as e:
    print(f"[warn] data_import router: {e}")

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
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=Inter:wght@400;500;600;700;800&display=swap');
:root{
  --ink:#0a0a0f; --text:#0a0a0f; --body:#4a4a55; --muted:#8a8a95;
  --line:#eaeaef; --line2:#f3f3f6;
  --bg:#fafafa; --card:#ffffff; --card2:#fafafa;
  --accent:#d64583; --accent-soft:#fdf0f7; --accent-dark:#b03468;
  --green:#22c55e; --amber:#f59e0b; --red:#ef4444; --blue:#3b82f6;
  --shadow-sm:0 1px 3px rgba(10,10,15,.04),0 2px 8px rgba(10,10,15,.04);
  --shadow-md:0 4px 12px rgba(10,10,15,.06),0 12px 32px rgba(10,10,15,.06);
  --table-free:#ffffff; --t-ok:#ffffff; --t-warn:#fff7ed; --t-over:#fef2f2; --t-paid:#eff6ff;
}
*{box-sizing:border-box;font-family:'Inter','Noto Sans JP',-apple-system,system-ui,Segoe UI,Roboto,sans-serif}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased}
header{position:sticky;top:0;z-index:40;display:flex;gap:12px;align-items:center;padding:14px 18px;border-bottom:1px solid var(--line);background:rgba(255,255,255,.92);backdrop-filter:blur(20px);min-height:72px}
header h1{margin:0 10px 0 0;font-size:18px;font-weight:800;letter-spacing:.02em;background:linear-gradient(135deg,var(--ink),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
select,input{font-size:14px;padding:9px 12px;border-radius:10px;border:1px solid var(--line);background:#fff;color:var(--text);outline:none;transition:all .2s;font-family:inherit}
select:focus,input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.btn{cursor:pointer;font-size:13px;font-weight:600;padding:9px 14px;border-radius:10px;border:1px solid var(--line);background:#fff;color:var(--ink);font-family:inherit;transition:all .2s}
.btn:hover{border-color:var(--accent);color:var(--accent);transform:translateY(-1px);box-shadow:var(--shadow-sm)}
.btn.solid{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.solid:hover{background:var(--accent-dark);border-color:var(--accent-dark);color:#fff}
.page{display:grid;grid-template-columns:1fr 440px;gap:18px;padding:18px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden;box-shadow:var(--shadow-sm)}
.panel h2{margin:0;padding:14px 18px;border-bottom:1px solid var(--line);font-size:14px;font-weight:700;letter-spacing:.02em}
.p{padding:14px}
.floor-wrap{position:relative;height:68vh;border-radius:12px;background:repeating-linear-gradient(45deg,#fafafa 0,#fafafa 14px,#fff 14px,#fff 28px);overflow:hidden;border:1px solid var(--line)}
.table{
  position:absolute;min-width:140px;min-height:86px;border:2px solid var(--line);border-radius:14px;
  padding:10px 12px;cursor:pointer;user-select:none;background:#fff;color:var(--ink);
  box-shadow:0 4px 12px rgba(10,10,15,.06);
  transition:transform .2s,box-shadow .2s,border-color .2s;
}
.table:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(214,69,131,.12)}
.table.sel{border-color:var(--accent);box-shadow:0 0 0 4px var(--accent-soft),0 8px 24px rgba(214,69,131,.18)}
.table .name{font-size:18px;font-weight:800;letter-spacing:-.01em}
.table .small{font-size:12px;color:var(--muted)}
.table .ttime{font-size:16px;font-weight:700;margin-top:4px;letter-spacing:-.01em;font-family:'Inter',sans-serif}
.table .tnomi{font-size:10px;margin-top:2px;color:var(--accent);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;letter-spacing:.3px}
.table.t-free{background:#fff;color:var(--ink);border-color:var(--line)}
.table.t-free .name{color:var(--muted)}
.table.t-ok{background:#fff;color:var(--ink);border-color:#bbf7d0}
.table.t-ok::before{content:'';position:absolute;top:-2px;left:-2px;right:-2px;height:4px;background:var(--green);border-radius:14px 14px 0 0}
.table.t-warn{background:var(--t-warn);color:var(--ink);border-color:#fed7aa}
.table.t-warn::before{content:'';position:absolute;top:-2px;left:-2px;right:-2px;height:4px;background:var(--amber);border-radius:14px 14px 0 0}
.table.t-warn .ttime{color:var(--amber)}
.table.t-over{background:var(--t-over);color:var(--ink);border-color:#fecaca}
.table.t-over::before{content:'';position:absolute;top:-2px;left:-2px;right:-2px;height:4px;background:var(--red);border-radius:14px 14px 0 0}
.table.t-over .ttime{color:var(--red)}
.table.t-paid{background:var(--t-paid);color:var(--ink);border-color:#bfdbfe}
.table.t-paid::before{content:'';position:absolute;top:-2px;left:-2px;right:-2px;height:4px;background:var(--blue);border-radius:14px 14px 0 0}
.side{display:flex;flex-direction:column;gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow-sm)}
.card h3{margin:0;padding:12px 16px;border-bottom:1px solid var(--line);font-size:13px;font-weight:700;color:var(--ink);letter-spacing:.02em}
.card .cbody{padding:14px 16px}
.tabs{display:flex;border-bottom:1px solid var(--line);background:#fff}
.tab{flex:1;text-align:center;padding:12px 8px;cursor:pointer;border-bottom:2px solid transparent;font-size:13px;font-weight:600;color:var(--muted);transition:all .2s}
.tab:hover{color:var(--accent);background:var(--accent-soft)}
.tab.active{border-color:var(--accent);color:var(--accent);background:var(--accent-soft);font-weight:700}
.tabpanes>div{display:none}.tabpanes>div.active{display:block}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.bigbtn{cursor:pointer;font-size:14px;font-weight:600;min-height:48px;padding:11px 14px;border-radius:12px;border:1px solid var(--line);background:#fff;color:var(--ink);width:100%;text-align:left;transition:all .2s;font-family:inherit}
.bigbtn:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-soft);transform:translateY(-1px);box-shadow:var(--shadow-sm)}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.kv{display:flex;justify-content:space-between;gap:8px;margin:6px 0;font-size:13px}
.kv b,.kv strong{color:var(--ink)}
.mono{font-family:'Inter','JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
.mono.discount{color:var(--green)}
.muted{color:var(--muted)}
hr{border:0;border-top:1px solid var(--line);margin:10px 0}
#toasts{position:fixed;right:18px;bottom:18px;display:flex;flex-direction:column;gap:8px;z-index:60}
.toast{min-width:260px;max-width:420px;border-radius:12px;padding:12px 14px;border:1px solid var(--line);background:#fff;color:var(--ink);box-shadow:0 12px 32px rgba(10,10,15,.12);animation:slide .22s ease-out}
.toast.ok{border-color:#86efac;background:#f0fdf4;color:#14532d}
.toast.err{border-color:#fca5a5;background:#fef2f2;color:#7f1d1d}
.toast .title{font-weight:700;margin-bottom:4px}
@keyframes slide{from{transform:translateY(10px);opacity:0}to{transform:translateY(0);opacity:1}}
.badge{display:inline-block;padding:3px 9px;border-radius:999px;font-size:11px;border:1px solid var(--line);background:#fff;color:var(--body);font-weight:600}
.badge.on{background:#f0fdf4;border-color:#86efac;color:#14532d}
.badge.off{background:#fef2f2;border-color:#fca5a5;color:#7f1d1d}

/* タイマーカード */
#timerCard{border:2px solid transparent;transition:border-color .3s}
#timerCard .cbody{background:linear-gradient(135deg,#fff 0%,var(--accent-soft) 100%)}

/* 数量コントローラ */
.itemRow{display:flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:10px;padding:10px;background:#fff;transition:all .2s}
.itemRow:hover{border-color:var(--accent);box-shadow:var(--shadow-sm)}
.itemName{flex:1;font-size:13px;font-weight:500}
.qtyCtrl{display:flex;align-items:center;gap:6px}
.qtyCtrl button{width:34px;height:34px;border-radius:10px;border:1px solid var(--line);background:#fff;color:var(--ink);font-size:18px;font-weight:700;cursor:pointer;transition:all .15s}
.qtyCtrl button:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
.qtyCtrl .val{min-width:28px;text-align:center;font-weight:700;font-family:'Inter',sans-serif}

/* ハンバーガーメニュー */
.hamburger{display:none;background:#fff;border:1px solid var(--line);border-radius:10px;color:var(--ink);font-size:20px;padding:6px 12px;cursor:pointer;line-height:1;transition:all .2s}
.hamburger:hover{border-color:var(--accent);color:var(--accent)}
.nav-overlay{display:none;position:fixed;inset:0;background:rgba(10,10,15,.5);backdrop-filter:blur(4px);z-index:90}
.nav-drawer{display:none;position:fixed;top:0;right:0;width:280px;height:100%;background:#fff;border-left:1px solid var(--line);z-index:91;overflow-y:auto;padding:18px;flex-direction:column;gap:6px;box-shadow:-12px 0 32px rgba(10,10,15,.1)}
.nav-drawer.open{display:flex}
.nav-overlay.open{display:block}
.nav-drawer a{display:block;padding:12px 16px;border-radius:10px;font-size:14px;font-weight:600;text-decoration:none;border:1px solid var(--line);background:#fff;color:var(--ink);transition:all .15s}
.nav-drawer a:hover,.nav-drawer a:active{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
.nav-close{background:none;border:none;color:var(--muted);font-size:24px;cursor:pointer;align-self:flex-end;padding:4px 8px}

/* iPad横タブ切替 */
.ipad-tabs{display:none;border-bottom:1px solid var(--line);background:#fff}
.ipad-tabs button{flex:1;padding:14px 8px;font-size:15px;font-weight:700;background:none;border:none;border-bottom:3px solid transparent;color:var(--muted);cursor:pointer;font-family:inherit;transition:all .2s}
.ipad-tabs button.active{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}

/* Admin nav links（ヘッダー内ナビ） */
#adminNav a{
  display:inline-block;font-size:12px !important;padding:6px 11px !important;border-radius:999px !important;
  border:1px solid var(--line) !important;text-decoration:none;font-weight:600 !important;
  background:#fff;color:var(--body) !important;transition:all .15s;
}
#adminNav a:hover{border-color:var(--accent) !important;color:var(--accent) !important;background:var(--accent-soft)}

/* iPad横向き (1024px以下) */
@media(max-width:1100px){
  .page{grid-template-columns:1fr 380px;gap:10px;padding:10px}
  .table{min-width:120px !important;min-height:75px !important}
}

/* iPad縦向き & タブレット (768px-900px) */
@media(max-width:900px){
  .hamburger{display:inline-block}
  #adminNav{display:none !important}
  header{flex-wrap:wrap;gap:8px;padding:10px 12px}
  header h1{font-size:16px}
  #tableEditBtns .btn{font-size:14px !important;padding:10px 16px !important;border-radius:10px}
  .page{grid-template-columns:1fr;gap:0;padding:0}
  .ipad-tabs{display:flex}
  .page>section.panel{display:none}
  .page>aside.side{display:none}
  .page>section.panel.ipad-active{display:block}
  .page>aside.side.ipad-active{display:flex}
  .floor-wrap{height:55vh}
  .side{gap:10px;padding:10px}
  .panel h2{padding:14px 16px;font-size:16px}
  /* タッチ最適化 */
  .bigbtn{min-height:52px;font-size:16px;padding:12px 14px}
  .tab{padding:14px 10px;font-size:16px}
  .table{min-width:130px !important;min-height:80px !important}
  .table .name{font-size:20px}
  .table .small{font-size:13px}
  .table .ttime{font-size:16px}
  .itemRow{padding:10px}
  .qtyCtrl button{width:44px;height:44px;font-size:22px;border-radius:12px}
  .qtyCtrl .val{font-size:18px;min-width:32px}
  .itemName{font-size:16px}
  select,input{font-size:16px;padding:10px 12px}
  .grid{grid-template-columns:repeat(2,1fr);gap:8px}
  .card h3{padding:12px 14px;font-size:15px}
  .card .cbody{padding:12px 14px}
  #timerRemain{font-size:36px !important}
  #payAmount{font-size:20px !important;padding:12px !important}
  .btn.solid{font-size:16px;padding:12px 16px}
}

/* スマホ (500px以下) */
@media(max-width:500px){
  .page{padding:0}
  .floor-wrap{height:40vh}
  .bigbtn{font-size:14px;padding:10px;min-height:44px}
  .table{min-width:100px !important;min-height:65px !important}
  .table .name{font-size:16px}
  .grid{grid-template-columns:1fr}
  .qtyCtrl button{width:40px;height:40px}
  header h1{font-size:14px}
}

/* ===== Premium Pink: Concept A 専用構造 ===== */
:root{
  --r:10px; --r2:16px; --r3:999px;
  --shadow-lg:0 20px 50px -20px rgba(10,10,15,.18);
}
/* ヘッダー Brand ロゴ */
.brand-wrap{display:flex;align-items:baseline;gap:10px}
.brand-wrap .brand{font-family:'Inter',sans-serif;font-weight:800;font-size:20px;letter-spacing:.03em;background:linear-gradient(135deg,var(--ink),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.brand-wrap .sub{font-size:11px;color:var(--accent);letter-spacing:.2em;font-weight:600;border-left:1px solid var(--line);padding-left:10px}
.user-chip{display:flex;align-items:center;gap:8px;padding:6px 12px;border:1px solid var(--line);border-radius:var(--r3);font-size:12px;color:var(--body);background:#fff;font-weight:600}
.user-chip .dot{width:8px;height:8px;background:var(--green);border-radius:50%;box-shadow:0 0 0 3px rgba(34,197,94,.15)}
.user-chip select,.user-chip input{border:0 !important;background:transparent !important;padding:0 4px !important;font-size:12px !important;font-weight:600 !important;width:auto !important;box-shadow:none !important}
.user-chip select:focus,.user-chip input:focus{box-shadow:none !important}
.set-toggle{cursor:pointer;background:none;border:1px solid var(--line);border-radius:var(--r3);padding:6px 12px;font-size:12px;color:var(--body);font-weight:600;transition:all .2s;font-family:inherit}
.set-toggle:hover{border-color:var(--accent);color:var(--accent)}
.set-toggle input[type=checkbox]{accent-color:var(--accent);margin-right:4px;vertical-align:middle}

/* ヒーローセクション */
.hero{padding:24px 24px 4px;display:grid;grid-template-columns:1.4fr 1fr 1fr 1fr;gap:16px}
.hero-main{background:linear-gradient(135deg,#fff 0%,var(--accent-soft) 100%);border:1px solid var(--line);border-radius:var(--r2);padding:22px 28px;position:relative;overflow:hidden;box-shadow:var(--shadow-sm)}
.hero-main::after{content:'';position:absolute;top:-30px;right:-30px;width:200px;height:200px;background:radial-gradient(circle,var(--accent) 0%,transparent 70%);opacity:.08;pointer-events:none}
.hero-main .label{font-size:11px;color:var(--accent);font-weight:700;letter-spacing:.15em;margin-bottom:6px;position:relative}
.hero-main .store{font-size:20px;font-weight:800;margin-bottom:14px;letter-spacing:-.01em;position:relative}
.hero-main .revenue{display:flex;align-items:baseline;gap:6px;position:relative}
.hero-main .revenue .yen{font-size:18px;color:var(--muted);font-weight:500}
.hero-main .revenue .amt{font-family:'Inter',sans-serif;font-size:38px;font-weight:800;letter-spacing:-.02em;background:linear-gradient(135deg,var(--ink),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hero-main .delta{display:inline-flex;align-items:center;gap:4px;font-size:11px;color:var(--green);font-weight:700;margin-top:8px;padding:3px 10px;background:rgba(34,197,94,.1);border-radius:var(--r3);position:relative}
.kpi-tile{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);padding:18px 20px;display:flex;flex-direction:column;justify-content:space-between;transition:all .25s;box-shadow:var(--shadow-sm)}
.kpi-tile:hover{transform:translateY(-2px);box-shadow:var(--shadow-md);border-color:var(--accent)}
.kpi-tile .kpi-label{font-size:10px;color:var(--muted);letter-spacing:.12em;text-transform:uppercase;font-weight:700}
.kpi-tile .kpi-val{font-family:'Inter',sans-serif;font-size:24px;font-weight:800;letter-spacing:-.01em;color:var(--ink);margin-top:6px}
.kpi-tile .kpi-sub{font-size:11px;color:var(--muted);margin-top:4px}
.kpi-tile .kpi-sub.up{color:var(--green);font-weight:700}

/* パネル */
.panel-head{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px;background:#fff}
.panel-head h2{margin:0;padding:0;border:0;font-size:14px;font-weight:700}
.panel-head .meta{margin-left:auto;font-size:12px;color:var(--muted);display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.legend{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--muted);font-weight:500}
.legend .sw{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.legend .sw.free{background:#e5e7eb;border:1px solid #cbd5e1}
.legend .sw.ok{background:var(--green)}
.legend .sw.warn{background:var(--amber)}
.legend .sw.over{background:var(--red)}
.legend .sw.paid{background:var(--blue)}

/* 選択中テーブルカード */
.selected-table-card{padding:14px 16px;background:var(--accent-soft);border:1px solid var(--accent);border-radius:var(--r);display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.selected-table-card .l .lbl{font-size:10px;color:var(--accent);font-weight:700;letter-spacing:.1em;text-transform:uppercase}
.selected-table-card .l .tn{font-family:'Inter',sans-serif;font-size:22px;font-weight:800;color:var(--ink);margin-top:2px}
.selected-table-card .l .ti{font-size:11px;color:var(--muted);margin-top:2px}
.selected-table-card .r{text-align:right}
.selected-table-card .r .tot{font-family:'Inter',sans-serif;font-size:22px;font-weight:800;color:var(--accent);letter-spacing:-.01em}
.selected-table-card .r .tl{font-size:10px;color:var(--muted);font-weight:600;letter-spacing:.1em;text-transform:uppercase}

/* 入店行 */
.input-row{display:flex;gap:10px;align-items:center;margin-bottom:12px}
.input-row .ic{flex-shrink:0;width:36px;height:36px;border-radius:var(--r);background:var(--accent-soft);color:var(--accent);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700}
.input-row select{flex:0 0 auto;min-width:80px}
.input-row .grow{flex:1}

/* ダーク決済バー */
.payment-bar{margin-top:10px;padding:14px 16px;background:#0a0a0f;border-radius:var(--r);color:#fff}
.payment-bar .pl{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.payment-bar .pl .lbl{font-size:11px;color:#bbb;letter-spacing:.1em;font-weight:700;text-transform:uppercase}
.payment-bar .pl .pamount{font-family:'Inter',sans-serif;font-size:24px;font-weight:800;color:#fff;letter-spacing:-.01em}
.payment-bar input#payAmount{flex:1;background:rgba(255,255,255,.08) !important;border:1px solid rgba(255,255,255,.15) !important;color:#fff !important;font-size:18px;padding:10px;text-align:right;font-weight:700;border-radius:8px;width:100%;margin-bottom:8px}
.payment-bar input#payAmount:focus{border-color:var(--accent) !important;box-shadow:0 0 0 3px rgba(214,69,131,.3) !important}
.pay-types{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
.pay-btn{padding:10px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);border-radius:8px;color:#fff;cursor:pointer;font-size:12px;font-weight:700;transition:all .2s;font-family:inherit}
.pay-btn:hover{background:rgba(255,255,255,.15);border-color:var(--accent);transform:translateY(-1px)}
.pay-btn.cash{background:rgba(34,197,94,.18);border-color:rgba(34,197,94,.4)}
.pay-btn.cash:hover{background:rgba(34,197,94,.28)}
.pay-btn.card{background:rgba(59,130,246,.18);border-color:rgba(59,130,246,.4)}
.pay-btn.card:hover{background:rgba(59,130,246,.28)}
.pay-btn.qr{background:rgba(168,85,247,.18);border-color:rgba(168,85,247,.4)}
.pay-btn.qr:hover{background:rgba(168,85,247,.28)}
.pay-confirm{margin-top:10px;width:100%;padding:13px;background:var(--accent);color:#fff;border:0;border-radius:8px;font-weight:800;font-size:15px;cursor:pointer;transition:all .2s;font-family:inherit;letter-spacing:.02em}
.pay-confirm:hover{background:var(--accent-dark);transform:translateY(-1px);box-shadow:0 8px 20px rgba(214,69,131,.3)}

/* 設定ポップオーバー */
#settingsPopover{display:none;position:absolute;top:64px;right:18px;background:#fff;border:1px solid var(--line);border-radius:var(--r);padding:16px;box-shadow:var(--shadow-lg);z-index:60;min-width:240px}
#settingsPopover.open{display:block}
#settingsPopover label{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:13px;color:var(--body);font-weight:600;margin-bottom:10px}
#settingsPopover label:last-child{margin-bottom:0}
#settingsPopover input,#settingsPopover select{border:1px solid var(--line);background:#fff;border-radius:8px;padding:6px 10px;font-size:13px;width:120px}

/* ヒーロー: スマホ */
@media(max-width:1100px){
  .hero{grid-template-columns:1.4fr 1fr 1fr;gap:12px;padding:14px 14px 4px}
  .hero-main .revenue .amt{font-size:32px}
  .hero .kpi-tile:nth-child(4){display:none}
}
@media(max-width:900px){
  .hero{grid-template-columns:1fr 1fr;gap:10px;padding:10px}
  .hero-main{grid-column:1/-1}
  .hero-main .revenue .amt{font-size:36px}
  .panel-head{padding:12px 14px;flex-wrap:wrap}
  .panel-head .meta{font-size:11px;gap:8px}
}
</style>
</head>
<body>

<!-- サブスク未払い警告モーダル（解約時はクローズ不可・再契約必須） -->
<div id="subModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:9999;align-items:center;justify-content:center">
<div style="background:#1a0e12;border:2px solid #ef4444;border-radius:16px;padding:32px;max-width:480px;text-align:center;box-shadow:0 0 40px rgba(239,68,68,.3)">
  <div style="font-size:48px;margin-bottom:12px">🔒</div>
  <h2 style="margin:0 0 12px;color:#fca5a5;font-size:20px">POS Start はご利用いただけません</h2>
  <p id="subModalMsg" style="color:#e5e7eb;font-size:14px;margin:0 0 8px;line-height:1.6">サブスクリプションが無効です。<br>引き続きご利用いただくには再契約が必要です。</p>
  <p style="color:#94a3b8;font-size:12px;margin:0 0 20px">ご不明点はサポートまでお問い合わせください</p>
  <a href="/ui/subscription" class="btn solid" style="text-decoration:none;font-size:15px;padding:12px 28px;display:inline-block">サブスクリプション設定へ</a>
</div></div>

<header>
  <div class="brand-wrap"><span class="brand">NEXUS</span><span class="sub">POS</span></div>
  <div id="adminNav" style="display:none;gap:4px;align-items:center;flex-wrap:wrap">
    <a href="/ui/management" target="_blank">📊 分析</a>
    <a href="/ui/closing" target="_blank">📋 締め</a>
    <a href="/ui/customers" target="_blank">👥 顧客</a>
    <a href="/ui/salary" target="_blank">💵 給与</a>
    <a href="/ui/attendance" target="_blank">🕐 出退勤</a>
    <a href="/ui/bottles" target="_blank">🍾 ボトル</a>
    <a href="/ui/tabs" target="_blank">📝 伝票</a>
    <a href="/ui/pricing" target="_blank">💰 料金</a>
    <a href="/ui/backup" target="_blank" id="backupNavLink">💾 バックアップ<span id="backupDot" style="margin-left:3px;font-size:9px;vertical-align:middle;color:#64748b">●</span></a>
    <a href="/ui/audit" target="_blank">🔍 監査</a>
    <a href="/ui/weather" target="_blank">🌤 天気</a>
    <a href="/ui/mail" target="_blank">📧 メール</a>
    <a href="/ui/subscription" target="_blank">💳 サブスク</a>
    <a href="/ui/import" target="_blank">📦 移行</a>
  </div>
  <div style="margin-left:auto;display:flex;align-items:center;gap:8px;white-space:nowrap">
    <span class="user-chip" style="font-family:'Inter',monospace;font-size:11px"><span id="selTable" class="mono" style="color:var(--accent);font-weight:800">-</span><span class="muted" style="opacity:.5">/</span>SS:<span id="selSess" class="mono">-</span></span>
    <span class="user-chip"><span class="dot"></span><select id="role" style="appearance:none">
      <option value="owner" selected>オーナー</option>
      <option value="manager">マネージャ</option>
      <option value="cashier">レジ</option>
      <option value="staff">スタッフ</option>
    </select></span>
    <button class="set-toggle" onclick="toggleSettings(event)">⚙ 設定</button>
    <button class="hamburger" id="menuBtn" onclick="toggleNavDrawer()">☰</button>
  </div>
  <div id="settingsPopover">
    <label>店舗ID <input id="storeId" type="number" value="1"></label>
    <label>配置編集モード <input id="editToggle" type="checkbox"></label>
  </div>
</header>

<!-- ナビドロワー（iPad/スマホ用） -->
<div class="nav-overlay" id="navOverlay" onclick="toggleNavDrawer()"></div>
<div class="nav-drawer" id="navDrawer">
  <button class="nav-close" onclick="toggleNavDrawer()">✕</button>
  <a href="/ui/management" style="color:#f59e0b" class="admin-link">📊 分析</a>
  <a href="/ui/closing" style="color:#22c55e" class="admin-link">📋 締め</a>
  <a href="/ui/customers" style="color:#a855f7" class="admin-link">👥 顧客台帳</a>
  <a href="/ui/bottles" style="color:#ec4899" class="admin-link">🍾 ボトルキープ</a>
  <a href="/ui/tabs" style="color:#ef4444" class="admin-link">📝 伝票</a>
  <a href="/ui/pricing" style="color:#0ea5e9" class="admin-link">💰 料金</a>
  <a href="/ui/salary" style="color:#0ea5e9" class="admin-link">💵 給与</a>
  <a href="/ui/backup" style="color:#0ea5e9" class="admin-link">💾 バックアップ</a>
  <a href="/ui/audit" style="color:#64748b" class="admin-link">🔍 監査</a>
  <a href="/ui/weather" style="color:#0ea5e9">🌤 天気</a>
  <a href="/ui/mail" style="color:#f59e0b" class="admin-link">📧 メール</a>
  <a href="/ui/attendance" style="color:#22c55e" class="admin-link">🕐 出退勤</a>
  <a href="/ui/subscription" style="color:#0ea5e9" class="admin-link">💳 サブスク</a>
  <a href="/ui/import" style="color:#f59e0b" class="admin-link">📦 データ移行</a>

</div>

<!-- iPad用フロア/操作タブ -->
<div class="ipad-tabs" id="ipadTabs">
  <button class="active" onclick="switchIpadTab('floor',this)">フロア</button>
  <button onclick="switchIpadTab('ops',this)">操作</button>
</div>

<!-- ヒーロー: 本日売上 + KPI -->
<section class="hero" id="heroSection">
  <div class="hero-main">
    <div class="label" id="heroDate">本日</div>
    <div class="store" id="heroStore">店舗 1</div>
    <div class="revenue"><span class="yen">¥</span><span class="amt" id="heroRevenue">-</span></div>
    <span class="delta" id="heroDelta" style="display:none">- 先週比</span>
  </div>
  <div class="kpi-tile">
    <div class="kpi-label">客数（組）</div>
    <div>
      <div class="kpi-val"><span id="kpiGroups">-</span><span style="font-size:13px;color:var(--muted);font-weight:500"> 組</span></div>
      <div class="kpi-sub" id="kpiGroupsSub">本日累計</div>
    </div>
  </div>
  <div class="kpi-tile">
    <div class="kpi-label">確定売上</div>
    <div>
      <div class="kpi-val" id="kpiConfirmed">-</div>
      <div class="kpi-sub up" id="kpiConfirmedSub">会計済</div>
    </div>
  </div>
  <div class="kpi-tile">
    <div class="kpi-label">稼働テーブル</div>
    <div>
      <div class="kpi-val"><span id="kpiOccupied">-</span><span style="font-size:13px;color:var(--muted);font-weight:500"> / <span id="kpiTotalTables">-</span></span></div>
      <div class="kpi-sub" id="kpiOccupancy">稼働率 -</div>
    </div>
  </div>
</section>

<div class="page">
  <section class="panel ipad-active" id="floorPanel">
    <div class="panel-head">
      <h2>🪑 フロア</h2>
      <span id="tableEditBtns" style="display:none;gap:6px;margin-left:6px">
        <button class="btn" onclick="addTable()" style="font-size:12px;padding:5px 12px;background:#f0fdf4;border-color:#86efac;color:#15803d;font-weight:700">+ テーブル追加</button>
        <button class="btn" onclick="removeTable()" style="font-size:12px;padding:5px 12px;background:#fef2f2;border-color:#fecaca;color:#b91c1c;font-weight:700">- 選択テーブル削除</button>
      </span>
      <div class="meta">
        <span class="legend"><span class="sw free"></span>空席</span>
        <span class="legend"><span class="sw ok"></span>営業中</span>
        <span class="legend"><span class="sw warn"></span>注意</span>
        <span class="legend"><span class="sw over"></span>超過</span>
        <span class="legend"><span class="sw paid"></span>会計済</span>
        <span id="floorClock" class="mono" style="font-family:'Inter',sans-serif;font-weight:700;color:var(--ink)">--:--:--</span>
      </div>
    </div>
    <div class="p"><div class="floor-wrap" id="floor"></div></div>
  </section>

  <aside class="side" id="opsPanel">
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
        <div id="timerBar" style="height:6px;border-radius:3px;background:#f3f3f6;margin-top:8px;overflow:hidden">
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
            <!-- 選択中テーブルカード -->
            <div class="selected-table-card" id="selectedTableCard">
              <div class="l">
                <div class="lbl">選択中</div>
                <div class="tn" id="stcName">-</div>
                <div class="ti" id="stcInfo">テーブルを選択してください</div>
              </div>
              <div class="r">
                <div class="tl">会計</div>
                <div class="tot" id="stcTotal">¥0</div>
              </div>
            </div>

            <!-- 入店行: 人数 + 入店ボタン -->
            <div class="input-row">
              <div class="ic">👥</div>
              <select id="guestCount">
                <option value="1">1名</option><option value="2">2名</option><option value="3" selected>3名</option>
                <option value="4">4名</option><option value="5">5名</option><option value="6">6名</option>
              </select>
              <button class="bigbtn solid grow" id="btnCheckin" style="text-align:center;font-weight:800;padding:11px 16px">入店</button>
            </div>

            <!-- 延長ボタンエリア（JS で動的に描画）-->
            <div id="extendBtnsArea" style="margin-bottom:10px"></div>

            <!-- 操作ボタングリッド -->
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:6px">
              <button class="bigbtn" id="btnAutoExtend" style="text-align:center;font-size:12px">自動延長 OFF</button>
              <button class="bigbtn" id="btnChangeGuest" style="text-align:center;font-size:12px;background:#eff6ff;border-color:#bfdbfe;color:#1d4ed8">人数変更</button>
              <button class="bigbtn" id="btnMoveTable" style="text-align:center;font-size:12px;background:#eff6ff;border-color:#bfdbfe;color:#1d4ed8">席変更</button>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:6px">
              <button class="bigbtn" id="btnChangeStart" style="text-align:center;font-size:12px;background:#eff6ff;border-color:#bfdbfe;color:#1d4ed8">時間変更</button>
              <button class="bigbtn" id="btnDouhan" style="text-align:center;font-size:12px;background:#fdf0f7;border-color:#f9a8d4;color:#b03468;font-weight:700">同伴</button>
              <button class="bigbtn" id="btnDiscount" style="text-align:center;font-size:12px;background:#f0fdf4;border-color:#86efac;color:#15803d;font-weight:700">割引</button>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:6px">
              <button class="bigbtn" id="btnNomHon" style="text-align:center;font-size:13px;background:#fdf0f7;border-color:#d64583;color:#b03468;font-weight:700">本指名+</button>
              <button class="bigbtn" id="btnNomJyonai" style="text-align:center;font-size:13px;background:#fdf0f7;border-color:#d64583;color:#b03468;font-weight:700">場内指名+</button>
            </div>
            <div style="margin-bottom:6px">
              <button class="bigbtn" id="btnCancelCheckin" style="width:100%;text-align:center;font-size:12px;color:#b91c1c;border-color:#fecaca;background:#fef2f2">入店取消</button>
            </div>

            <!-- ダーク決済バー -->
            <div class="payment-bar">
              <div class="pl"><span class="lbl">お支払い</span><span class="pamount" id="payAmountDisplay">¥0</span></div>
              <input id="payAmount" placeholder="金額を入力" type="number">
              <div class="pay-types">
                <button class="pay-btn cash" id="btnPayCash">💵 現金</button>
                <button class="pay-btn card" id="btnPayCard">💳 カード</button>
                <button class="pay-btn qr" id="btnPayQR">📱 QR</button>
              </div>
              <button class="pay-confirm" id="btnCheckout">✓ 会計確定</button>
              <button id="btnReceipt" style="margin-top:6px;width:100%;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);color:#fff;padding:9px;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer">領収書</button>
            </div>

            <!-- 伝票（内訳） -->
            <div id="billBox" class="muted mono" style="font-size:12px;line-height:1.6;margin-top:12px;padding:10px 12px;background:var(--card2);border-radius:8px;border:1px dashed var(--line)"></div>
          </div>

          <!-- 数量管理：各カテゴリ 0スタート + / - -->
          <div id="pane-drink">
            <div style="margin-bottom:10px;padding:10px 12px;border:1px solid var(--accent);border-radius:10px;background:var(--accent-soft)">
              <div style="font-size:11px;color:var(--accent);font-weight:700;letter-spacing:.05em;margin-bottom:6px">ドリンクバック対象キャスト</div>
              <select id="drinkCastSelect" style="width:100%;font-size:14px;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:#fff;color:var(--ink)">
                <option value="">なし（お客様用）</option>
              </select>
            </div>
            <div id="listDrink" class="grid"></div>
            <div class="row" style="justify-content:flex-end;margin-top:10px">
              <button class="btn solid" id="applyDrink" style="font-size:15px;padding:10px 20px">注文確定</button>
            </div>
          </div>
          <div id="pane-bottle">
            <div style="margin-bottom:10px;padding:10px 12px;border:1px solid var(--accent);border-radius:10px;background:var(--accent-soft)">
              <div style="font-size:11px;color:var(--accent);font-weight:700;letter-spacing:.05em;margin-bottom:6px">ボトルバック対象キャスト</div>
              <select id="bottleCastSelect" style="width:100%;font-size:14px;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:#fff;color:var(--ink)">
                <option value="">なし（テーブルボトル）</option>
              </select>
            </div>
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

    <!-- WS状態（最下部に控えめ表示・ヒーローKPIにデータは集約済み） -->
    <div style="text-align:center;font-size:10px;color:var(--muted);padding:4px">
      <span id="wsStatus">WS: 接続中...</span>
      <span style="display:none"><span id="salesConfirmed">-</span><span id="salesToday">-</span><span id="salesSessions">-</span></span>
    </div>
  </aside>
</div>

<div id="toasts"></div>

<script>
/* ====== 認証チェック ====== */
if (!sessionStorage.getItem('pos_auth')) {
  window.location.href = '/';
}

/* ====== ロール制御（トークンベース） ====== */
(function(){
  const tokenRole = sessionStorage.getItem('pos_role') || 'owner';
  const sel = document.getElementById('role');
  if (sel) {
    if (tokenRole === 'owner') {
      // ownerはロール切替可能
      sel.value = 'owner';
    } else {
      // staff等はロール固定・変更不可
      sel.value = tokenRole;
      sel.disabled = true;
      sel.title = 'このアカウントではロール変更できません';
    }
  }
})();

/* ====== ハンバーガーメニュー & iPad切替 ====== */
function toggleNavDrawer(){
  document.getElementById('navDrawer').classList.toggle('open');
  document.getElementById('navOverlay').classList.toggle('open');
}
function switchIpadTab(which, btn){
  const fp=document.getElementById('floorPanel');
  const op=document.getElementById('opsPanel');
  document.querySelectorAll('.ipad-tabs button').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  if(which==='floor'){
    fp.classList.add('ipad-active'); op.classList.remove('ipad-active');
  }else{
    fp.classList.remove('ipad-active'); op.classList.add('ipad-active');
  }
}
/* テーブルタップ時にiPadモードなら操作パネルに自動切替 */
function autoSwitchToOps(){
  if(window.innerWidth<=900){
    const btn=document.querySelectorAll('.ipad-tabs button')[1];
    if(btn) switchIpadTab('ops',btn);
  }
}

/* ====== テーブル追加/削除 ====== */
async function addTable(){
  const name = prompt('テーブル名を入力（例: T-3）');
  if(!name) return;
  try{
    const r = await api('/tables',{method:'POST',body:{store_id:store(),name:name,capacity:6,x:10,y:10}});
    toast('テーブル追加',`${name} を追加しました`,'ok');
    await loadFloor();
  }catch(e){
    toast('エラー',e.message||'追加に失敗しました','err');
  }
}
async function removeTable(){
  if(!selectedTableId){toast('エラー','削除するテーブルを選択してください','err');return;}
  const tbl = floorModel.tables.find(t=>t.id===selectedTableId);
  const tname = tbl?tbl.name:'テーブル';
  if(!confirm(`${tname} を削除しますか？\n※使用中のテーブルは削除できません`)) return;
  try{
    await api(`/tables/${selectedTableId}`,{method:'DELETE'});
    toast('テーブル削除',`${tname} を削除しました`,'ok');
    selectedTableId=null; $('selTable').textContent='-';
    await loadFloor();
  }catch(e){
    toast('エラー',e.message||'削除に失敗しました','err');
  }
}

/* ====== 共通 ====== */
const $ = (id)=>document.getElementById(id);
const role = ()=> $('role').value;
const store = ()=> parseInt($('storeId').value||'1',10);

/* 管理ナビの表示切替（owner/managerのみ） */
function updateAdminNav(){
  const nav=$('adminNav');
  if(!nav)return;
  const tokenRole = sessionStorage.getItem('pos_role')||'owner';
  const r = role();
  // staffトークンの場合は管理ナビを常に非表示
  if(tokenRole==='staff'){
    nav.style.display='none';
    return;
  }
  nav.style.display=(r==='owner'||r==='manager')?'flex':'none';
}
/* バックアップ稼働状態を取得してドット表示を更新 */
async function loadBackupStatus(){
  const dot=$('backupDot');
  if(!dot)return;
  try{
    const r=await api('/backup/auto/status');
    if(!r)return;
    if(r.running){
      dot.style.color='#22c55e';
      dot.title=`自動バックアップ稼働中（${r.interval_minutes}分ごと）`;
    }else{
      dot.style.color='#ef4444';
      dot.title='自動バックアップ停止中 — バックアップ画面から開始できます';
    }
    /* 最新バックアップ時刻も取得して title に追記 */
    const bl=await api('/backup/list');
    if(bl&&bl.length>0){
      const last=bl[0];
      dot.title+=' | 最終: '+last.created;
    }
  }catch(e){}
}
document.addEventListener('DOMContentLoaded',()=>{
  updateAdminNav();
  $('role')?.addEventListener('change', updateAdminNav);
  /* 配置編集チェックボックス */
  const et=$('editToggle');
  if(et) et.addEventListener('change',()=>{
    const btns=$('tableEditBtns');
    if(btns) btns.style.display=et.checked?'inline':'none';
  });
  /* staffトークンは管理機能を非表示 */
  const tokenRole = sessionStorage.getItem('pos_role')||'owner';
  if(tokenRole==='staff'){
    const editLabel = et?.closest('label');
    if(editLabel) editLabel.style.display='none';
    /* ナビドロワーの管理リンクも非表示 */
    document.querySelectorAll('.admin-link').forEach(a=>a.style.display='none');
  }
  /* バックアップ状態を初回ロード＋5分ごとに更新 */
  loadBackupStatus();
  setInterval(loadBackupStatus, 5*60*1000);
  /* アップデート通知バナー表示 */
  checkUpdateNotice();
});

/* アップデート通知バナー */
async function checkUpdateNotice(){
  try{
    const r = await fetch('/api/version');
    if(!r.ok) return;
    const v = await r.json();
    if(!v.update_available) return;
    /* 既に表示済みなら再表示しない（同じバージョンを24h dismiss） */
    const dismissedFor = localStorage.getItem('update_dismiss_for');
    if(dismissedFor === v.latest_version) return;
    /* バナー生成 */
    const banner = document.createElement('div');
    banner.id = 'updateBanner';
    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:10000;background:linear-gradient(135deg,#0ea5e9,#0284c7);color:#fff;padding:10px 14px;display:flex;justify-content:center;align-items:center;gap:14px;font-size:13px;box-shadow:0 4px 12px rgba(0,0,0,.2);flex-wrap:wrap';
    banner.innerHTML = `
      <span>📢 新しいバージョン <strong>v${v.latest_version}</strong> が利用可能です（現在 v${v.current}）</span>
      <a href="${v.download_url}" target="_blank" style="background:#fff;color:#0284c7;padding:5px 14px;border-radius:6px;text-decoration:none;font-weight:700">ダウンロード</a>
      <button onclick="dismissUpdate('${v.latest_version}')" style="background:transparent;color:#fff;border:1px solid rgba(255,255,255,.5);padding:5px 12px;border-radius:6px;cursor:pointer;font-size:12px">後で</button>
    `;
    document.body.insertBefore(banner, document.body.firstChild);
    /* ヘッダーが隠れないようにbodyに余白 */
    document.body.style.paddingTop = (banner.offsetHeight + 4) + 'px';
  }catch(e){ /* ignore */ }
}
function dismissUpdate(version){
  localStorage.setItem('update_dismiss_for', version);
  const b = document.getElementById('updateBanner');
  if(b){ b.remove(); document.body.style.paddingTop = ''; }
}

let selectedTableId = null;
let currentSessionId = null;
let currentBill = null;
let loops = { tick:null, bill:null, sales:null, floor:null, floorTick:null };

const floorModel = {tables:[], tableEls:{}, sessionByTable:{}, billBySession:{}};
const qtyState = { drink:{}, bottle:{}, food:{} }; // itemId: qty（0スタート）

/* 店舗セット時間設定（起動時に取得） */
let storeTimeConfig = { set_minutes: 60, extend_unit: 30, set_plans: [], extend_options: [] };
async function loadStoreTimeConfig(){
  try {
    const r = await api(`/settings/store-time/${store()}`);
    if(r) storeTimeConfig = r;
  } catch(e){ /* デフォルト値を使用 */ }
  renderExtendButtons();
}

/* 延長ボタン描画（設定に応じてシングル/マルチ） */
function renderExtendButtons(){
  const area = $('extendBtnsArea');
  if (!area) return;
  const opts = (storeTimeConfig.extend_options || []).filter(o => o.is_active);
  if (opts.length === 0) {
    const eu = storeTimeConfig.extend_unit || 30;
    area.innerHTML = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
      <button class="bigbtn" id="btnExtend30" style="text-align:center;font-size:14px">延長 +${eu}分</button>
      <button class="bigbtn" id="btnUnextend" style="text-align:center;font-size:14px">延長取消 -${eu}分</button></div>`;
    $('btnExtend30').onclick = () => extendDefault().catch(e=>toast(e.message,'err'));
    $('btnUnextend').onclick  = () => unextend30().catch(e=>toast(e.message,'err'));
  } else {
    const cols = Math.min(opts.length, 3);
    const btns = opts.map(o =>
      `<button class="bigbtn" style="text-align:center;font-size:12px;padding:6px 4px"
         onclick="extendWithOption(${o.id},${o.minutes},${o.price},'${(o.label||'').replace(/'/g,'\\\'')}')" >
         延長 ${o.label}<br><small style="color:#8a8a95;font-weight:500">¥${Math.round(o.price).toLocaleString()}/人</small></button>`
    ).join('');
    area.innerHTML = `<div style="display:grid;grid-template-columns:repeat(${cols},1fr);gap:6px;margin-bottom:4px">${btns}</div>
      <button class="bigbtn" id="btnUnextend" style="width:100%;text-align:center;font-size:12px">延長取消</button>`;
    $('btnUnextend').onclick = () => unextend30().catch(e=>toast(e.message,'err'));
  }
}

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
  const tk = sessionStorage.getItem('pos_token')||'';
  const headers = {'Content-Type':'application/json','X-Role': role(), 'X-Token': tk};
  const o = Object.assign({method:'GET', headers}, opt);
  if (o.body && typeof o.body !== 'string') o.body = JSON.stringify(o.body);
  const res = await fetch(path, o);
  if (res.status===401) { sessionStorage.clear(); window.location.href='/'; return; }
  if (res.status===402) { window.location.href='/ui/subscription'; return; }
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
      const nomiText=nomiTagsText(b);
      center=`<div class="ttime mono" id="ttime-${t.id}">${remain>=0?`残り ${remain}分`:`超過 ${Math.abs(remain)}分`}</div>
              <div class="small mono" id="tdetail-${t.id}">${guests}名 ¥${total.toLocaleString()}</div>
              <div class="tnomi" id="tnomi-${t.id}">${nomiText}</div>`;
      el.className='table '+colorClass(remain,b);
    }
    el.innerHTML=`<div class="name">${t.name}</div>${center}`
      + (s ? '' : `<div class="tnomi" id="tnomi-${t.id}"></div>`);

    el.addEventListener('click', async ()=>{
      selectedTableId=t.id;
      $('selTable').textContent=t.name;
      document.querySelectorAll('.table').forEach(x=>x.classList.remove('sel'));
      el.classList.add('sel');
      autoSwitchToOps();

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
    selectedTableId=tables[0].id; $('selTable').textContent=tables[0].name;
  }
  if(selectedTableId){
    const selEl=$('table-'+selectedTableId);
    if(selEl) selEl.classList.add('sel');
  }
  // ヒーローKPI（稼働テーブル）と選択カードを更新
  try{ updateSelectedTableCard(); }catch{}
  try{ if(typeof refreshSales==='function') refreshSales(); }catch{}
}

/* 指名タグを "D:ゆかり　A:なな" 形式の文字列に変換 */
function nomiTagsText(b){
  if(!b||!b.nomi_tags||!b.nomi_tags.length) return '';
  return b.nomi_tags.map(n=>`${n.prefix}:${n.cast_name}`).join('　');
}

/* 毎秒更新（卓カード） */
function floorTick(){
  Object.entries(floorModel.sessionByTable).forEach(([tid,sid])=>{
    const el=floorModel.tableEls[tid]; const b=floorModel.billBySession[sid]; if(!el||!b) return;
    const elapsed=liveElapsed(b.elapsed_minutes ?? b?.time_breakdown?.total_minutes ?? 0, b._fetchedAt);
    const booked=b.booked_minutes ?? 60;
    const remain=booked-elapsed;
    const tt=$('ttime-'+tid), td=$('tdetail-'+tid), tn=$('tnomi-'+tid);
    const guests=b.guest_count||1;
    const total=Math.round(b.total||0);
    if(tt) tt.textContent= remain>=0?`残り ${remain}分`:`超過 ${Math.abs(remain)}分`;
    if(td) td.textContent= `${guests}名 ¥${total.toLocaleString()}`;
    if(tn) tn.textContent= nomiTagsText(b);
    el.classList.remove('t-free','t-ok','t-warn','t-over','t-paid');
    el.classList.add(colorClass(remain,b));
  });
}

/* キャスト読み込み（ドリンクバック用セレクト） */
async function loadCasts(){
  try{
    const casts = await api(`/casts?store_id=${store()}`);
    const selDrink = $('drinkCastSelect');
    const selBottle = $('bottleCastSelect');
    if(selDrink){
      selDrink.innerHTML='<option value="">なし（お客様用）</option>';
      casts.forEach(c=>{ selDrink.innerHTML+=`<option value="${c.id}">${c.name}</option>`; });
    }
    if(selBottle){
      selBottle.innerHTML='<option value="">なし（テーブルボトル）</option>';
      casts.forEach(c=>{ selBottle.innerHTML+=`<option value="${c.id}">${c.name}</option>`; });
    }
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

  // ドリンク/ボトルの場合はキャスト選択を取得
  let castId = null;
  if (cat === 'drink') {
    const sel = $('drinkCastSelect');
    castId = sel ? (parseInt(sel.value) || null) : null;
  } else if (cat === 'bottle') {
    const sel = $('bottleCastSelect');
    castId = sel ? (parseInt(sel.value) || null) : null;
  }

  // キャスト未選択の場合に確認ダイアログ
  if ((cat === 'drink' || cat === 'bottle') && !castId) {
    const label = cat === 'drink' ? 'ドリンク' : 'ボトル';
    if (!confirm(`⚠️ キャストが選択されていません。\nバックなし（お店売上）として${label}を注文しますか？`)) return;
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
  const plans=(storeTimeConfig.set_plans||[]).filter(p=>p.is_active);
  if(plans.length>0){
    showSetPlanModal(gc, plans);
  } else {
    await _doCheckin(gc, storeTimeConfig.set_minutes, storeTimeConfig.extend_unit, null);
  }
}
async function _doCheckin(gc, setMin, extUnit, setFeeOverride){
  const s = await api('/sessions',{method:'POST', body:{store_id:store(), table_id:selectedTableId, guest_count:gc, set_minutes:setMin, extend_unit:extUnit, set_fee_override:setFeeOverride}});
  currentSessionId=s.id; $('selSess').textContent=s.id;
  autoExtendBySession[s.id]=false; reflectAutoExtendBtn();
  toast('入店しました'); await refreshBill(); await loadFloor(); refreshSales(); startLoops();
}

/* コース選択モーダル */
let _planCheckinGc=1;
function showSetPlanModal(gc, plans){
  _planCheckinGc=gc;
  let modal=$('setPlanModal');
  if(!modal){
    modal=document.createElement('div');
    modal.id='setPlanModal';
    modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:300;display:flex;align-items:center;justify-content:center';
    modal.innerHTML=`<div style="background:#ffffff;border:1px solid #eaeaef;border-radius:16px;padding:24px;min-width:300px;max-width:420px;width:90%;box-shadow:0 20px 50px rgba(10,10,15,.18)">
      <h3 style="margin:0 0 16px;font-size:16px;color:#0a0a0f;font-weight:700">コース選択</h3>
      <div id="setPlanBtns" style="display:flex;flex-direction:column;gap:10px"></div>
      <button onclick="document.getElementById('setPlanModal').style.display='none'" style="margin-top:14px;width:100%;padding:10px;border-radius:8px;border:1px solid #eaeaef;background:#fff;color:#4a4a55;cursor:pointer;font-size:14px;font-weight:600">キャンセル</button>
    </div>`;
    document.body.appendChild(modal);
  }
  $('setPlanBtns').innerHTML=plans.map(p=>
    `<button onclick="checkinWithPlan(${p.id})"
      style="padding:14px 16px;border-radius:10px;border:1px solid #d64583;background:#fdf0f7;color:#0a0a0f;cursor:pointer;font-size:15px;text-align:left;width:100%;font-weight:600;transition:all .2s">
      <div style="font-weight:700;color:#0a0a0f">${p.label}</div>
      <div style="font-size:13px;color:#b03468;margin-top:3px;font-weight:500">${p.minutes}分 · ¥${Math.round(p.price).toLocaleString()}/人</div>
    </button>`).join('');
  modal.style.display='flex';
}
async function checkinWithPlan(planId){
  document.getElementById('setPlanModal').style.display='none';
  const plan=(storeTimeConfig.set_plans||[]).find(p=>p.id===planId);
  if(!plan) return toast('コースが見つかりません','err');
  try{
    await _doCheckin(_planCheckinGc, plan.minutes, storeTimeConfig.extend_unit, plan.price);
  }catch(e){toast(e.message,'err')}
}

async function extendDefault(){
  if (!currentSessionId) throw new Error('セッションがありません');
  await api(`/sessions/${currentSessionId}/extend`, {method:'POST'});
  const eu = storeTimeConfig.extend_unit||30;
  toast(`延長 +${eu}分`); await refreshBill(); await loadFloor(); refreshSales();
}
async function extendWithOption(optId, minutes, price, label){
  if (!currentSessionId) throw new Error('セッションがありません');
  await api(`/sessions/${currentSessionId}/extend?option_id=${optId}`, {method:'POST'});
  toast(`延長 ${label}`); await refreshBill(); await loadFloor(); refreshSales();
}
async function extend30(){
  // 後方互換（autoExtendTick から呼ばれる）
  await extendDefault();
}
async function unextend30(){
  if (!currentSessionId) throw new Error('セッションがありません');
  try{
    await api(`/sessions/${currentSessionId}/unextend`, {method:'POST'});
    toast('延長取消しました');
  }catch(e){
    toast(e.message,'err');
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
  renderTimer(null); renderBill(null);
}

async function payCash(amount){
  if (!currentSessionId) throw new Error('セッションがありません');
  await api(`/sessions/${currentSessionId}/payments`,{method:'POST', body:{store_id:store(), method:'cash', amount:Number(amount)}});
  toast('支払いを記録しました'); $('payAmount').value=''; await refreshBill(); await loadFloor(); refreshSales();
}
async function checkout(){
  if (!currentSessionId) throw new Error('セッションがありません');
  const sid = currentSessionId; // 会計前に退避
  try{ await api(`/sessions/${sid}/checkout`, {method:'POST'}); }
  catch(e){ console.warn(e); toast('会計API未実装の可能性（UIは続行）','err'); }
  // 会計確定後に領収書ポップアップを自動表示
  try{
    const d = await api(`/sessions/${sid}/receipt`);
    openReceiptWindow(d);
    toast('会計を確定しました — 領収書を表示しました');
  }catch(e){
    toast('会計を確定しました');
  }
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

/* 本指名 / 場内指名登録 */
async function recordNomination(nomiType){
  if (!currentSessionId) return toast('テーブルを選択してください','err');
  let casts;
  try{ casts = await api(`/casts?store_id=${store()}`); }
  catch{ return toast('キャスト取得に失敗しました','err'); }
  if(!casts||!casts.length) return toast('キャストが登録されていません','err');

  const label = nomiType === 'hon' ? '本指名' : '場内指名';
  const options = casts.map(c=>`${c.id}: ${c.name}${c.is_on_duty===false ? ' ⛔退勤中' : ''}`).join('\n');
  const input = prompt(`${label}キャストを選択\n番号を入力してください:\n（⛔退勤中のキャストは指名できません）\n\n${options}`);
  if(!input) return;
  const castId = parseInt(input.split(':')[0], 10);
  if(isNaN(castId)) return toast('正しい番号を入力してください','err');
  const cast = casts.find(c=>c.id===castId);
  if(!cast) return toast('該当するキャストが見つかりません','err');
  // 退勤中チェック（フロントでも事前ブロック）
  if(cast.is_on_duty === false){
    alert(`⛔ ${cast.name} さんは退勤中のため指名できません。\n\n先に「出退勤」画面から出勤打刻をしてください。`);
    return;
  }

  try{
    await api(`/sessions/${currentSessionId}/nominations`, {method:'POST', body:{cast_id: castId, nomi_type: nomiType}});
    toast(`${label}: ${cast.name}`, `セッション ${currentSessionId} に${label}を登録しました`, 'ok');
    await refreshBill(); await loadFloor();
  }catch(e){
    toast(`${label}登録エラー`, e.message||'登録に失敗しました','err');
  }
}

/* 同伴登録 */
async function recordDouhan(){
  if (!currentSessionId) return toast('テーブルを選択してください','err');
  // キャスト一覧を取得
  let casts;
  try{ casts = await api(`/casts?store_id=${store()}`); }
  catch{ return toast('キャスト取得に失敗しました','err'); }
  if(!casts||!casts.length) return toast('キャストが登録されていません','err');

  // 選択ダイアログ生成
  const options = casts.map(c=>`${c.id}: ${c.name}${c.is_on_duty===false ? ' ⛔退勤中' : ''}`).join('\n');
  const input = prompt(`同伴キャストを選択\n番号を入力してください:\n（⛔退勤中のキャストは登録できません）\n\n${options}`);
  if(!input) return;
  const castId = parseInt(input.split(':')[0], 10);
  if(isNaN(castId)) return toast('正しい番号を入力してください','err');
  const cast = casts.find(c=>c.id===castId);
  if(!cast) return toast('該当するキャストが見つかりません','err');
  // 退勤中チェック
  if(cast.is_on_duty === false){
    alert(`⛔ ${cast.name} さんは退勤中のため同伴登録できません。\n\n先に「出退勤」画面から出勤打刻をしてください。`);
    return;
  }

  try{
    await api(`/sessions/${currentSessionId}/douhan`, {method:'POST', body:{cast_id: castId}});
    toast(`同伴: ${cast.name}`, `セッション ${currentSessionId} に同伴登録しました`, 'ok');
    await refreshBill(); await loadFloor();
  }catch(e){
    toast('同伴登録エラー', e.message||'登録に失敗しました','err');
  }
}

/* 割引適用 */
let _discountRules = [];
async function loadDiscountRules(){
  try{ _discountRules = await api(`/settings/pricing/${store()}/discounts`)||[]; }catch{}
}
async function applyDiscount(){
  if(!currentSessionId) return toast('テーブルを選択してください','err');
  if(!_discountRules.length) await loadDiscountRules();
  const active = _discountRules.filter(d=>d.is_active);
  if(!active.length) return toast('割引ルールが登録されていません\n料金設定から追加してください','err');

  // 現在の割引状態を確認
  const hasDisc = currentBill && currentBill.discount_amount > 0;
  const typeLabels = {fixed:'固定値引き', rate:'割引率', set_override:'セット料金変更', free_drink:'ドリンク無料'};
  const options = active.map((d,i)=>{
    let desc = d.label || '割引';
    if(d.disc_type==='fixed') desc += ` (¥${d.value.toLocaleString()}引き)`;
    else if(d.disc_type==='rate') desc += ` (${Math.round(d.value*100)}%OFF)`;
    else if(d.disc_type==='set_override') desc += ` (セット¥${d.value.toLocaleString()})`;
    else if(d.disc_type==='free_drink') desc += ` (${d.value}杯無料)`;
    return `${i+1}: ${desc}`;
  }).join('\n');

  const msg = hasDisc
    ? `現在「${currentBill.discount_label}」適用中\n\n0: 割引解除\n${options}\n\n番号を入力:`
    : `割引を選択:\n\n${options}\n\n番号を入力:`;
  const input = prompt(msg);
  if(input===null) return;
  const idx = parseInt(input,10);

  if(idx===0 && hasDisc){
    try{
      await api(`/sessions/${currentSessionId}/discount`,{method:'DELETE'});
      toast('割引を解除しました'); await refreshBill(); await loadFloor(); refreshSales();
    }catch(e){ toast('割引解除エラー',e.message,'err'); }
    return;
  }
  const disc = active[idx-1];
  if(!disc) return toast('正しい番号を入力してください','err');
  try{
    await api(`/sessions/${currentSessionId}/discount`,{method:'POST',body:{label:disc.label,disc_type:disc.disc_type,value:disc.value}});
    toast(`割引適用: ${disc.label}`); await refreshBill(); await loadFloor(); refreshSales();
  }catch(e){ toast('割引適用エラー',e.message,'err'); }
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
  if (b.nomination_fee > 0) {
    (b.nominations||[]).forEach(n => lines.push(row(n.label, toYen(n.amount), 'accent')));
  }
  if (b.night_surcharge > 0) lines.push(row('深夜加算', toYen(b.night_surcharge)));
  if (b.discount_amount > 0) lines.push(row(`割引（${b.discount_label||''}）`, '-'+toYen(b.discount_amount), 'discount'));
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
  // 選択カードの会計表示
  const stcTotal=document.getElementById('stcTotal');
  if(stcTotal){ stcTotal.textContent='¥'+(b.due>0?b.due:b.total||0).toLocaleString(); }
  try{ updatePayDisplay(); }catch{}
}

/* 今日の売上（注文/取消/会計で即時反映） + ヒーロー/KPI 更新 */
async function refreshSales(){
  try{
    const d=await api(`/closing?store_id=${store()}`);
    const conf=(d.confirmed_sales||0);
    const total=(d.total_sales||0);
    const closed=d.closed_count||0;
    const open=d.open_count||0;
    // 既存（非表示）
    $('salesConfirmed').textContent=conf.toLocaleString()+' 円';
    $('salesToday').textContent=total.toLocaleString()+' 円';
    $('salesSessions').textContent=`${closed}組確定 / ${open}組open`;
    // ヒーロー
    const heroRev=$('heroRevenue'); if(heroRev) heroRev.textContent=total.toLocaleString();
    const kpiGroups=$('kpiGroups'); if(kpiGroups) kpiGroups.textContent=String(closed+open);
    const kpiGroupsSub=$('kpiGroupsSub'); if(kpiGroupsSub) kpiGroupsSub.textContent=`確定 ${closed} / 営業中 ${open}`;
    const kpiConfirmed=$('kpiConfirmed'); if(kpiConfirmed) kpiConfirmed.textContent='¥'+conf.toLocaleString();
    const tot=floorModel && floorModel.tables ? floorModel.tables.length : 0;
    const occ=floorModel && floorModel.sessions ? floorModel.sessions.filter(s=>s.status!=='paid'&&s.status!=='cancelled').length : 0;
    const kpiOcc=$('kpiOccupied'); if(kpiOcc) kpiOcc.textContent=String(occ);
    const kpiTot=$('kpiTotalTables'); if(kpiTot) kpiTot.textContent=String(tot);
    const kpiOccRate=$('kpiOccupancy'); if(kpiOccRate) kpiOccRate.textContent=tot>0?`稼働率 ${Math.round(occ/tot*100)}%`:'稼働率 -';
    // 日付ラベル
    const heroDate=$('heroDate'); if(heroDate){ const d2=new Date(); heroDate.textContent=`本日 · ${d2.getFullYear()}.${String(d2.getMonth()+1).padStart(2,'0')}.${String(d2.getDate()).padStart(2,'0')} (${'日月火水木金土'[d2.getDay()]})`; }
    // 店舗名
    const heroStore=$('heroStore'); if(heroStore) heroStore.textContent=`店舗 ${store()}`;
  }catch{}
}

/* 設定ポップオーバー */
function toggleSettings(ev){
  if(ev){ ev.stopPropagation(); }
  const p=document.getElementById('settingsPopover'); if(!p) return;
  p.classList.toggle('open');
  if(p.classList.contains('open')){
    setTimeout(()=>{
      document.addEventListener('click', function _h(e){
        if(!p.contains(e.target) && !e.target.closest('.set-toggle')){ p.classList.remove('open'); document.removeEventListener('click',_h); }
      });
    },10);
  }
}

/* 選択中テーブルカード & 決済バー金額表示の更新 */
function updateSelectedTableCard(){
  const stcName=document.getElementById('stcName');
  const stcInfo=document.getElementById('stcInfo');
  const stcTotal=document.getElementById('stcTotal');
  if(!stcName) return;
  if(!selectedTableId){ stcName.textContent='-'; stcInfo.textContent='テーブルを選択してください'; stcTotal.textContent='¥0'; return; }
  const tbl=(floorModel.tables||[]).find(t=>t.id===selectedTableId);
  const sess=(floorModel.sessions||[]).find(s=>s.table_id===selectedTableId && s.status!=='paid'&&s.status!=='cancelled');
  stcName.textContent=tbl?tbl.name:('T'+selectedTableId);
  if(sess){
    const elapsed=Math.floor((Date.now()-new Date(sess.started_at).getTime())/60000);
    stcInfo.textContent=`${sess.guest_count}名 · ${new Date(sess.started_at).toTimeString().slice(0,5)}開始 · 経過 ${elapsed}分`;
  }else{
    stcInfo.textContent='空席';
  }
}
function updatePayDisplay(){
  const inp=document.getElementById('payAmount');
  const disp=document.getElementById('payAmountDisplay');
  if(!inp||!disp) return;
  const v=parseInt(inp.value||'0',10);
  disp.textContent='¥'+(isNaN(v)?0:v).toLocaleString();
}
document.addEventListener('DOMContentLoaded',()=>{
  const inp=document.getElementById('payAmount');
  if(inp){ inp.addEventListener('input',updatePayDisplay); }
});

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
        if(['order','cancel_order','payment','checkout','cancel_session','extend','checkin','guest_count','move_table','start_time','douhan','nomination'].includes(msg.event)){
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
  loops.tick=setInterval(()=>{ if(currentBill&&currentSessionId) renderTimer(currentBill); else renderTimer(null); autoExtendTick(); updateFloorClock(); },1000);
  loops.bill=setInterval(()=>{ if(currentSessionId) refreshBill().catch(()=>{}); },5000);
  loops.sales=setInterval(refreshSales,10000); // WS補完用（10秒ごと）
  loops.floor=setInterval(()=>loadFloor().catch(()=>{}),5000);
  loops.floorTick=setInterval(floorTick,1000);
}

/* 初期化 */
async function initUI(){
  // 入店/延長関係
  $('btnCheckin').addEventListener('click', ()=>checkin().catch(e=>toast(e.message,'err')));
  // btnExtend30 / btnUnextend は renderExtendButtons() で動的に生成・バインドされる
  $('btnAutoExtend').addEventListener('click', toggleAutoExtend);
  $('btnCancelCheckin').addEventListener('click', ()=>{
    if(!currentSessionId) return toast('セッションがありません','err');
    if(!confirm('この入店を取り消しますか？注文も全て削除されます。')) return;
    cancelCheckin().catch(e=>toast(e.message,'err'));
  });

  // 人数変更・席変更・スタート時間変更・同伴
  $('btnChangeGuest').addEventListener('click', ()=>changeGuestCount().catch(e=>toast(e.message,'err')));
  $('btnMoveTable').addEventListener('click', ()=>moveTable().catch(e=>toast(e.message,'err')));
  $('btnChangeStart').addEventListener('click', ()=>changeStartTime().catch(e=>toast(e.message,'err')));
  $('btnDouhan').addEventListener('click', ()=>recordDouhan().catch(e=>toast(e.message,'err')));
  $('btnDiscount').addEventListener('click', ()=>applyDiscount().catch(e=>toast(e.message,'err')));
  $('btnNomHon').addEventListener('click', ()=>recordNomination('hon').catch(e=>toast(e.message,'err')));
  $('btnNomJyonai').addEventListener('click', ()=>recordNomination('jyonai').catch(e=>toast(e.message,'err')));

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

  // 領収書ポップアップ表示（会計確定後・ボタン押下の両方から呼ぶ）
  function openReceiptWindow(d){
    const b = d.bill, st = d.store;
    const now = new Date().toLocaleString('ja-JP');
    const orderRows = (b.orders||[]).map(o=>
      `<tr><td>${o.name}${o.qty>1?' x'+o.qty:''}</td><td style="text-align:right">¥${Math.round(o.amount).toLocaleString()}</td></tr>`
    ).join('');
    const nomiRows = (b.nominations||[]).map(n=>
      `<tr><td>${n.label}</td><td style="text-align:right">¥${Math.round(n.amount).toLocaleString()}</td></tr>`
    ).join('');
    const w = window.open('','_blank','width=420,height=650');
    if(!w){ toast('ポップアップがブロックされています。許可してください。','err'); return; }
    w.document.write(`<!doctype html><html><head><meta charset="utf-8">
      <title>領収書・明細</title>
      <style>body{font-family:sans-serif;font-size:13px;padding:20px;color:#111}
      h2{text-align:center;font-size:18px;border-bottom:2px solid #000;padding-bottom:8px;margin-bottom:12px}
      table{width:100%;border-collapse:collapse;margin:8px 0}
      td,th{padding:4px 6px}th{text-align:left;font-weight:600;border-bottom:1px solid #ccc;font-size:11px;color:#666}
      .right{text-align:right}.total{font-size:16px;font-weight:bold;border-top:2px solid #000}
      .subtotal-row{border-top:1px solid #ccc}.muted{color:#666;font-size:11px}
      .change{font-size:15px;font-weight:bold;color:#006800}
      @media print{button{display:none}.no-print{display:none}}</style>
      </head><body>
      <h2>領 収 書 ・ 明 細</h2>
      <div style="text-align:center;margin-bottom:10px">
        <div style="font-size:11px;color:#666">${now}</div>
        <div style="font-size:11px">NO: ${d.invoice_no}</div>
      </div>
      <table>
        <tr><td class="muted">テーブル</td><td>${b.table||''}</td></tr>
        <tr><td class="muted">人数</td><td>${b.guest_count}名</td></tr>
        <tr><td class="muted">入店</td><td>${new Date(b.start_time).toLocaleTimeString('ja-JP',{hour:'2-digit',minute:'2-digit'})}</td></tr>
        <tr><td class="muted">退店</td><td>${new Date(b.end_time).toLocaleTimeString('ja-JP',{hour:'2-digit',minute:'2-digit'})}</td></tr>
      </table>
      <table>
        <tr><th>品目</th><th style="text-align:right">金額</th></tr>
        <tr><td>セット料金</td><td class="right">¥${Math.round(b.time_breakdown?.time_amount||0).toLocaleString()}</td></tr>
        ${b.table_charge>0?`<tr><td>お通し/TC</td><td class="right">¥${Math.round(b.table_charge).toLocaleString()}</td></tr>`:''}
        ${b.vip_fee>0?`<tr><td>VIP席料</td><td class="right">¥${Math.round(b.vip_fee).toLocaleString()}</td></tr>`:''}
        ${orderRows}
        ${nomiRows}
        ${b.night_surcharge>0?`<tr><td>深夜加算</td><td class="right">¥${Math.round(b.night_surcharge).toLocaleString()}</td></tr>`:''}
        ${b.discount_amount>0?`<tr><td>割引（${b.discount_label||''}）</td><td class="right">-¥${Math.round(b.discount_amount).toLocaleString()}</td></tr>`:''}
        <tr class="subtotal-row"><td>小計</td><td class="right">¥${Math.round(b.subtotal).toLocaleString()}</td></tr>
        <tr><td>サービス料</td><td class="right">¥${Math.round(b.service_fee).toLocaleString()}</td></tr>
        <tr><td>消費税(10%)</td><td class="right">¥${Math.round(b.tax).toLocaleString()}</td></tr>
        <tr class="total"><td>合 計</td><td class="right">¥${Math.round(b.total).toLocaleString()}</td></tr>
        <tr><td>お支払い済み</td><td class="right">¥${Math.round(b.paid).toLocaleString()}</td></tr>
        <tr><td class="change">お釣り</td><td class="right change">¥${Math.max(0,Math.round(b.paid-b.total)).toLocaleString()}</td></tr>
      </table>
      <div style="margin-top:16px;text-align:center;font-size:11px;color:#666">
        <div style="font-weight:600">${st.legal_name||''}</div>
        <div>${st.address||''}</div>
        ${st.tel?`<div>TEL: ${st.tel}</div>`:''}
        ${st.invoice_reg_no?`<div>登録番号: ${st.invoice_reg_no}</div>`:''}
      </div>
      <div style="text-align:center;margin-top:14px" class="no-print">
        <button onclick="window.print()" style="padding:10px 28px;font-size:14px;cursor:pointer">🖨️ 印刷</button>
      </div>
      </body></html>`);
    w.document.close();
  }

  // 領収書ボタン（open中のセッションでも closed後でも使える）
  $('btnReceipt')?.addEventListener('click', async ()=>{
    const sid = currentSessionId;
    if (!sid) return toast('セッションを選択してください','err');
    try{
      const d = await api(`/sessions/${sid}/receipt`);
      openReceiptWindow(d);
    }catch(e){toast('領収書エラー: '+e.message,'err')}
  });

  // 数量反映
  $('applyDrink').addEventListener('click', ()=>applyCategory('drink').catch(e=>toast(e.message,'err')));
  $('applyBottle').addEventListener('click', ()=>applyCategory('bottle').catch(e=>toast(e.message,'err')));
  $('applyFood').addEventListener('click', ()=>applyCategory('food').catch(e=>toast(e.message,'err')));

  await loadStoreTimeConfig();
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

  // サブスク状態チェック（解約・未払いなら強制ロック画面）
  try{
    const sub=await fetch('/stripe/status?store_id='+store());
    if(sub.ok){
      const sd=await sub.json();
      if(sd.locked){
        const msg=$('subModalMsg');
        if(msg){
          const labels={canceled:'解約済み',past_due:'お支払いが滞っています',inactive:'未加入',unpaid:'お支払いが確認できません'};
          msg.innerHTML=`サブスクリプションが<b style="color:#fca5a5">${labels[sd.status]||sd.status}</b>です。<br>引き続きご利用いただくには再契約が必要です。`;
        }
        $('subModal').style.display='flex';
        // 操作を完全に無効化
        document.body.style.pointerEvents='none';
        $('subModal').style.pointerEvents='auto';
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
:root{--bg:#fafafa;--card:#ffffff;--card2:#ffffff;--line:#eaeaef;--border:#eaeaef;--text:#0a0a0f;--ink:#0a0a0f;--muted:#8a8a95;--body:#4a4a55;--accent:#d64583;--accent-soft:#fdf0f7;--accent-dark:#b03468;--gold:#c9a96e;--gold-soft:#faf3e3;--warn:#f59e0b;--amber:#f59e0b;--err:#ef4444;--red:#ef4444;--ok:#22c55e;--green:#22c55e;--blue:#3b82f6;--purple:#a855f7;}
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

/* === Premium Pink Theme Override (auto-injected) === */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=Inter:wght@400;500;600;700;800&display=swap');
body{font-family:'Inter','Noto Sans JP',-apple-system,system-ui,Segoe UI,Roboto,sans-serif !important;background:#fafafa !important;color:#0a0a0f !important;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4{color:#0a0a0f}
a{color:#d64583}
.card,section,article{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.stat,.kpi,.tile,.metric{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f}
table{border-color:#eaeaef !important}
th{background:#fafafa !important;color:#4a4a55 !important;border-color:#eaeaef !important;font-weight:700 !important}
td{border-color:#f3f3f6 !important;color:#0a0a0f !important;background:#ffffff}
tr:nth-child(even) td{background:#fafafa}
.btn{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important;font-weight:600 !important;transition:all .2s}
.btn:hover{border-color:#d64583 !important;color:#d64583 !important}
.btn.primary,.btn.solid{background:#d64583 !important;border-color:#d64583 !important;color:#ffffff !important}
.btn.danger{background:#ef4444 !important;border-color:#ef4444 !important;color:#ffffff !important}
input,select,textarea{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important}
input:focus,select:focus{border-color:#d64583 !important;box-shadow:0 0 0 3px #fdf0f7 !important;outline:none}
.method.POST{background:#f0fdf4 !important;color:#15803d !important}
.method.DELETE{background:#fef2f2 !important;color:#b91c1c !important}
.method.PATCH{background:#fff7ed !important;color:#c2410c !important}
.method.PUT{background:#eff6ff !important;color:#1d4ed8 !important}
.cast-card{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.cast-card.in .status{background:#f0fdf4 !important;color:#15803d !important}
.cast-card.out .status{background:#fafafa !important;color:#8a8a95 !important}
.bar{background:#ffffff !important;border-color:#eaeaef}
.notice{background:#fff7ed !important;color:#7c2d12 !important;border-left:3px solid #f59e0b !important}
.modal-card{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.toast.ok{background:#f0fdf4 !important;color:#14532d !important;border-color:#86efac !important}
.toast.err{background:#fef2f2 !important;color:#7f1d1d !important;border-color:#fca5a5 !important}
[style*="background:#111827"],[style*="background:#0f172a"],[style*="background:#0a1220"],[style*="background:#0e1a26"]{background:#ffffff !important;color:#0a0a0f !important}
[style*="border:1px solid #263244"],[style*="border:1px solid #334155"]{border-color:#eaeaef !important}

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

# ======================= 出退勤UI =======================
@app.get("/ui/attendance", response_class=HTMLResponse)
def ui_attendance():
    return HTMLResponse(r"""<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>出退勤 - Girls Bar POS</title>
<script>
// 早期ガード: スタッフ等のオーナー以外を弾く（未設定はowner扱い: コードベース規約に準拠）
(function(){
  const role = sessionStorage.getItem('pos_role') || 'owner';
  if(role !== 'owner'){
    alert('この画面はオーナー専用です');
    window.location.href='/ui';
  }
})();
</script>
<style>
:root{--bg:#fafafa;--card:#ffffff;--card2:#ffffff;--line:#eaeaef;--border:#eaeaef;--text:#0a0a0f;--ink:#0a0a0f;--muted:#8a8a95;--body:#4a4a55;--accent:#d64583;--accent-soft:#fdf0f7;--accent-dark:#b03468;--gold:#c9a96e;--gold-soft:#faf3e3;--warn:#f59e0b;--amber:#f59e0b;--err:#ef4444;--red:#ef4444;--ok:#22c55e;--green:#22c55e;--blue:#3b82f6;--purple:#a855f7;}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text)}
header{position:sticky;top:0;z-index:40;display:flex;gap:12px;align-items:center;padding:14px 16px;border-bottom:1px solid var(--line);background:rgba(11,18,32,.95);backdrop-filter:blur(6px)}
header h1{margin:0;font-size:18px}
.nav a{color:var(--accent);text-decoration:none;font-size:14px;padding:6px 10px;border-radius:8px;border:1px solid var(--line)}
.container{max-width:700px;margin:0 auto;padding:24px 16px}
.clock{text-align:center;font-size:48px;font-weight:900;font-family:ui-monospace,monospace;color:var(--accent);margin-bottom:24px}
.date{text-align:center;font-size:16px;color:var(--muted);margin-bottom:8px}
.cast-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px}
.cast-card{background:var(--card);border:2px solid var(--line);border-radius:16px;padding:20px;text-align:center;cursor:pointer;transition:all .15s;user-select:none}
.cast-card:active{transform:scale(.97)}
.cast-card .name{font-size:20px;font-weight:700;margin-bottom:8px}
.cast-card .status{font-size:14px;padding:4px 12px;border-radius:20px;display:inline-block}
.cast-card.in{border-color:var(--green)}
.cast-card.in .status{background:#14532d;color:#86efac}
.cast-card.out{border-color:var(--line)}
.cast-card.out .status{background:#1e1b2e;color:var(--muted)}
.cast-card .time{font-size:12px;color:var(--muted);margin-top:6px}
.cast-card .elapsed{font-size:13px;color:#fcd34d;margin-top:4px;font-family:ui-monospace,monospace}
.cast-card .wage{font-size:18px;font-weight:800;color:#86efac;margin-top:4px;font-family:ui-monospace,monospace}
.summary{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:18px}
.summary .box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.summary .label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.summary .val{font-size:22px;font-weight:800;font-family:ui-monospace,monospace}
.summary .val.sales{color:#22c55e}
.summary .val.wages{color:#fcd34d}
.summary .sub{font-size:11px;color:var(--muted);margin-top:4px}
@media(max-width:500px){.summary{grid-template-columns:1fr}.summary .val{font-size:18px}}
.toast{position:fixed;bottom:20px;right:20px;padding:14px 20px;border-radius:12px;font-size:15px;font-weight:700;z-index:100;animation:slide .2s ease-out}
.toast.ok{background:#14532d;border:1px solid var(--green);color:#86efac}
.toast.err{background:#450a0a;border:1px solid var(--red);color:#fca5a5}
@keyframes slide{from{transform:translateY(10px);opacity:0}to{transform:translateY(0);opacity:1}}
@media(max-width:500px){
  .cast-grid{grid-template-columns:1fr 1fr}
  .cast-card .name{font-size:17px}
  .clock{font-size:36px}
}

/* === Premium Pink Theme Override (auto-injected) === */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=Inter:wght@400;500;600;700;800&display=swap');
body{font-family:'Inter','Noto Sans JP',-apple-system,system-ui,Segoe UI,Roboto,sans-serif !important;background:#fafafa !important;color:#0a0a0f !important;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4{color:#0a0a0f}
a{color:#d64583}
.card,section,article{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.stat,.kpi,.tile,.metric{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f}
table{border-color:#eaeaef !important}
th{background:#fafafa !important;color:#4a4a55 !important;border-color:#eaeaef !important;font-weight:700 !important}
td{border-color:#f3f3f6 !important;color:#0a0a0f !important;background:#ffffff}
tr:nth-child(even) td{background:#fafafa}
.btn{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important;font-weight:600 !important;transition:all .2s}
.btn:hover{border-color:#d64583 !important;color:#d64583 !important}
.btn.primary,.btn.solid{background:#d64583 !important;border-color:#d64583 !important;color:#ffffff !important}
.btn.danger{background:#ef4444 !important;border-color:#ef4444 !important;color:#ffffff !important}
input,select,textarea{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important}
input:focus,select:focus{border-color:#d64583 !important;box-shadow:0 0 0 3px #fdf0f7 !important;outline:none}
.method.POST{background:#f0fdf4 !important;color:#15803d !important}
.method.DELETE{background:#fef2f2 !important;color:#b91c1c !important}
.method.PATCH{background:#fff7ed !important;color:#c2410c !important}
.method.PUT{background:#eff6ff !important;color:#1d4ed8 !important}
.cast-card{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.cast-card.in .status{background:#f0fdf4 !important;color:#15803d !important}
.cast-card.out .status{background:#fafafa !important;color:#8a8a95 !important}
.bar{background:#ffffff !important;border-color:#eaeaef}
.notice{background:#fff7ed !important;color:#7c2d12 !important;border-left:3px solid #f59e0b !important}
.modal-card{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.toast.ok{background:#f0fdf4 !important;color:#14532d !important;border-color:#86efac !important}
.toast.err{background:#fef2f2 !important;color:#7f1d1d !important;border-color:#fca5a5 !important}
[style*="background:#111827"],[style*="background:#0f172a"],[style*="background:#0a1220"],[style*="background:#0e1a26"]{background:#ffffff !important;color:#0a0a0f !important}
[style*="border:1px solid #263244"],[style*="border:1px solid #334155"]{border-color:#eaeaef !important}

</style></head><body>
<header>
  <h1>出退勤</h1>
  <div class="nav" style="display:flex;gap:8px;margin-left:auto">
    <a href="/ui">← フロア</a>
    <a href="/ui/salary">給与</a>
    <a href="/ui/attendance/manage" id="manageLink" style="display:none;border-color:#f59e0b;color:#fcd34d">📋 修正・追加</a>
  </div>
</header>
<div class="container">
  <div class="date" id="dateDisp"></div>
  <div class="clock" id="clockDisp">--:--:--</div>
  <label style="display:flex;align-items:center;gap:6px;margin-bottom:16px;color:var(--muted);font-size:13px">
    店舗 <input id="storeId" type="number" value="1" style="width:70px;font-size:16px;padding:8px;border-radius:8px;border:1px solid #263244;background:#0a1220;color:var(--text)">
  </label>
  <div class="summary">
    <div class="box">
      <div class="label">本日の売上（見込み）</div>
      <div class="val sales" id="todaySales">¥-</div>
      <div class="sub" id="salesSub">確定 ¥0 / 0組</div>
    </div>
    <div class="box">
      <div class="label">出勤中の発生時給（合計）</div>
      <div class="val wages" id="totalWages">¥0</div>
      <div class="sub" id="wagesSub">出勤中 0名</div>
    </div>
  </div>
  <div class="cast-grid" id="castGrid"></div>
</div>
<script>
const $=id=>document.getElementById(id);

function updateClock(){
  const now=new Date();
  $('clockDisp').textContent=now.toLocaleTimeString('ja-JP');
  const days=['日','月','火','水','木','金','土'];
  $('dateDisp').textContent=`${now.getFullYear()}年${now.getMonth()+1}月${now.getDate()}日 (${days[now.getDay()]})`;
}
setInterval(updateClock,1000); updateClock();

async function api(path,opt={}){
  const tk=sessionStorage.getItem('pos_token')||'';
  const o={method:'GET',headers:{'Content-Type':'application/json','X-Role':'owner','X-Token':tk},...opt};
  if(o.body&&typeof o.body!=='string') o.body=JSON.stringify(o.body);
  const r=await fetch(path,o);
  if(r.status===401){sessionStorage.clear();window.location.href='/';return;}
  if(r.status===402){window.location.href='/ui/subscription';return;}
  if(r.status===403){alert('オーナー権限が必要です');window.location.href='/ui';return;}
  if(!r.ok){const t=await r.text();throw new Error(t);}
  return r.json();
}

function showToast(msg,type='ok'){
  const t=document.createElement('div');
  t.className='toast '+type; t.textContent=msg;
  document.body.appendChild(t);
  setTimeout(()=>t.remove(),3000);
}

let castData=[];
function escapeHtml(s){return (s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmtElapsed(ms){
  const sec=Math.max(0,Math.floor(ms/1000));
  const h=Math.floor(sec/3600), m=Math.floor((sec%3600)/60), ss=sec%60;
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(ss).padStart(2,'0')}`;
}
function fmtYen(n){return '¥'+Math.round(n).toLocaleString();}

async function loadStatus(){
  const s=$('storeId').value;
  try{
    castData=await api(`/attendance/status?store_id=${s}`);
    const grid=$('castGrid'); grid.innerHTML='';
    castData.forEach(c=>{
      const card=document.createElement('div');
      card.className='cast-card '+(c.clocked_in?'in':'out');
      card.dataset.castId=c.cast_id;
      let timeStr='';
      if(c.clocked_in && c.clock_in_time){
        const d=new Date(c.clock_in_time+'Z');
        timeStr=d.toLocaleTimeString('ja-JP',{hour:'2-digit',minute:'2-digit'})+' から勤務中';
      }
      const showWage = c.clocked_in && c.hourly_rate>0;
      card.innerHTML=`<div class="name">${escapeHtml(c.cast_name)}</div>
        <div class="status">${c.clocked_in?'出勤中':'退勤'}</div>
        ${timeStr?`<div class="time">${timeStr}</div>`:''}
        ${c.clocked_in?'<div class="elapsed" data-elapsed>--:--:--</div>':''}
        ${showWage?'<div class="wage" data-wage>¥0</div>':''}`;
      card.addEventListener('click',async()=>{
        if(c.clocked_in){
          if(!confirm(`${c.cast_name} を退勤にしますか？`)) return;
          try{
            await api('/attendance/clock-out',{method:'POST',body:{store_id:parseInt(s),cast_id:c.cast_id}});
            showToast(`${c.cast_name} 退勤しました`);
          }catch(e){showToast(e.message,'err');}
        }else{
          if(!confirm(`${c.cast_name} を出勤にしますか？`)) return;
          try{
            await api('/attendance/clock-in',{method:'POST',body:{store_id:parseInt(s),cast_id:c.cast_id}});
            showToast(`${c.cast_name} 出勤しました`);
          }catch(e){showToast(e.message,'err');}
        }
        loadStatus();
        loadSales();
      });
      grid.appendChild(card);
    });
    tickWages();
  }catch(e){showToast(e.message,'err');}
}

function tickWages(){
  const grid=$('castGrid');
  let totalWages=0, activeCount=0;
  const now=Date.now();
  castData.forEach(c=>{
    if(!c.clocked_in || !c.clock_in_time) return;
    activeCount++;
    const startMs=new Date(c.clock_in_time+'Z').getTime();
    const elapsedMs=Math.max(0, now-startMs);
    const hours=elapsedMs/3600000;
    const wage=hours*(c.hourly_rate||0);
    totalWages+=wage;
    const card=grid.querySelector(`[data-cast-id="${c.cast_id}"]`);
    if(card){
      const eEl=card.querySelector('[data-elapsed]');
      if(eEl) eEl.textContent=fmtElapsed(elapsedMs);
      const wEl=card.querySelector('[data-wage]');
      if(wEl) wEl.textContent=fmtYen(wage);
    }
  });
  $('totalWages').textContent=fmtYen(totalWages);
  $('wagesSub').textContent=`出勤中 ${activeCount}名`;
}

async function loadSales(){
  const s=$('storeId').value;
  try{
    const d=await api(`/closing?store_id=${s}`);
    $('todaySales').textContent=fmtYen(d.total_sales||0);
    $('salesSub').textContent=`確定 ${fmtYen(d.confirmed_sales||0)} / ${d.closed_count||0}組確定・${d.open_count||0}組open`;
  }catch{}
}

$('storeId').addEventListener('change',()=>{loadStatus();loadSales();});
// オーナー限定リンクの表示
if(sessionStorage.getItem('pos_role')==='owner'){
  $('manageLink').style.display='inline-block';
}
loadStatus();
loadSales();
setInterval(tickWages,1000);   // 1秒ごとに経過時間と発生時給を更新
setInterval(loadStatus,30000); // 30秒ごとに出退勤状況を再取得（WS補完用）
setInterval(loadSales,15000);  // 15秒ごとに売上を再取得（WS補完用）

// WebSocket: フロア端末からの注文・入店・会計を即時反映
let _ws=null;
function connectWS(){
  try{
    const proto=location.protocol==='https:'?'wss:':'ws:';
    _ws=new WebSocket(`${proto}//${location.host}/ws`);
    _ws.onclose=()=>{ setTimeout(connectWS,3000); };
    _ws.onerror=()=>{ try{_ws.close();}catch{} };
    _ws.onmessage=(ev)=>{
      try{
        const msg=JSON.parse(ev.data);
        // 売上に影響するイベント → 売上カードを即時更新
        if(['order','cancel_order','payment','checkout','cancel_session','extend','checkin','guest_count','move_table','start_time','douhan','discount'].includes(msg.event)){
          loadSales();
        }
      }catch{}
    };
  }catch{}
}
connectWS();
</script></body></html>""")

# ======================= 出退勤 編集UI（オーナー限定） =======================
@app.get("/ui/attendance/manage", response_class=HTMLResponse)
def ui_attendance_manage():
    return HTMLResponse(r"""<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>出退勤 修正 - Girls Bar POS</title>
<style>
:root{--bg:#fafafa;--card:#ffffff;--card2:#ffffff;--line:#eaeaef;--border:#eaeaef;--text:#0a0a0f;--ink:#0a0a0f;--muted:#8a8a95;--body:#4a4a55;--accent:#d64583;--accent-soft:#fdf0f7;--accent-dark:#b03468;--gold:#c9a96e;--gold-soft:#faf3e3;--warn:#f59e0b;--amber:#f59e0b;--err:#ef4444;--red:#ef4444;--ok:#22c55e;--green:#22c55e;--blue:#3b82f6;--purple:#a855f7;}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text)}
header{position:sticky;top:0;z-index:40;display:flex;gap:12px;align-items:center;padding:14px 16px;border-bottom:1px solid var(--line);background:rgba(11,18,32,.95)}
header h1{margin:0;font-size:17px}
.nav a{color:var(--accent);text-decoration:none;font-size:13px;padding:6px 10px;border-radius:8px;border:1px solid var(--line)}
.container{max-width:1000px;margin:0 auto;padding:20px 16px}
.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:18px;padding:14px;background:var(--card);border:1px solid var(--line);border-radius:12px}
.bar label{display:flex;flex-direction:column;gap:4px;font-size:11px;color:var(--muted)}
.bar input,.bar select{font-size:14px;padding:7px 10px;border-radius:8px;border:1px solid #263244;background:#0a1220;color:var(--text)}
.btn{cursor:pointer;font-size:13px;padding:8px 14px;border-radius:8px;border:1px solid #334155;background:#111827;color:var(--text)}
.btn.solid{background:var(--accent);border-color:var(--accent);color:#001018;font-weight:700}
.btn.green{background:var(--green);border-color:var(--green);color:#001018;font-weight:700}
.btn.red{background:#7f1d1d;border-color:var(--red);color:#fca5a5}
.btn.sm{font-size:11px;padding:5px 9px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:13px}
th,td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left}
th{background:#111827;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
tr:last-child td{border-bottom:none}
.empty{text-align:center;padding:40px;color:var(--muted)}
.notice{background:#1a2030;border-left:3px solid var(--amber);padding:10px 14px;border-radius:8px;font-size:12px;color:var(--muted);margin-bottom:14px}
.notice b{color:#fcd34d}
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:100;align-items:center;justify-content:center}
.modal.show{display:flex}
.modal-card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px;max-width:420px;width:90%}
.modal-card h3{margin:0 0 14px;font-size:16px}
.modal-card label{display:block;font-size:12px;color:var(--muted);margin:10px 0 4px}
.modal-card input,.modal-card select{width:100%;padding:9px 12px;border-radius:8px;border:1px solid #263244;background:#0a1220;color:var(--text);font-size:14px}
.modal-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}
.toast{position:fixed;bottom:20px;right:20px;padding:12px 18px;border-radius:10px;font-size:14px;font-weight:700;z-index:200}
.toast.ok{background:#14532d;border:1px solid var(--green);color:#86efac}
.toast.err{background:#450a0a;border:1px solid var(--red);color:#fca5a5}

/* === Premium Pink Theme Override (auto-injected) === */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=Inter:wght@400;500;600;700;800&display=swap');
body{font-family:'Inter','Noto Sans JP',-apple-system,system-ui,Segoe UI,Roboto,sans-serif !important;background:#fafafa !important;color:#0a0a0f !important;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4{color:#0a0a0f}
a{color:#d64583}
.card,section,article{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.stat,.kpi,.tile,.metric{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f}
table{border-color:#eaeaef !important}
th{background:#fafafa !important;color:#4a4a55 !important;border-color:#eaeaef !important;font-weight:700 !important}
td{border-color:#f3f3f6 !important;color:#0a0a0f !important;background:#ffffff}
tr:nth-child(even) td{background:#fafafa}
.btn{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important;font-weight:600 !important;transition:all .2s}
.btn:hover{border-color:#d64583 !important;color:#d64583 !important}
.btn.primary,.btn.solid{background:#d64583 !important;border-color:#d64583 !important;color:#ffffff !important}
.btn.danger{background:#ef4444 !important;border-color:#ef4444 !important;color:#ffffff !important}
input,select,textarea{background:#ffffff !important;border:1px solid #eaeaef !important;color:#0a0a0f !important}
input:focus,select:focus{border-color:#d64583 !important;box-shadow:0 0 0 3px #fdf0f7 !important;outline:none}
.method.POST{background:#f0fdf4 !important;color:#15803d !important}
.method.DELETE{background:#fef2f2 !important;color:#b91c1c !important}
.method.PATCH{background:#fff7ed !important;color:#c2410c !important}
.method.PUT{background:#eff6ff !important;color:#1d4ed8 !important}
.cast-card{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.cast-card.in .status{background:#f0fdf4 !important;color:#15803d !important}
.cast-card.out .status{background:#fafafa !important;color:#8a8a95 !important}
.bar{background:#ffffff !important;border-color:#eaeaef}
.notice{background:#fff7ed !important;color:#7c2d12 !important;border-left:3px solid #f59e0b !important}
.modal-card{background:#ffffff !important;border-color:#eaeaef !important;color:#0a0a0f}
.toast.ok{background:#f0fdf4 !important;color:#14532d !important;border-color:#86efac !important}
.toast.err{background:#fef2f2 !important;color:#7f1d1d !important;border-color:#fca5a5 !important}
[style*="background:#111827"],[style*="background:#0f172a"],[style*="background:#0a1220"],[style*="background:#0e1a26"]{background:#ffffff !important;color:#0a0a0f !important}
[style*="border:1px solid #263244"],[style*="border:1px solid #334155"]{border-color:#eaeaef !important}

</style></head><body>
<header>
  <h1>📋 出退勤 修正・追加（オーナー限定）</h1>
  <div class="nav" style="display:flex;gap:8px;margin-left:auto">
    <a href="/ui/attendance">← 打刻画面</a>
    <a href="/ui/salary">給与</a>
    <a href="/ui">フロア</a>
  </div>
</header>

<div class="container">
  <div class="notice">
    <b>⚠ 打刻忘れの手動入力</b> — このページはオーナーのみアクセスできます。修正・追加した内容はすぐ給与計算に反映されます。
  </div>

  <div class="bar">
    <label>店舗 <input id="storeId" type="number" value="1" style="width:80px"></label>
    <label>年 <input id="year" type="number" style="width:90px"></label>
    <label>月 <input id="month" type="number" min="1" max="12" style="width:70px"></label>
    <button class="btn solid" onclick="loadRecords()">読み込み</button>
    <button class="btn green" style="margin-left:auto" onclick="openAdd()">＋ 新規追加</button>
  </div>

  <div id="tableWrap"></div>
</div>

<!-- 追加・編集モーダル -->
<div class="modal" id="modal">
  <div class="modal-card">
    <h3 id="modalTitle">出退勤を追加</h3>
    <label>キャスト</label>
    <select id="m_cast"></select>
    <label>出勤日時</label>
    <input id="m_in" type="datetime-local">
    <label>退勤日時（未入力可）</label>
    <input id="m_out" type="datetime-local">
    <div class="modal-actions">
      <button class="btn red" id="m_delete" onclick="doDelete()" style="margin-right:auto;display:none">削除</button>
      <button class="btn" onclick="closeModal()">キャンセル</button>
      <button class="btn solid" onclick="doSave()">保存</button>
    </div>
  </div>
</div>

<script>
const $=id=>document.getElementById(id);
let editId=null;
let castList=[];

async function api(path,opt={}){
  const tk=sessionStorage.getItem('pos_token')||'';
  const o={method:'GET',headers:{'Content-Type':'application/json','X-Role':'owner','X-Token':tk},...opt};
  if(o.body&&typeof o.body!=='string') o.body=JSON.stringify(o.body);
  const r=await fetch(path,o);
  if(r.status===401){sessionStorage.clear();window.location.href='/';return;}
  if(r.status===402){window.location.href='/ui/subscription';return;}
  if(r.status===403){alert('オーナー権限が必要です');window.location.href='/ui/attendance';return;}
  if(!r.ok){const t=await r.text();throw new Error(t);}
  return r.json();
}

function toast(msg,type='ok'){
  const el=document.createElement('div');
  el.className='toast '+type; el.textContent=msg;
  document.body.appendChild(el);
  setTimeout(()=>el.remove(),2500);
}

function escapeHtml(s){return (s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

async function loadCasts(){
  const s=$('storeId').value;
  try{
    castList=await api(`/casts?store_id=${s}`);
    const sel=$('m_cast');
    sel.innerHTML=castList.map(c=>`<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
  }catch(e){toast(e.message,'err');}
}

async function loadRecords(){
  const s=$('storeId').value;
  const y=$('year').value;
  const m=$('month').value;
  try{
    const rows=await api(`/attendance/records?store_id=${s}&year=${y}&month=${m}`);
    const wrap=$('tableWrap');
    if(!rows.length){
      wrap.innerHTML='<div class="empty">この月の記録はまだありません</div>';
      return;
    }
    wrap.innerHTML=`<table>
      <thead><tr><th>キャスト</th><th>出勤</th><th>退勤</th><th>勤務時間</th><th></th></tr></thead>
      <tbody>${rows.map(r=>`
        <tr>
          <td>${escapeHtml(r.cast_name)}</td>
          <td>${r.clock_in.replace('T',' ')}</td>
          <td>${r.clock_out?r.clock_out.replace('T',' '):'<span style="color:#fca5a5">未退勤</span>'}</td>
          <td>${r.hours!==null?r.hours+' h':'—'}</td>
          <td><button class="btn sm" onclick='openEdit(${JSON.stringify(r).replace(/'/g,"&#39;")})'>編集</button></td>
        </tr>`).join('')}
      </tbody></table>`;
  }catch(e){toast(e.message,'err');}
}

function openAdd(){
  editId=null;
  $('modalTitle').textContent='出退勤を追加';
  $('m_delete').style.display='none';
  // 当月の今日にプリセット
  const y=parseInt($('year').value), m=parseInt($('month').value);
  const today=new Date();
  const d=(today.getFullYear()===y && today.getMonth()+1===m)?today.getDate():1;
  const pad=n=>String(n).padStart(2,'0');
  $('m_in').value=`${y}-${pad(m)}-${pad(d)}T19:00`;
  $('m_out').value=`${y}-${pad(m)}-${pad(d)}T23:00`;
  $('modal').classList.add('show');
}

function openEdit(r){
  editId=r.id;
  $('modalTitle').textContent='出退勤を修正';
  $('m_delete').style.display='';
  $('m_cast').value=r.cast_id;
  $('m_in').value=r.clock_in;
  $('m_out').value=r.clock_out||'';
  $('modal').classList.add('show');
}

function closeModal(){$('modal').classList.remove('show');}

async function doSave(){
  const cast_id=parseInt($('m_cast').value);
  const clock_in=$('m_in').value;
  const clock_out=$('m_out').value||null;
  if(!clock_in){toast('出勤日時は必須です','err');return;}
  try{
    if(editId){
      await api(`/attendance/records/${editId}`,{method:'PATCH',body:{clock_in,clock_out}});
      toast('修正しました');
    }else{
      await api('/attendance/records',{method:'POST',body:{
        store_id:parseInt($('storeId').value), cast_id, clock_in, clock_out
      }});
      toast('追加しました');
    }
    closeModal();
    loadRecords();
  }catch(e){toast(e.message,'err');}
}

async function doDelete(){
  if(!editId) return;
  if(!confirm('この記録を削除しますか？')) return;
  try{
    await api(`/attendance/records/${editId}`,{method:'DELETE'});
    toast('削除しました');
    closeModal();
    loadRecords();
  }catch(e){toast(e.message,'err');}
}

// 初期化
(function init(){
  // ロールチェック（フロント側でも早期ガード）— 未設定はowner扱い（規約準拠）
  const role = sessionStorage.getItem('pos_role') || 'owner';
  if(role !== 'owner'){
    alert('このページはオーナー専用です');
    window.location.href='/ui/attendance';
    return;
  }
  const now=new Date();
  $('year').value=now.getFullYear();
  $('month').value=now.getMonth()+1;
  loadCasts();
  loadRecords();
})();
</script></body></html>""")