"""management.py — Dsystem風マネジメントダッシュボード
キャスト成績・給率・店舗売上分析 + /ui/management
"""
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException, Header, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import func

from db_shared import Base, SessionLocal, require_role

JST = ZoneInfo("Asia/Tokyo")
router = APIRouter(tags=["management"])
ADMIN_ROLES = ["owner", "manager"]

# ─────────────────────────── キャスト成績分析 ───────────────────────────

def compute_cast_performance(db, store_id: int, year: int, month: int) -> List[dict]:
    """Dsystem風キャスト成績レポート"""
    from pos import Cast, Session, Nomination, Order, Attendance, Payment
    from cast_salary import CastSalaryConfig, DrinkBackRecord

    casts = db.query(Cast).filter_by(store_id=store_id, is_active=True).all()

    # 当月のセッション（closed）を取得
    all_sessions = db.query(Session).filter(
        Session.store_id == store_id,
        Session.status == "closed"
    ).all()
    month_sessions = [
        s for s in all_sessions
        if s.start_time and s.start_time.replace(tzinfo=timezone.utc).astimezone(JST).year == year
        and s.start_time.replace(tzinfo=timezone.utc).astimezone(JST).month == month
    ]
    total_sessions = len(month_sessions)
    total_guests = sum(s.guest_count for s in month_sessions)

    # 全セッション売上を計算
    from pos import compute_bill
    session_bills = {}
    total_revenue = 0
    for s in month_sessions:
        try:
            bill = compute_bill(db, s)
            session_bills[s.id] = bill
            total_revenue += bill["total"]
        except Exception:
            pass

    results = []
    for cast in casts:
        cfg = db.query(CastSalaryConfig).filter_by(cast_id=cast.id).first()

        # 指名データ
        all_noms = db.query(Nomination).filter_by(store_id=store_id, cast_id=cast.id).all()
        month_noms = [n for n in all_noms
                      if n.created_at.replace(tzinfo=timezone.utc).astimezone(JST).year == year
                      and n.created_at.replace(tzinfo=timezone.utc).astimezone(JST).month == month]
        hon_count = sum(1 for n in month_noms if n.nomi_type == "hon")
        jyonai_count = sum(1 for n in month_noms if n.nomi_type == "jyonai")
        dohan_count = sum(1 for n in month_noms if n.nomi_type == "dohan")
        total_noms = hon_count + jyonai_count + dohan_count

        # 指名率
        hon_rate = (hon_count / total_sessions * 100) if total_sessions > 0 else 0
        jyonai_rate = (jyonai_count / total_sessions * 100) if total_sessions > 0 else 0
        dohan_rate = (dohan_count / total_sessions * 100) if total_sessions > 0 else 0

        # このキャストが関与したセッションの売上（指名があったセッション）
        nom_session_ids = set(n.session_id for n in month_noms)
        cast_revenue = sum(session_bills.get(sid, {}).get("total", 0) for sid in nom_session_ids)

        # 給率 = キャスト総支給 / キャスト関与売上
        # 勤務時間
        attends = db.query(Attendance).filter_by(
            store_id=store_id, person_type="cast", person_id=cast.id
        ).all()
        hours_worked = 0.0
        attendance_days = set()
        for a in attends:
            if a.clock_in and a.clock_out:
                ci_jst = a.clock_in.replace(tzinfo=timezone.utc).astimezone(JST)
                if ci_jst.year == year and ci_jst.month == month:
                    hours_worked += (a.clock_out - a.clock_in).total_seconds() / 3600
                    attendance_days.add(ci_jst.date())

        hourly_rate = cfg.hourly_rate if cfg else 0
        time_pay = hours_worked * hourly_rate

        # ドリンクバック
        drink_backs = db.query(DrinkBackRecord).filter_by(
            store_id=store_id, cast_id=cast.id
        ).all()
        drink_back_total = sum(
            r.amount for r in drink_backs
            if r.created_at.replace(tzinfo=timezone.utc).astimezone(JST).year == year
            and r.created_at.replace(tzinfo=timezone.utc).astimezone(JST).month == month
        )

        # 指名料
        nom_fee_hon = cfg.nom_fee_hon if cfg else 0
        nom_fee_jyonai = cfg.nom_fee_jyonai if cfg else 0
        nom_fee_dohan = cfg.nom_fee_dohan if cfg else 0
        nom_pay = hon_count * nom_fee_hon + jyonai_count * nom_fee_jyonai + dohan_count * nom_fee_dohan

        total_pay = time_pay + drink_back_total + nom_pay

        # 給率 = 総支給 / 関与売上
        pay_rate = (total_pay / cast_revenue * 100) if cast_revenue > 0 else 0
        # 売上貢献率 = 関与売上 / 全売上
        revenue_share = (cast_revenue / total_revenue * 100) if total_revenue > 0 else 0

        results.append({
            "cast_id": cast.id,
            "cast_name": cast.name,
            "rank": cast.rank or "",
            # 出勤
            "attendance_days": len(attendance_days),
            "hours_worked": round(hours_worked, 1),
            # 指名
            "hon_count": hon_count,
            "jyonai_count": jyonai_count,
            "dohan_count": dohan_count,
            "total_noms": total_noms,
            # 指名率（%）
            "hon_rate": round(hon_rate, 1),
            "jyonai_rate": round(jyonai_rate, 1),
            "dohan_rate": round(dohan_rate, 1),
            # 金額
            "time_pay": round(time_pay),
            "drink_back": round(drink_back_total),
            "nom_pay": round(nom_pay),
            "total_pay": round(total_pay),
            # 売上
            "cast_revenue": round(cast_revenue),
            "pay_rate": round(pay_rate, 1),       # 給率（%）
            "revenue_share": round(revenue_share, 1),  # 売上貢献率（%）
        })

    # 売上貢献額でソート（降順）
    results.sort(key=lambda x: x["cast_revenue"], reverse=True)
    return results

