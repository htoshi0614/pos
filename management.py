"""management.py — Dsystem風マネジメントダッシュボード
キャスト成績・給率・店舗売上分析・指名ランキング・目標設定・
時間帯別客数ヒートマップ・リピート率分析 + /ui/management
"""
from datetime import datetime, timezone, timedelta, date
from typing import Optional, List
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException, Header, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Float, DateTime
from collections import defaultdict

from db_shared import Base, SessionLocal, require_role

JST = ZoneInfo("Asia/Tokyo")
router = APIRouter(tags=["management"])
ADMIN_ROLES = ["owner", "manager"]

# ---------- 目標設定モデル ----------
class CastGoal(Base):
    __tablename__ = "cast_goals"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, index=True)
    cast_id = Column(Integer, index=True)
    year_month = Column(String, index=True)  # YYYY-MM
    target_nominations = Column(Integer, default=0)
    target_sales = Column(Float, default=0)
    target_attendance = Column(Integer, default=0)

class CastGoalIn(BaseModel):
    store_id: int
    cast_id: int
    year_month: str
    target_nominations: int = 0
    target_sales: float = 0
    target_attendance: int = 0

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

        # ドリンクバック＆ボトルバック
        all_backs = db.query(DrinkBackRecord).filter_by(
            store_id=store_id, cast_id=cast.id
        ).all()
        drink_back_total = 0.0
        bottle_back_total = 0.0
        for r in all_backs:
            rj = r.created_at.replace(tzinfo=timezone.utc).astimezone(JST)
            if rj.year == year and rj.month == month:
                if getattr(r, 'back_type', 'drink') == 'bottle':
                    bottle_back_total += r.amount
                else:
                    drink_back_total += r.amount

        # 指名料
        nom_fee_hon = cfg.nom_fee_hon if cfg else 0
        nom_fee_jyonai = cfg.nom_fee_jyonai if cfg else 0
        nom_fee_dohan = cfg.nom_fee_dohan if cfg else 0
        nom_pay = hon_count * nom_fee_hon + jyonai_count * nom_fee_jyonai + dohan_count * nom_fee_dohan

        total_pay = time_pay + drink_back_total + bottle_back_total + nom_pay

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
            "bottle_back": round(bottle_back_total),
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

# ─────────────────────────── 時間帯×曜日 ヒートマップ ───────────────────────────

def compute_heatmap(db, store_id: int, year: int, month: int) -> dict:
    from pos import Session
    all_sessions = db.query(Session).filter(
        Session.store_id == store_id,
        Session.status == "closed"
    ).all()
    month_sessions = [
        s for s in all_sessions
        if s.start_time and s.start_time.replace(tzinfo=timezone.utc).astimezone(JST).year == year
        and s.start_time.replace(tzinfo=timezone.utc).astimezone(JST).month == month
    ]
    # 曜日(0=月..6=日) × 時間帯(0-23) → 来客数
    heatmap = defaultdict(lambda: defaultdict(int))
    for s in month_sessions:
        jst = s.start_time.replace(tzinfo=timezone.utc).astimezone(JST)
        dow = jst.weekday()  # 0=Mon
        hour = jst.hour
        heatmap[dow][hour] += s.guest_count

    rows = []
    for dow in range(7):
        row = {"dow": dow, "hours": {h: heatmap[dow][h] for h in range(24)}}
        rows.append(row)
    return {"heatmap": rows}

# ─────────────────────────── リピート率分析 ───────────────────────────

