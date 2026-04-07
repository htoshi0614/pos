"""landing.py — POS Start ランディングページ & デモ申込みフォーム"""

from datetime import datetime
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, DateTime, Text
from db_shared import Base, SessionLocal

router = APIRouter(tags=["landing"])

# ---------- デモ申込みDB ----------
class DemoRequest(Base):
    __tablename__ = "demo_requests"
    id = Column(Integer, primary_key=True)
    store_name = Column(String, default="")
    name = Column(String, default="")
    phone = Column(String, default="")
    email = Column(String, default="")
    message = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

class DemoRequestIn(BaseModel):
    store_name: str
    name: str
    phone: str = ""
    email: str = ""
    message: str = ""

@router.post("/api/demo-request")
def submit_demo_request(payload: DemoRequestIn):
    db = SessionLocal()
    try:
        req = DemoRequest(
            store_name=payload.store_name,
            name=payload.name,
            phone=payload.phone,
            email=payload.email,
            message=payload.message,
        )
        db.add(req)
        db.commit()
        return {"ok": True, "message": "お申込みありがとうございます。"}
    finally:
        db.close()

@router.get("/lp", response_class=HTMLResponse)
def landing_page():
    return HTMLResponse(_LP_HTML)

_LP_HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>POS Start — その計算、まだ人がやってますか？</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#0d0d0d;--card:rgba(255,255,255,.06);--card-border:rgba(255,255,255,.1);
  --gold:#d4a853;--gold-light:#f0d48a;--red:#c0392b;--red-glow:rgba(192,57,43,.5);
  --text:#f0ece2;--muted:rgba(240,236,226,.55);
  --radius:12px;
}
html{scroll-behavior:smooth}
body{font-family:"Hiragino Kaku Gothic ProN","Noto Sans JP",-apple-system,sans-serif;color:var(--text);line-height:1.8;background:var(--bg)}
a{color:var(--gold);text-decoration:none}
.inner{max-width:880px;margin:0 auto;padding:0 20px}