# ─────────────────────────── 店舗売上分析 ───────────────────────────

def compute_store_analytics(db, store_id: int, year: int, month: int) -> dict:
    """店舗の月次売上分析"""
    from pos import Session, Payment, compute_bill

    all_sessions = db.query(Session).filter(
        Session.store_id == store_id,
        Session.status == "closed"
    ).all()
    month_sessions = [
        s for s in all_sessions
        if s.start_time and s.start_time.replace(tzinfo=timezone.utc).astimezone(JST).year == year
        and s.start_time.replace(tzinfo=timezone.utc).astimezone(JST).month == month
    ]

    total_revenue = 0
    total_guests = 0
    daily_revenue = {}       # "YYYY-MM-DD" -> amount
    hourly_revenue = {}      # hour -> amount
    payment_methods = {"cash": 0, "card": 0, "qr": 0}

    for s in month_sessions:
        try:
            bill = compute_bill(db, s)
        except Exception:
            continue

        total_revenue += bill["total"]
        total_guests += s.guest_count

        # 日別
        start_jst = s.start_time.replace(tzinfo=timezone.utc).astimezone(JST)
        day_key = start_jst.strftime("%Y-%m-%d")
        daily_revenue[day_key] = daily_revenue.get(day_key, 0) + bill["total"]

        # 時間帯別
        hour = start_jst.hour
        hourly_revenue[hour] = hourly_revenue.get(hour, 0) + bill["total"]

        # 支払い方法
        for p in s.payments:
            m = p.method or "cash"
            payment_methods[m] = payment_methods.get(m, 0) + int(p.amount)

    session_count = len(month_sessions)
    avg_per_guest = round(total_revenue / total_guests) if total_guests > 0 else 0
    avg_per_group = round(total_revenue / session_count) if session_count > 0 else 0

    # 日別を日付順にソート
    daily_sorted = sorted(daily_revenue.items())
    # 時間帯を時間順にソート
    hourly_sorted = sorted(hourly_revenue.items())

    return {
        "year": year,
        "month": month,
        "total_revenue": total_revenue,
        "session_count": session_count,
        "total_guests": total_guests,
        "avg_per_guest": avg_per_guest,
        "avg_per_group": avg_per_group,
        "daily": [{"date": d, "revenue": r} for d, r in daily_sorted],
        "hourly": [{"hour": h, "revenue": r} for h, r in hourly_sorted],
        "payment_methods": payment_methods,
    }