def compute_repeat_analysis(db, store_id: int, year: int, month: int) -> dict:
    """新規 vs リピーターの分析（顧客台帳ベース）"""
    try:
        from customer_crm import CustomerProfile, VisitLog
        profiles = db.query(CustomerProfile).filter_by(store_id=store_id).all()
        ym = f"{year:04d}-{month:02d}"

        new_count = 0
        repeat_count = 0
        total_new_spent = 0
        total_repeat_spent = 0

        for p in profiles:
            visits = db.query(VisitLog).filter_by(customer_id=p.id).all()
            month_visits = [v for v in visits if v.visit_date and v.visit_date.startswith(ym)]
            if not month_visits:
                continue
            # 初来店月かどうか
            if p.first_visit and p.first_visit.startswith(ym):
                new_count += 1
                total_new_spent += sum(v.spent for v in month_visits)
            else:
                repeat_count += 1
                total_repeat_spent += sum(v.spent for v in month_visits)

        total = new_count + repeat_count
        repeat_rate = (repeat_count / total * 100) if total > 0 else 0

        # 再来店サイクル（全顧客の平均来店間隔）
        intervals = []
        for p in profiles:
            visits = db.query(VisitLog).filter_by(customer_id=p.id).order_by(VisitLog.visit_date.asc()).all()
            dates = sorted(set(v.visit_date for v in visits if v.visit_date))
            for i in range(1, len(dates)):
                try:
                    d1 = date.fromisoformat(dates[i-1])
                    d2 = date.fromisoformat(dates[i])
                    intervals.append((d2 - d1).days)
                except Exception:
                    pass
        avg_interval = round(sum(intervals) / len(intervals), 1) if intervals else 0

        return {
            "new_count": new_count, "repeat_count": repeat_count,
            "total": total, "repeat_rate": round(repeat_rate, 1),
            "avg_new_spent": round(total_new_spent / new_count) if new_count > 0 else 0,
            "avg_repeat_spent": round(total_repeat_spent / repeat_count) if repeat_count > 0 else 0,
            "avg_revisit_days": avg_interval,
        }
    except ImportError:
        return {"new_count": 0, "repeat_count": 0, "total": 0, "repeat_rate": 0,
                "avg_new_spent": 0, "avg_repeat_spent": 0, "avg_revisit_days": 0,
                "note": "customer_crm モジュール未導入"}

# ─────────────────────────── API Routes ───────────────────────────