/* ===== HERO ===== */
.hero{
  position:relative;overflow:hidden;
  background:linear-gradient(170deg,#0d0d0d 0%,#1a0a0a 40%,#0d0d0d 100%);
  padding:100px 20px 80px;text-align:center;
}
.hero::before{
  content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;
  background:radial-gradient(circle at 60% 40%,rgba(212,168,83,.08) 0%,transparent 50%);
  pointer-events:none;
}
.hero-eyecatch{
  font-size:clamp(14px,2.5vw,18px);color:var(--gold);letter-spacing:4px;
  text-transform:uppercase;margin-bottom:20px;font-weight:600;
}
.hero h1{
  font-size:clamp(28px,6vw,52px);font-weight:900;line-height:1.3;margin-bottom:12px;
}
.hero h1 .accent{color:var(--gold);position:relative}
.hero h1 .accent::after{
  content:'';position:absolute;bottom:-2px;left:0;width:100%;height:3px;
  background:linear-gradient(90deg,var(--gold),transparent);
}
.hero .sub{
  font-size:clamp(15px,2.5vw,20px);color:var(--muted);margin-bottom:12px;
}
.hero .sub-strong{
  font-size:clamp(18px,3vw,26px);font-weight:700;margin-bottom:40px;
  background:linear-gradient(90deg,var(--gold-light),var(--gold));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
}
.cta-btn{
  display:inline-block;padding:18px 56px;font-size:18px;font-weight:800;
  color:#0d0d0d;background:linear-gradient(135deg,var(--gold-light),var(--gold));
  border:none;border-radius:60px;cursor:pointer;letter-spacing:1px;
  box-shadow:0 4px 24px rgba(212,168,83,.3);
  transition:transform .2s,box-shadow .2s;
}
.cta-btn:hover{transform:translateY(-3px);box-shadow:0 8px 32px rgba(212,168,83,.5)}
.cta-btn-sub{display:block;font-size:12px;font-weight:400;margin-top:4px;opacity:.7}

/* ===== PAIN ===== */
.pain{padding:80px 20px;background:#111}
.section-label{
  text-align:center;font-size:13px;color:var(--gold);letter-spacing:3px;
  text-transform:uppercase;margin-bottom:8px;font-weight:600;
}
.section-title{
  text-align:center;font-size:clamp(22px,4vw,32px);font-weight:800;margin-bottom:48px;
}
.check-list{max-width:600px;margin:0 auto;list-style:none}
.check-list li{
  padding:16px 0;border-bottom:1px solid rgba(255,255,255,.06);
  font-size:17px;display:flex;align-items:center;gap:14px;
}
.check-list li::before{
  content:'';width:20px;height:20px;border:2px solid rgba(255,255,255,.2);
  border-radius:4px;flex-shrink:0;
}
.check-list li:last-child{border-bottom:none}
.check-list .highlight{color:var(--gold);font-weight:700}

/* ===== SOLUTION ===== */
.solution{padding:80px 20px;background:var(--bg)}
.sol-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:24px;margin-top:48px}
.sol-card{
  background:var(--card);border:1px solid var(--card-border);
  border-radius:var(--radius);padding:32px 24px;position:relative;overflow:hidden;
  transition:border-color .3s,transform .3s;
}
.sol-card:hover{border-color:var(--gold);transform:translateY(-4px)}
.sol-card::before{
  content:'';position:absolute;top:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,var(--gold),transparent);
}
.sol-card .sol-icon{font-size:28px;margin-bottom:16px;display:block}
.sol-card h3{font-size:18px;font-weight:700;margin-bottom:8px}
.sol-card p{font-size:14px;color:var(--muted);line-height:1.7}
.sol-card .sol-tag{
  display:inline-block;margin-top:12px;padding:4px 12px;
  background:rgba(212,168,83,.12);color:var(--gold);border-radius:20px;
  font-size:12px;font-weight:600;
}