# ─────────────────────────── API Routes ───────────────────────────

@router.get("/management/cast-performance")
def api_cast_performance(store_id: int,
                         year: int = Query(default=None),
                         month: int = Query(default=None),
                         x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    now_jst = datetime.now(tz=JST)
    y = year or now_jst.year
    m = month or now_jst.month
    db = SessionLocal()
    try:
        data = compute_cast_performance(db, store_id, y, m)
        return {"year": y, "month": m, "store_id": store_id, "casts": data}
    finally:
        db.close()

@router.get("/management/store-analytics")
def api_store_analytics(store_id: int,
                        year: int = Query(default=None),
                        month: int = Query(default=None),
                        x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    now_jst = datetime.now(tz=JST)
    y = year or now_jst.year
    m = month or now_jst.month
    db = SessionLocal()
    try:
        data = compute_store_analytics(db, store_id, y, m)
        return data
    finally:
        db.close()

# ─────────────────────────── Management UI ───────────────────────────

@router.get("/ui/management", response_class=HTMLResponse)
def ui_management():
    return HTMLResponse(r"""
<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>マネジメント - Girls Bar POS</title>
<style>
:root{--bg:#0b1220;--card:#0f172a;--line:#1f2937;--text:#e5e7eb;--muted:#b0bec5;--accent:#0ea5e9;--green:#22c55e;--red:#ef4444;--gold:#f59e0b;--purple:#a855f7}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text)}
header{position:sticky;top:0;z-index:40;display:flex;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);background:rgba(11,18,32,.95);backdrop-filter:blur(6px);flex-wrap:wrap}
header h1{margin:0;font-size:17px}
.nav a{color:var(--accent);text-decoration:none;font-size:13px;padding:5px 10px;border-radius:8px;border:1px solid var(--line)}
.container{max-width:1200px;margin:0 auto;padding:20px 16px;display:flex;flex-direction:column;gap:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px;overflow:hidden}
.card h2{margin:0 0 14px;font-size:15px;border-bottom:1px solid var(--line);padding-bottom:10px}
label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--muted)}
input,select{font-size:14px;padding:7px 10px;border-radius:8px;border:1px solid #263244;background:#0a1220;color:var(--text)}
.btn{cursor:pointer;font-size:14px;padding:8px 16px;border-radius:10px;border:1px solid #334155;background:#111827;color:var(--text)}
.btn.solid{background:var(--accent);border-color:var(--accent);color:#001018;font-weight:700}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--muted);font-weight:500;font-size:11px;white-space:nowrap}
td.num{text-align:right;font-family:monospace;font-size:12px}
td.rank-1{color:var(--gold);font-weight:900}
td.rank-2{color:#94a3b8;font-weight:700}
td.rank-3{color:#cd7f32;font-weight:700}

/* KPI カード */
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}
.kpi{background:#0a1624;border:1px solid var(--line);border-radius:12px;padding:14px;text-align:center}
.kpi .label{font-size:11px;color:var(--muted);margin-bottom:4px}
.kpi .value{font-size:24px;font-weight:900;font-family:monospace}
.kpi .sub{font-size:11px;color:var(--muted);margin-top:4px}

/* バー */
.bar-wrap{display:flex;align-items:center;gap:6px}
.bar{height:16px;border-radius:8px;min-width:2px}
.bar.blue{background:var(--accent)}
.bar.green{background:var(--green)}
.bar.gold{background:var(--gold)}
.bar.purple{background:var(--purple)}

/* セクションタブ */
.section-tabs{display:flex;gap:0;border-bottom:2px solid var(--line);margin-bottom:16px}
.section-tab{padding:10px 16px;cursor:pointer;font-size:14px;border-bottom:2px solid transparent;margin-bottom:-2px;color:var(--muted)}
.section-tab.active{border-color:var(--accent);color:var(--text);font-weight:700}
.section-pane{display:none}
.section-pane.active{display:block}

/* 支払方法グラフ */
.pie-row{display:flex;gap:16px;align-items:center;justify-content:center;flex-wrap:wrap}
.pie-item{text-align:center}
.pie-item .dot{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:4px}

@media(max-width:700px){
  .kpi-grid{grid-template-columns:repeat(2,1fr)}
  table{display:block;overflow-x:auto}
  th,td{padding:5px 4px;white-space:nowrap}
  .container{padding:12px 10px}
  header{gap:8px}
}
</style></head><body>
<header>
  <h1>マネジメント</h1>
  <div class="nav" style="display:flex;gap:6px;margin-left:auto">
    <a href="/ui">フロア</a>
    <a href="/ui/pricing">料金設定</a>
    <a href="/ui/salary">給与管理</a>
    <a href="/ui/weather">天気/シフト</a>
    <a href="/ui/subscription">サブスク</a>
  </div>
</header>

<div class="container">
  <div class="row">
    <label style="flex-direction:row;align-items:center;gap:6px">店舗 <input id="storeId" type="number" value="1" style="width:70px"></label>
    <label style="flex-direction:row;align-items:center;gap:6px">年 <input id="year" type="number" style="width:90px"></label>
    <label style="flex-direction:row;align-items:center;gap:6px">月 <input id="month" type="number" min="1" max="12" style="width:60px"></label>
    <button class="btn solid" onclick="loadAll()">分析</button>
  </div>

  <!-- セクションタブ -->
  <div class="section-tabs">
    <div class="section-tab active" data-sec="cast">キャスト成績</div>
    <div class="section-tab" data-sec="store">店舗売上</div>
  </div>

  <!-- ===== キャスト成績 ===== -->
  <div class="section-pane active" id="sec-cast">

    <!-- キャストKPI -->
    <div class="kpi-grid" id="castKpis"></div>

    <!-- 成績ランキング -->
    <div class="card">
      <h2>キャスト成績ランキング</h2>
      <div style="overflow-x:auto">
      <table>
        <thead><tr>
          <th>#</th><th>名前</th><th>ランク</th>
          <th>出勤日</th><th>勤務h</th>
          <th>本指名</th><th>場内</th><th>同伴</th><th>指名計</th>
          <th>本指名率</th><th>場内率</th><th>同伴率</th>
          <th>時給分</th><th>ドリンクバック</th><th>指名料</th><th>総支給</th>
          <th>売上貢献</th><th>給率</th><th>売上シェア</th>
        </tr></thead>
        <tbody id="castBody"></tbody>
      </table>
      </div>
    </div>

    <!-- 給率分析バー -->
    <div class="card">
      <h2>給率 比較（キャスト総支給 ÷ 関与売上）</h2>
      <div id="payRateBars"></div>
    </div>

    <!-- 売上貢献バー -->
    <div class="card">
      <h2>売上貢献額ランキング</h2>
      <div id="revenueBars"></div>
    </div>
  </div>

  <!-- ===== 店舗売上 ===== -->
  <div class="section-pane" id="sec-store">

    <div class="kpi-grid" id="storeKpis"></div>

    <!-- 日別売上 -->
    <div class="card">
      <h2>日別売上推移</h2>
      <div id="dailyBars" style="display:flex;align-items:flex-end;gap:3px;height:180px;padding:10px 0;border-bottom:1px solid var(--line)"></div>
      <div id="dailyLabels" style="display:flex;gap:3px;font-size:9px;color:var(--muted);margin-top:4px"></div>
    </div>

    <!-- 時間帯別売上 -->
    <div class="card">
      <h2>時間帯別売上</h2>
      <div id="hourlyBars" style="display:flex;align-items:flex-end;gap:4px;height:160px;padding:10px 0;border-bottom:1px solid var(--line)"></div>
      <div id="hourlyLabels" style="display:flex;gap:4px;font-size:10px;color:var(--muted);margin-top:4px"></div>
    </div>

    <!-- 支払方法 -->
    <div class="card">
      <h2>支払方法内訳</h2>
      <div class="pie-row" id="paymentPie"></div>
    </div>
  </div>
</div>

<script>
const $=id=>document.getElementById(id);
const now=new Date();
$('year').value=now.getFullYear();
$('month').value=now.getMonth()+1;
const yen=v=>`¥${Math.round(v||0).toLocaleString()}`;
const pct=v=>`${(v||0).toFixed(1)}%`;

// タブ
document.querySelectorAll('.section-tab').forEach(tab=>{
  tab.addEventListener('click',()=>{
    document.querySelectorAll('.section-tab').forEach(t=>t.classList.remove('active'));
    document.querySelectorAll('.section-pane').forEach(p=>p.classList.remove('active'));
    tab.classList.add('active');
    $('sec-'+tab.dataset.sec)?.classList.add('active');
  });
});

async function api(path){
  const r=await fetch(path,{headers:{'X-Role':'owner'}});
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}

async function loadAll(){
  const s=$('storeId').value,y=$('year').value,m=$('month').value;
  await Promise.all([loadCast(s,y,m),loadStore(s,y,m)]);
}

async function loadCast(s,y,m){
  try{
    const d=await api(`/management/cast-performance?store_id=${s}&year=${y}&month=${m}`);
    const casts=d.casts||[];

    // KPIs
    const totalPay=casts.reduce((a,c)=>a+c.total_pay,0);
    const totalRev=casts.reduce((a,c)=>a+c.cast_revenue,0);
    const totalNoms=casts.reduce((a,c)=>a+c.total_noms,0);
    const avgPayRate=casts.length?casts.reduce((a,c)=>a+c.pay_rate,0)/casts.length:0;
    $('castKpis').innerHTML=`
      <div class="kpi"><div class="label">在籍キャスト</div><div class="value">${casts.length}</div><div class="sub">名</div></div>
      <div class="kpi"><div class="label">総支給額</div><div class="value" style="font-size:18px">${yen(totalPay)}</div></div>
      <div class="kpi"><div class="label">総指名数</div><div class="value">${totalNoms}</div><div class="sub">件</div></div>
      <div class="kpi"><div class="label">平均給率</div><div class="value" style="color:${avgPayRate>50?'var(--red)':avgPayRate>35?'var(--gold)':'var(--green)'}">${pct(avgPayRate)}</div></div>
    `;

    // テーブル
    const tb=$('castBody');tb.innerHTML='';
    casts.forEach((c,i)=>{
      const rankCls=i<3?`rank-${i+1}`:'';
      const tr=document.createElement('tr');
      tr.innerHTML=`
        <td class="${rankCls}">${i+1}</td><td><b>${c.cast_name}</b></td><td>${c.rank}</td>
        <td class="num">${c.attendance_days}</td><td class="num">${c.hours_worked}</td>
        <td class="num">${c.hon_count}</td><td class="num">${c.jyonai_count}</td><td class="num">${c.dohan_count}</td>
        <td class="num"><b>${c.total_noms}</b></td>
        <td class="num" style="color:${c.hon_rate>20?'var(--green)':'inherit'}">${pct(c.hon_rate)}</td>
        <td class="num">${pct(c.jyonai_rate)}</td>
        <td class="num">${pct(c.dohan_rate)}</td>
        <td class="num">${yen(c.time_pay)}</td><td class="num">${yen(c.drink_back)}</td>
        <td class="num">${yen(c.nom_pay)}</td><td class="num"><b>${yen(c.total_pay)}</b></td>
        <td class="num">${yen(c.cast_revenue)}</td>
        <td class="num" style="color:${c.pay_rate>50?'var(--red)':c.pay_rate>35?'var(--gold)':'var(--green)'}"><b>${pct(c.pay_rate)}</b></td>
        <td class="num">${pct(c.revenue_share)}</td>`;
      tb.appendChild(tr);
    });

    // 給率バー
    const maxPR=Math.max(...casts.map(c=>c.pay_rate),1);
    $('payRateBars').innerHTML=casts.map(c=>{
      const w=Math.max(2,c.pay_rate/maxPR*100);
      const color=c.pay_rate>50?'var(--red)':c.pay_rate>35?'var(--gold)':'var(--green)';
      return `<div class="bar-wrap" style="margin:6px 0">
        <span style="width:80px;font-size:12px">${c.cast_name}</span>
        <div class="bar" style="width:${w}%;background:${color}"></div>
        <span style="font-size:12px;font-family:monospace;color:${color}">${pct(c.pay_rate)}</span>
      </div>`;
    }).join('');

    // 売上貢献バー
    const maxRev=Math.max(...casts.map(c=>c.cast_revenue),1);
    $('revenueBars').innerHTML=casts.map((c,i)=>{
      const w=Math.max(2,c.cast_revenue/maxRev*100);
      const colors=['var(--gold)','#94a3b8','#cd7f32','var(--accent)','var(--purple)'];
      const color=colors[i]||'var(--accent)';
      return `<div class="bar-wrap" style="margin:6px 0">
        <span style="width:80px;font-size:12px">${c.cast_name}</span>
        <div class="bar" style="width:${w}%;background:${color}"></div>
        <span style="font-size:12px;font-family:monospace">${yen(c.cast_revenue)}</span>
      </div>`;
    }).join('');

  }catch(e){console.error(e);alert('キャスト成績取得エラー: '+e.message)}
}

async function loadStore(s,y,m){
  try{
    const d=await api(`/management/store-analytics?store_id=${s}&year=${y}&month=${m}`);

    // KPIs
    $('storeKpis').innerHTML=`
      <div class="kpi"><div class="label">月間売上</div><div class="value" style="font-size:20px;color:var(--green)">${yen(d.total_revenue)}</div></div>
      <div class="kpi"><div class="label">来店組数</div><div class="value">${d.session_count}</div><div class="sub">組</div></div>
      <div class="kpi"><div class="label">来店人数</div><div class="value">${d.total_guests}</div><div class="sub">名</div></div>
      <div class="kpi"><div class="label">客単価</div><div class="value" style="font-size:18px">${yen(d.avg_per_guest)}</div></div>
      <div class="kpi"><div class="label">組単価</div><div class="value" style="font-size:18px">${yen(d.avg_per_group)}</div></div>
    `;

    // 日別バーチャート
    const daily=d.daily||[];
    const maxD=Math.max(...daily.map(x=>x.revenue),1);
    $('dailyBars').innerHTML=daily.map(x=>{
      const h=Math.max(2,x.revenue/maxD*160);
      return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end">
        <div style="font-size:9px;font-family:monospace;margin-bottom:2px">${x.revenue>0?Math.round(x.revenue/1000)+'k':''}</div>
        <div style="width:100%;height:${h}px;background:var(--accent);border-radius:4px 4px 0 0;min-width:8px"></div>
      </div>`;
    }).join('');
    $('dailyLabels').innerHTML=daily.map(x=>`<div style="flex:1;text-align:center">${x.date.slice(8)}</div>`).join('');

    // 時間帯別バーチャート
    const hourly=d.hourly||[];
    const maxH=Math.max(...hourly.map(x=>x.revenue),1);
    $('hourlyBars').innerHTML=hourly.map(x=>{
      const h=Math.max(2,x.revenue/maxH*140);
      return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end">
        <div style="font-size:9px;font-family:monospace;margin-bottom:2px">${x.revenue>0?Math.round(x.revenue/1000)+'k':''}</div>
        <div style="width:100%;height:${h}px;background:var(--purple);border-radius:4px 4px 0 0;min-width:12px"></div>
      </div>`;
    }).join('');
    $('hourlyLabels').innerHTML=hourly.map(x=>`<div style="flex:1;text-align:center">${x.hour}時</div>`).join('');

    // 支払方法
    const pm=d.payment_methods||{};
    const pmTotal=Object.values(pm).reduce((a,v)=>a+v,0)||1;
    const pmColors={cash:'var(--green)',card:'var(--accent)',qr:'var(--purple)'};
    const pmLabels={cash:'現金',card:'カード',qr:'QR'};
    $('paymentPie').innerHTML=Object.entries(pm).map(([k,v])=>{
      const p=(v/pmTotal*100).toFixed(1);
      return `<div class="pie-item">
        <div><span class="dot" style="background:${pmColors[k]||'#999'}"></span>${pmLabels[k]||k}</div>
        <div style="font-size:20px;font-weight:900;font-family:monospace">${p}%</div>
        <div style="font-size:11px;color:var(--muted)">${yen(v)}</div>
      </div>`;
    }).join('');

  }catch(e){console.error(e);alert('店舗分析エラー: '+e.message)}
}

loadAll();
</script>
</body></html>
""")