# 目標設定
@router.post("/management/goals")
def set_goal(payload: CastGoalIn, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        existing = db.query(CastGoal).filter_by(
            store_id=payload.store_id, cast_id=payload.cast_id, year_month=payload.year_month
        ).first()
        if existing:
            existing.target_nominations = payload.target_nominations
            existing.target_sales = payload.target_sales
            existing.target_attendance = payload.target_attendance
        else:
            g = CastGoal(**(payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()))
            db.add(g)
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@router.get("/management/goals")
def get_goals(store_id: int, year_month: str, x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        rows = db.query(CastGoal).filter_by(store_id=store_id, year_month=year_month).all()
        return [{"cast_id": g.cast_id, "target_nominations": g.target_nominations,
                 "target_sales": g.target_sales, "target_attendance": g.target_attendance} for g in rows]
    finally:
        db.close()

# ヒートマップ
@router.get("/management/heatmap")
def api_heatmap(store_id: int, year: int = None, month: int = None,
                x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    now_jst = datetime.now(tz=JST)
    y = year or now_jst.year; m = month or now_jst.month
    db = SessionLocal()
    try:
        return compute_heatmap(db, store_id, y, m)
    finally:
        db.close()

# リピート率
@router.get("/management/repeat-analysis")
def api_repeat_analysis(store_id: int, year: int = None, month: int = None,
                        x_role: Optional[str] = Header(None, alias="X-Role")):
    require_role(x_role, ADMIN_ROLES)
    now_jst = datetime.now(tz=JST)
    y = year or now_jst.year; m = month or now_jst.month
    db = SessionLocal()
    try:
        return compute_repeat_analysis(db, store_id, y, m)
    finally:
        db.close()

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
:root{--bg:#fafafa;--card:#ffffff;--card2:#ffffff;--line:#eaeaef;--border:#eaeaef;--text:#0a0a0f;--ink:#0a0a0f;--muted:#8a8a95;--body:#4a4a55;--accent:#d64583;--accent-soft:#fdf0f7;--accent-dark:#b03468;--gold:#c9a96e;--gold-soft:#faf3e3;--warn:#f59e0b;--amber:#f59e0b;--err:#ef4444;--red:#ef4444;--ok:#22c55e;--green:#22c55e;--blue:#3b82f6;--purple:#a855f7;}
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


/* === 分析画面 v2: 視認性 + 重要データ強調 === */
.container{max-width:1120px}
/* KPIカード: 白ベース + 上辺アクセントバー + 大型数値 */
.kpi-grid{gap:14px;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));margin-bottom:18px}
.kpi{background:#ffffff !important;border:1px solid #eaeaef !important;border-radius:14px;padding:18px 20px;position:relative;overflow:hidden;box-shadow:0 1px 3px rgba(10,10,15,.04),0 2px 8px rgba(10,10,15,.05);transition:transform .2s,box-shadow .2s}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#d64583,#c9a96e)}
.kpi:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(214,69,131,.1)}
.kpi .label{font-size:11px;color:#8a8a95 !important;font-weight:700;letter-spacing:.04em;margin-bottom:6px}
.kpi .value{font-family:'Inter',sans-serif;font-size:28px !important;font-weight:800;color:#0a0a0f;letter-spacing:-.01em;line-height:1.15}
.kpi .sub{font-size:11px;color:#8a8a95 !important;margin-top:2px}
/* 各タブ先頭KPI(=最重要指標)を強調 */
.kpi-grid .kpi:first-child{background:linear-gradient(135deg,#fff 0%,#fdf0f7 100%) !important;border-color:#d64583 !important;box-shadow:0 4px 16px rgba(214,69,131,.1)}
.kpi-grid .kpi:first-child .value{font-size:34px !important;background:linear-gradient(135deg,#0a0a0f,#d64583);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
/* テーブル行ホバー: 黒→薄ピンク */
tr:hover td{background:#fdf0f7 !important}
/* バー / ゴール トラックをライト化 */
.bar-wrap{background:#f3f3f6 !important}
.bar-label{color:#0a0a0f !important;font-weight:600}
.bar-value{color:#0a0a0f !important}
.goal-bar{background:#f3f3f6 !important}
.goal-text{color:#0a0a0f !important}
/* ランキング上位行の淡い色付け */
tr.gold-row td{background:linear-gradient(90deg,#fffaf0,transparent) !important}
tr.silver-row td{background:linear-gradient(90deg,#f6f7f9,transparent) !important}
tr.bronze-row td{background:linear-gradient(90deg,#fdf6f0,transparent) !important}
.rank-1{background:#c9a96e !important;color:#fff !important}
.rank-2{background:#c0c0c0 !important;color:#0a0a0f !important}
.rank-3{background:#cd7f32 !important;color:#fff !important}
.rank-other{background:#f3f3f6 !important;color:#8a8a95 !important}
/* セクションタブ: アクティブ明確化 */
.section-tabs{border-bottom:2px solid #eaeaef;gap:4px}
.section-tab{background:#ffffff !important;border:1px solid #eaeaef !important;border-bottom:none !important;color:#8a8a95 !important;font-weight:600;padding:11px 20px;transition:all .2s}
.section-tab:hover{color:#d64583 !important;background:#fdf0f7 !important}
.section-tab.active{background:#d64583 !important;color:#fff !important;border-color:#d64583 !important;box-shadow:0 -2px 10px rgba(214,69,131,.18)}
/* カード/見出しの階層強化 */
.card{box-shadow:0 1px 3px rgba(10,10,15,.04),0 2px 8px rgba(10,10,15,.05);border-radius:16px}
.card h2{font-size:15px;font-weight:800;color:#0a0a0f;letter-spacing:-.01em}
th{color:#8a8a95 !important;font-weight:700 !important;text-transform:none}
.pie-dot{box-shadow:0 1px 3px rgba(0,0,0,.1)}


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
    <div class="section-tab" data-sec="heatmap">ヒートマップ</div>
    <div class="section-tab" data-sec="repeat">リピート率</div>
    <div class="section-tab" data-sec="goals">目標設定</div>
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
          <th>時給分</th><th>ドリンクバック</th><th>ボトルバック</th><th>指名料</th><th>総支給</th>
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

  <!-- ===== ヒートマップ ===== -->
  <div class="section-pane" id="sec-heatmap">
    <div class="card">
      <h2>時間帯 × 曜日 来客ヒートマップ</h2>
      <div style="overflow-x:auto">
        <table id="heatmapTable" style="text-align:center">
          <thead id="heatmapHead"></thead>
          <tbody id="heatmapBody"></tbody>
        </table>
      </div>
      <div style="margin-top:12px;display:flex;gap:8px;align-items:center;font-size:11px;color:var(--muted)">
        <span>少</span>
        <div style="width:20px;height:12px;background:#0a1624;border:1px solid var(--line)"></div>
        <div style="width:20px;height:12px;background:#14532d"></div>
        <div style="width:20px;height:12px;background:#22c55e"></div>
        <div style="width:20px;height:12px;background:#f59e0b"></div>
        <div style="width:20px;height:12px;background:#ef4444"></div>
        <span>多</span>
      </div>
    </div>
  </div>

  <!-- ===== リピート率 ===== -->
  <div class="section-pane" id="sec-repeat">
    <div class="kpi-grid" id="repeatKpis"></div>
    <div class="card">
      <h2>新規 vs リピーター</h2>
      <div id="repeatBars" style="display:flex;height:40px;border-radius:8px;overflow:hidden;margin-bottom:12px"></div>
      <div id="repeatDetail" style="font-size:13px"></div>
    </div>
  </div>

  <!-- ===== 目標設定 ===== -->
  <div class="section-pane" id="sec-goals">
    <div class="card">
      <h2>キャスト月間目標</h2>
      <div style="overflow-x:auto">
        <table>
          <thead><tr><th>キャスト</th><th>指名目標</th><th>達成</th><th>進捗</th><th>売上目標</th><th>達成</th><th>進捗</th><th>出勤目標</th><th>達成</th><th>操作</th></tr></thead>
          <tbody id="goalsBody"></tbody>
        </table>
      </div>
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
  const tk=sessionStorage.getItem('pos_token')||'';
  const r=await fetch(path,{headers:{'X-Role':'owner','X-Token':tk}});
  if(r.status===401){sessionStorage.clear();window.location.href='/';return;}
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}

async function loadAll(){
  const s=$('storeId').value,y=$('year').value,m=$('month').value;
  await Promise.all([loadCast(s,y,m),loadStore(s,y,m),loadHeatmap(s,y,m),loadRepeat(s,y,m),loadGoals(s,y,m)]);
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
        <td class="num">${yen(c.time_pay)}</td><td class="num">${yen(c.drink_back)}</td><td class="num">${yen(c.bottle_back||0)}</td>
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

// ヒートマップ
async function loadHeatmap(s,y,m){
  try{
    const d=await api(`/management/heatmap?store_id=${s}&year=${y}&month=${m}`);
    const hm=d.heatmap||[];
    const dows=['月','火','水','木','金','土','日'];
    // ヘッダー: 時間帯
    const hours=[];for(let h=17;h<=27;h++) hours.push(h>=24?h-24:h); // 17時〜翌3時
    $('heatmapHead').innerHTML='<tr><th></th>'+hours.map(h=>`<th style="font-size:11px">${h}時</th>`).join('')+'</tr>';
    // 全体の最大値
    let maxVal=1;
    hm.forEach(row=>{ Object.values(row.hours).forEach(v=>{ if(v>maxVal) maxVal=v; }); });
    // ボディ
    $('heatmapBody').innerHTML=hm.map((row,i)=>{
      const cells=hours.map(h=>{
        const v=row.hours[h]||0;
        const intensity=v/maxVal;
        let bg='#0a1624';
        if(intensity>0.8) bg='#ef4444';
        else if(intensity>0.6) bg='#f59e0b';
        else if(intensity>0.3) bg='#22c55e';
        else if(intensity>0) bg='#14532d';
        return `<td style="background:${bg};color:${intensity>0.5?'#fff':'var(--muted)'};font-size:12px;min-width:36px;padding:8px 4px">${v||''}</td>`;
      }).join('');
      return `<tr><td style="font-weight:700;font-size:13px;color:${i>=5?'var(--accent)':'var(--text)'}">${dows[i]}</td>${cells}</tr>`;
    }).join('');
  }catch(e){console.error(e)}
}

// リピート率
async function loadRepeat(s,y,m){
  try{
    const d=await api(`/management/repeat-analysis?store_id=${s}&year=${y}&month=${m}`);
    $('repeatKpis').innerHTML=`
      <div class="kpi"><div class="label">リピート率</div><div class="value" style="color:var(--green)">${pct(d.repeat_rate)}</div></div>
      <div class="kpi"><div class="label">新規</div><div class="value">${d.new_count}</div><div class="sub">名</div></div>
      <div class="kpi"><div class="label">リピーター</div><div class="value">${d.repeat_count}</div><div class="sub">名</div></div>
      <div class="kpi"><div class="label">平均再来店</div><div class="value">${d.avg_revisit_days}</div><div class="sub">日</div></div>
      <div class="kpi"><div class="label">新規 客単価</div><div class="value" style="font-size:18px">${yen(d.avg_new_spent)}</div></div>
      <div class="kpi"><div class="label">リピーター 客単価</div><div class="value" style="font-size:18px">${yen(d.avg_repeat_spent)}</div></div>
    `;
    const total=d.new_count+d.repeat_count||1;
    const newPct=d.new_count/total*100;
    const repPct=d.repeat_count/total*100;
    $('repeatBars').innerHTML=`
      <div style="width:${newPct}%;background:var(--accent);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700">${d.new_count>0?'新規 '+d.new_count:''}</div>
      <div style="width:${repPct}%;background:var(--green);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700">${d.repeat_count>0?'リピ '+d.repeat_count:''}</div>
    `;
    $('repeatDetail').innerHTML=d.note?`<div style="color:var(--muted)">${d.note}</div>`:'';
  }catch(e){console.error(e)}
}

// 目標設定
let castPerfCache=[];
async function loadGoals(s,y,m){
  try{
    const ym=`${y}-${String(m).padStart(2,'0')}`;
    const [goals,perf]=await Promise.all([
      api(`/management/goals?store_id=${s}&year_month=${ym}`),
      api(`/management/cast-performance?store_id=${s}&year=${y}&month=${m}`)
    ]);
    castPerfCache=perf.casts||[];
    const goalMap={};
    goals.forEach(g=>goalMap[g.cast_id]=g);
    const tb=$('goalsBody');tb.innerHTML='';
    castPerfCache.forEach(c=>{
      const g=goalMap[c.cast_id]||{target_nominations:0,target_sales:0,target_attendance:0};
      const nomProg=g.target_nominations?Math.min(100,c.total_noms/g.target_nominations*100):0;
      const saleProg=g.target_sales?Math.min(100,c.cast_revenue/g.target_sales*100):0;
      const progBar=(pct)=>`<div style="width:100px;height:8px;background:#1e293b;border-radius:4px;overflow:hidden"><div style="width:${pct}%;height:100%;background:${pct>=100?'var(--green)':pct>=70?'var(--gold)':'var(--accent)'};border-radius:4px"></div></div>`;
      tb.innerHTML+=`<tr>
        <td><b>${c.cast_name}</b></td>
        <td><input type="number" value="${g.target_nominations}" style="width:60px" id="gn-${c.cast_id}"></td>
        <td class="num">${c.total_noms}</td><td>${progBar(nomProg)}</td>
        <td><input type="number" value="${g.target_sales}" style="width:90px" id="gs-${c.cast_id}"></td>
        <td class="num">${yen(c.cast_revenue)}</td><td>${progBar(saleProg)}</td>
        <td><input type="number" value="${g.target_attendance}" style="width:60px" id="ga-${c.cast_id}"></td>
        <td class="num">${c.attendance_days}</td>
        <td><button class="btn" style="font-size:11px;padding:4px 8px" onclick="saveGoal(${c.cast_id})">保存</button></td>
      </tr>`;
    });
  }catch(e){console.error(e)}
}

async function saveGoal(castId){
  const s=$('storeId').value,y=$('year').value,m=$('month').value;
  const ym=`${y}-${String(m).padStart(2,'0')}`;
  const body={store_id:parseInt(s),cast_id:castId,year_month:ym,
    target_nominations:parseInt(document.getElementById('gn-'+castId).value)||0,
    target_sales:parseFloat(document.getElementById('gs-'+castId).value)||0,
    target_attendance:parseInt(document.getElementById('ga-'+castId).value)||0};
  await fetch('/management/goals',{method:'POST',headers:{'Content-Type':'application/json','X-Role':'owner'},body:JSON.stringify(body)});
  loadGoals(s,y,m);
}

loadAll();
</script>
</body></html>
""")