/* ===== COMPARE ===== */
.compare-sec{padding:80px 20px;background:#111}
.compare-table{
  width:100%;border-collapse:separate;border-spacing:0;
  overflow:hidden;border-radius:var(--radius);
  border:1px solid var(--card-border);margin-top:48px;
}
.compare-table th,.compare-table td{padding:16px 20px;text-align:left;font-size:15px}
.compare-table thead th{
  background:rgba(212,168,83,.1);color:var(--gold);font-weight:700;
  border-bottom:2px solid rgba(212,168,83,.2);font-size:14px;letter-spacing:1px;
}
.compare-table thead th:first-child{text-align:left}
.compare-table thead th:not(:first-child){text-align:center}
.compare-table tbody td{border-bottom:1px solid rgba(255,255,255,.04)}
.compare-table tbody tr:last-child td{border-bottom:none}
.compare-table tbody td:not(:first-child){text-align:center;font-size:18px}
.compare-table tbody tr:hover{background:rgba(212,168,83,.03)}
.c-yes{color:#27ae60;font-weight:700}
.c-no{color:rgba(255,255,255,.15)}
.c-manual{color:var(--muted);font-size:13px}

/* ===== PRICING ===== */
.pricing{padding:80px 20px;background:var(--bg);text-align:center}
.price-card{
  max-width:520px;margin:48px auto 0;
  background:linear-gradient(145deg,rgba(212,168,83,.08),rgba(212,168,83,.02));
  border:2px solid rgba(212,168,83,.25);border-radius:16px;padding:48px 36px;
  position:relative;
}
.price-card .price-badge{
  position:absolute;top:-16px;left:50%;transform:translateX(-50%);
  background:linear-gradient(135deg,var(--gold-light),var(--gold));color:#0d0d0d;
  padding:6px 28px;border-radius:20px;font-size:13px;font-weight:800;letter-spacing:1px;
}
.price-amount{margin-top:20px;margin-bottom:8px}
.price-amount .yen{font-size:20px;font-weight:600;color:var(--muted)}
.price-amount .num{font-size:clamp(48px,10vw,72px);font-weight:900;color:var(--gold);letter-spacing:-2px}
.price-amount .unit{font-size:16px;color:var(--muted)}
.price-tax{font-size:13px;color:var(--muted);margin-bottom:28px}
.price-includes{list-style:none;text-align:left;max-width:320px;margin:0 auto}
.price-includes li{
  padding:10px 0;font-size:15px;display:flex;align-items:center;gap:10px;
  border-bottom:1px solid rgba(255,255,255,.04);
}
.price-includes li:last-child{border-bottom:none}
.price-includes li::before{content:'';font-size:16px}
.price-note{margin-top:20px;font-size:13px;color:var(--muted);font-style:italic}

/* ===== NUMBERS ===== */
.numbers{padding:60px 20px;background:#111}
.num-grid{display:flex;flex-wrap:wrap;justify-content:center;gap:32px}
.num-item{text-align:center;min-width:180px}
.num-item .big{font-size:clamp(36px,6vw,52px);font-weight:900;color:var(--gold)}
.num-item .big .small{font-size:50%;color:var(--text)}
.num-item .label{font-size:14px;color:var(--muted);margin-top:4px}

/* ===== FLOW ===== */
.flow-sec{padding:80px 20px;background:var(--bg)}
.flow-steps{display:flex;flex-wrap:wrap;justify-content:center;gap:0;margin-top:48px}
.flow-step{text-align:center;flex:1;min-width:160px;max-width:200px;padding:0 12px;position:relative}
.flow-step .step-num{
  width:48px;height:48px;border-radius:50%;
  background:linear-gradient(135deg,var(--gold-light),var(--gold));
  color:#0d0d0d;font-weight:800;font-size:20px;
  display:flex;align-items:center;justify-content:center;margin:0 auto 16px;
}
.flow-step h4{font-size:15px;font-weight:700;margin-bottom:4px}
.flow-step p{font-size:13px;color:var(--muted)}
.flow-step:not(:last-child)::after{
  content:'';position:absolute;top:24px;right:-16px;
  width:32px;height:2px;background:rgba(212,168,83,.3);
}

/* ===== TAGLINE ===== */
.tagline{
  padding:64px 20px;text-align:center;
  background:linear-gradient(170deg,#1a0a0a,#0d0d0d);
}
.tagline p{
  font-size:clamp(18px,3.5vw,28px);font-weight:800;line-height:1.6;
}
.tagline .em{
  background:linear-gradient(90deg,var(--gold-light),var(--gold));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
}

/* ===== FORM ===== */
.form-section{padding:80px 20px;background:#111;text-align:center}
.form-box{
  background:rgba(255,255,255,.04);border:1px solid var(--card-border);
  max-width:480px;margin:40px auto 0;border-radius:var(--radius);
  padding:40px 32px;text-align:left;
}
.form-box label{display:block;font-size:13px;font-weight:600;margin-bottom:6px;margin-top:20px;color:var(--muted)}
.form-box label:first-child{margin-top:0}
.form-box .req{color:var(--gold);font-size:11px;margin-left:4px}
.form-box input,.form-box textarea{
  width:100%;padding:14px 16px;background:rgba(255,255,255,.06);
  border:1px solid rgba(255,255,255,.1);border-radius:8px;
  color:var(--text);font-size:15px;font-family:inherit;
  transition:border-color .2s;
}
.form-box input:focus,.form-box textarea:focus{outline:none;border-color:var(--gold)}
.form-box input::placeholder,.form-box textarea::placeholder{color:rgba(255,255,255,.2)}
.form-box textarea{height:80px;resize:vertical}
.form-box .submit-btn{
  margin-top:28px;width:100%;padding:18px;
  background:linear-gradient(135deg,var(--gold-light),var(--gold));
  color:#0d0d0d;border:none;border-radius:8px;font-size:17px;font-weight:800;
  cursor:pointer;letter-spacing:1px;transition:box-shadow .2s,transform .2s;
}
.form-box .submit-btn:hover{box-shadow:0 6px 24px rgba(212,168,83,.4);transform:translateY(-2px)}
.form-box .submit-btn:disabled{opacity:.4;cursor:not-allowed;transform:none}
.form-msg{margin-top:16px;padding:14px;border-radius:8px;text-align:center;font-weight:600;font-size:14px;display:none}
.form-msg.ok{display:block;background:rgba(39,174,96,.15);color:#27ae60;border:1px solid rgba(39,174,96,.3)}
.form-msg.err{display:block;background:rgba(192,57,43,.15);color:#e74c3c;border:1px solid rgba(192,57,43,.3)}
.form-trust{margin-top:20px;font-size:12px;color:var(--muted);text-align:center}

/* ===== FOOTER ===== */
footer{
  text-align:center;padding:40px 20px;color:rgba(255,255,255,.2);
  font-size:12px;background:var(--bg);border-top:1px solid rgba(255,255,255,.04);
}
footer .ig{color:var(--gold);font-weight:600;font-size:14px;display:inline-block;margin-bottom:8px}

/* ===== RESPONSIVE ===== */
@media(max-width:640px){
  .hero{padding:72px 16px 56px}
  .flow-step:not(:last-child)::after{display:none}
  .flow-steps{flex-direction:column;align-items:center;gap:24px}
  .form-box{padding:28px 20px}
  .price-card{padding:40px 24px}
}
</style>
</head>
<body>

<!-- HERO -->
<section class="hero">
  <div class="inner">
    <div class="hero-eyecatch">ガールズバー・キャバクラ専用POS</div>
    <h1>その計算、まだ<br><span class="accent">人</span>がやってますか？</h1>
    <p class="sub">指名 ・ 場内 ・ ドリンクバック</p>
    <p class="sub-strong">すべて自動計算する業務特化のPOS</p>
    <a href="#form" class="cta-btn">
      無料デモに申し込む
      <span class="cta-btn-sub">30秒で完了 / 営業電話なし</span>
    </a>
  </div>
</section>

<!-- PAIN POINTS -->
<section class="pain">
  <div class="inner">
    <div class="section-label">Problem</div>
    <h2 class="section-title">こんなことで困ってませんか？</h2>
    <ul class="check-list">
      <li>指名や場内、同伴の計算が<span class="highlight">毎回大変</span></li>
      <li>ドリンクバックが合わず<span class="highlight">キャストともめる</span></li>
      <li>お店ごとのルールがPOSに合わない</li>
      <li>月末の集計作業で<span class="highlight">深夜まで残業</span>する</li>
      <li>Excel管理が限界</li>
    </ul>
  </div>
</section>

<!-- SOLUTION -->
<section class="solution">
  <div class="inner">
    <div class="section-label">Solution</div>
    <h2 class="section-title">POS Start が、全部解決します</h2>
    <div class="sol-grid">
      <div class="sol-card">
        <span class="sol-icon">&#9889;</span>
        <h3>業務特化・自動計算</h3>
        <p>指名/場内/同伴の給与、キャスト別ドリンクバック、卓・日別売上を全て自動集計。</p>
        <span class="sol-tag">月末の集計がワンクリック</span>
      </div>
      <div class="sol-card">
        <span class="sol-icon">&#9881;</span>
        <h3>お店のルールに対応</h3>
        <p>店ごとのバック率、全員バック/一部バック、特例ルールも柔軟に反映可能。</p>
        <span class="sol-tag">他店と違う運用もOK</span>
      </div>
      <div class="sol-card">
        <span class="sol-icon">&#128187;</span>
        <h3>iPad・パソコン対応</h3>
        <p>レジで操作、事務所で確認。ブラウザベースだからどこからでもアクセス。</p>
        <span class="sol-tag">専用端末の購入不要</span>
      </div>
      <div class="sol-card">
        <span class="sol-icon">&#128101;</span>
        <h3>顧客CRM</h3>
        <p>来店回数、指名キャスト、好みのドリンク、誕生日。常連対応を仕組み化。</p>
        <span class="sol-tag">属人化を排除</span>
      </div>
      <div class="sol-card">
        <span class="sol-icon">&#127870;</span>
        <h3>ボトルキープ管理</h3>
        <p>お客様名・ボトル名・保管期限を記録。期限切れは自動アラート。</p>
        <span class="sol-tag">ボトル紛失ゼロに</span>
      </div>
      <div class="sol-card">
        <span class="sol-icon">&#128179;</span>
        <h3>キャッシュレス決済</h3>
        <p>Stripe連携でカード決済対応。現金・カードの混合支払いもスムーズ。</p>
        <span class="sol-tag">機会損失を防止</span>
      </div>
    </div>
  </div>
</section>

<!-- NUMBERS -->
<section class="numbers">
  <div class="inner">
    <div class="num-grid">
      <div class="num-item">
        <div class="big">月20<span class="small">~</span>30<span class="small">時間</span></div>
        <div class="label">の業務時間を削減</div>
      </div>
      <div class="num-item">
        <div class="big">0<span class="small">件</span></div>
        <div class="label">バック計算のミス</div>
      </div>
      <div class="num-item">
        <div class="big">&#x2193; <span class="small">大幅</span></div>
        <div class="label">キャストトラブル減少</div>
      </div>
    </div>
  </div>
</section>

<!-- COMPARE -->
<section class="compare-sec">
  <div class="inner">
    <div class="section-label">Why POS Start?</div>
    <h2 class="section-title">他のPOSとのちがい</h2>
    <table class="compare-table">
      <thead>
        <tr><th></th><th>汎用POS</th><th style="color:var(--gold)">POS Start</th></tr>
      </thead>
      <tbody>
        <tr><td>業務対応</td><td class="c-manual">手動</td><td class="c-yes">&#10003; 自動</td></tr>
        <tr><td>バック計算</td><td class="c-no">&#x2715;</td><td class="c-yes">&#10003; 自動計算</td></tr>
        <tr><td>店舗ルール</td><td class="c-no">&#x2715;</td><td class="c-yes">&#10003; 対応</td></tr>
        <tr><td>指名/場内/同伴 給与</td><td class="c-no">&#x2715;</td><td class="c-yes">&#10003;</td></tr>
        <tr><td>ボトルキープ管理</td><td class="c-no">&#x2715;</td><td class="c-yes">&#10003;</td></tr>
        <tr><td>顧客CRM</td><td class="c-no">&#x2715;</td><td class="c-yes">&#10003;</td></tr>
        <tr><td>Excel給与出力</td><td class="c-no">&#x2715;</td><td class="c-yes">&#10003;</td></tr>
        <tr><td>アップデート料金</td><td class="c-manual">別途</td><td class="c-yes">&#10003; 月額込み</td></tr>
      </tbody>
    </table>
  </div>
</section>

<!-- PRICING -->
<section class="pricing">
  <div class="inner">
    <div class="section-label">Pricing</div>
    <h2 class="section-title">シンプルな料金体系</h2>
    <div class="price-card">
      <div class="price-badge">ALL-IN-ONE</div>
      <div class="price-amount">
        <span class="yen">月額</span>
        <span class="num">80,000</span>
        <span class="unit">円</span>
      </div>
      <div class="price-tax">（税込）</div>
      <ul class="price-includes">
        <li>初期費用ほぼ無し</li>
        <li>保守サポート込み</li>
        <li>アップデート込み</li>
        <li>お店のルールに合わせた初期設定</li>
        <li>導入後の操作サポート</li>
      </ul>
      <p class="price-note">* 店舗規模により別途ご相談の場合あり</p>
    </div>
  </div>
</section>

<!-- TAGLINE -->
<section class="tagline">
  <p>「レジ」ではなく<br><span class="em">経営を楽にする</span>POS</p>
</section>

<!-- FLOW -->
<section class="flow-sec">
  <div class="inner">
    <div class="section-label">Flow</div>
    <h2 class="section-title">導入の流れ</h2>
    <div class="flow-steps">
      <div class="flow-step">
        <div class="step-num">1</div>
        <h4>お問い合わせ</h4>
        <p>下のフォームから<br>30秒で完了</p>
      </div>
      <div class="flow-step">
        <div class="step-num">2</div>
        <h4>オンラインデモ</h4>
        <p>30分で全機能を<br>ご体験</p>
      </div>
      <div class="flow-step">
        <div class="step-num">3</div>
        <h4>初期設定</h4>
        <p>お店のルールに<br>合わせて設定</p>
      </div>
      <div class="flow-step">
        <div class="step-num">4</div>
        <h4>運用開始</h4>
        <p>最短即日で<br>スタート</p>
      </div>
    </div>
  </div>
</section>

<!-- FORM -->
<section class="form-section" id="form">
  <div class="inner">
    <div class="section-label">Contact</div>
    <h2 class="section-title">無料デモに申し込む</h2>
    <div class="form-box">
      <label>店舗名<span class="req">*必須</span></label>
      <input id="f-store" type="text" placeholder="Bar ○○">

      <label>お名前<span class="req">*必須</span></label>
      <input id="f-name" type="text" placeholder="山田太郎">

      <label>電話番号<span class="req">*必須</span></label>
      <input id="f-phone" type="tel" placeholder="090-1234-5678">

      <label>メールアドレス</label>
      <input id="f-email" type="email" placeholder="info@example.com">

      <label>ご質問・ご要望</label>
      <textarea id="f-msg" placeholder="気になる機能や店舗の状況など"></textarea>

      <button class="submit-btn" id="submitBtn" onclick="submitForm()">無料デモに申し込む</button>
      <div class="form-msg" id="formMsg"></div>
      <p class="form-trust">営業電話は一切しません。まずはデモでご確認ください。</p>
    </div>
  </div>
</section>

<!-- FOOTER -->
<footer>
  <div class="ig">@nexus_800</div><br>
  &copy; 2026 POS Start &mdash; 「レジ」ではなく経営を楽にするPOS
</footer>

<script>
async function submitForm(){
  const store=document.getElementById('f-store').value.trim();
  const name=document.getElementById('f-name').value.trim();
  const phone=document.getElementById('f-phone').value.trim();
  const email=document.getElementById('f-email').value.trim();
  const message=document.getElementById('f-msg').value.trim();
  const msg=document.getElementById('formMsg');
  const btn=document.getElementById('submitBtn');

  if(!store||!name||!phone){
    msg.className='form-msg err';
    msg.textContent='店舗名・お名前・電話番号は必須です';
    msg.style.display='block';
    return;
  }
  btn.disabled=true;btn.textContent='送信中...';
  try{
    const res=await fetch('/api/demo-request',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({store_name:store,name,phone,email,message})
    });
    if(!res.ok) throw new Error('送信に失敗しました');
    msg.className='form-msg ok';
    msg.textContent='お申込みありがとうございます！2営業日以内にご連絡いたします。';
    msg.style.display='block';
    btn.textContent='送信完了';
    ['f-store','f-name','f-phone','f-email','f-msg'].forEach(id=>document.getElementById(id).value='');
  }catch(e){
    msg.className='form-msg err';msg.textContent=e.message;msg.style.display='block';
    btn.disabled=false;btn.textContent='無料デモに申し込む';
  }
}
</script>
</body>
</html>"""
