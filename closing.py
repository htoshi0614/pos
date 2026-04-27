"""closing.py — 仮締め / 本締め / Zレポート / CSV・PDF出力"""

from datetime import datetime, date, timezone, timedelta
from typing import Optional, List, Dict, Literal
import json, csv, io

from fastapi import APIRouter, HTTPException, Header, Query
from fastapi.responses import HTMLResponse, StreamingResponse, Response
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, Text, ForeignKey, func
from sqlalchemy.orm import joinedload
from zoneinfo import ZoneInfo

from db_shared import Base, SessionLocal, require_role

router = APIRouter()
ADMIN_ROLES = ["owner", "manager"]
JST = ZoneInfo("Asia/Tokyo")

# ---------- Models ----------
class Closing(Base):
    __tablename__ = "closings"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, index=True)
    business_date = Column(String, index=True)  # YYYY-MM-DD
    status = Column(String, default="preliminary")  # preliminary / final
    closed_by = Column(String, default="")
    preliminary_at = Column(DateTime, nullable=True)
    final_at = Column(DateTime, nullable=True)
    unlocked_by = Column(String, default="")
    unlocked_at = Column(DateTime, nullable=True)
    report_json = Column(Text, default="{}")  # Zレポート全データ保存

# ---------- Helpers ----------

def _business_date_range(biz_date: str):
    """営業日(YYYY-MM-DD) → UTC start/end を返す（JST 0:00-23:59）"""
    d = date.fromisoformat(biz_date)
    start_jst = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=JST)
    end_jst = start_jst + timedelta(days=1)
    return start_jst.astimezone(timezone.utc), end_jst.astimezone(timezone.utc)


def _build_z_report(db, store_id: int, biz_date: str) -> Dict:
    """確定(closed)セッションのみでZレポートを構築"""
    from pos import Session, Order, Payment, Item, Table, Cast, Nomination, compute_bill

    start_utc, end_utc = _business_date_range(biz_date)

    sessions = (
        db.query(Session)
        .options(
            joinedload(Session.orders).joinedload(Order.item),
            joinedload(Session.orders).joinedload(Order.cast),
            joinedload(Session.payments),
            joinedload(Session.table),
            joinedload(Session.nominations).joinedload(Nomination.cast),
        )
        .filter(
            Session.store_id == store_id,
            Session.start_time >= start_utc,
            Session.start_time < end_utc,
            Session.status == "closed",
        )
        .all()
    )

    # --- 集計 ---
    total_sales = 0
    total_guests = 0
    by_payment: Dict[str, float] = {}
    by_table: Dict[str, Dict] = {}
    by_cast: Dict[str, Dict] = {}
    by_item: Dict[str, Dict] = {}
    by_hour: Dict[int, Dict] = {}

    for s in sessions:
        bill = compute_bill(db, s)
        session_total = int(bill.get("total", 0))
        total_sales += session_total
        total_guests += s.guest_count

        # 支払手段別
        for p in s.payments:
            method = p.method or "other"
            by_payment[method] = by_payment.get(method, 0) + p.amount

        # 卓別
        tname = s.table.name if s.table else "不明"
        if tname not in by_table:
            by_table[tname] = {"sessions": 0, "guests": 0, "sales": 0}
        by_table[tname]["sessions"] += 1
        by_table[tname]["guests"] += s.guest_count
        by_table[tname]["sales"] += session_total

        # キャスト別（注文のcast_id + 指名）
        cast_appeared: set = set()  # このセッションに登場したキャストID
        for o in s.orders:
            if o.cast_id:
                cast_appeared.add(o.cast_id)
                cast = db.query(Cast).get(o.cast_id)
                cname = cast.name if cast else f"ID:{o.cast_id}"
                if cname not in by_cast:
                    by_cast[cname] = {"orders_amount": 0, "nomi_count": 0, "sessions": 0, "total": 0}
                by_cast[cname]["orders_amount"] += o.unit_price * o.qty
                by_cast[cname]["total"] += o.unit_price * o.qty
        for nom in s.nominations:
            if nom.cast_id:
                cast_appeared.add(nom.cast_id)
            cast = db.query(Cast).get(nom.cast_id) if nom.cast_id else None
            cname = cast.name if cast else f"ID:{nom.cast_id}"
            if cname not in by_cast:
                by_cast[cname] = {"orders_amount": 0, "nomi_count": 0, "sessions": 0, "total": 0}
            by_cast[cname]["nomi_count"] += 1
            by_cast[cname]["total"] += nom.fee or 0
        # セッション件数（キャストが登場したセッション数）
        for cid in cast_appeared:
            cast = db.query(Cast).get(cid)
            cname = cast.name if cast else f"ID:{cid}"
            if cname not in by_cast:
                by_cast[cname] = {"orders_amount": 0, "nomi_count": 0, "sessions": 0, "total": 0}
            by_cast[cname]["sessions"] += 1

        # 商品別
        for o in s.orders:
            iname = o.item.name if o.item else f"item#{o.item_id}"
            cat = o.item.category if o.item else "other"
            key = f"{iname}"
            if key not in by_item:
                by_item[key] = {"category": cat, "qty": 0, "amount": 0}
            by_item[key]["qty"] += o.qty
            by_item[key]["amount"] += o.unit_price * o.qty

        # 時間帯別 (入店時刻のJST時)
        start_jst = s.start_time.replace(tzinfo=timezone.utc).astimezone(JST)
        h = start_jst.hour
        if h not in by_hour:
            by_hour[h] = {"sessions": 0, "guests": 0, "sales": 0}
        by_hour[h]["sessions"] += 1
        by_hour[h]["guests"] += s.guest_count
        by_hour[h]["sales"] += session_total

    # 未収金（paid < total のセッション）
    total_paid = sum(by_payment.values())
    unpaid = max(0, total_sales - int(total_paid))

    return {
        "store_id": store_id,
        "business_date": biz_date,
        "session_count": len(sessions),
        "guest_count": total_guests,
        "total_sales": total_sales,
        "total_paid": int(total_paid),
        "unpaid": unpaid,
        "by_payment": by_payment,
        "by_table": by_table,
        "by_cast": by_cast,
        "by_item": by_item,
        "by_hour": {str(k): v for k, v in sorted(by_hour.items())},
    }


# ---------- API ----------

@router.get("/closing/z-report")
def get_z_report(
    store_id: int,
    business_date: Optional[str] = None,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    """Zレポート取得（確定売上ベース）"""
    require_role(x_role, ["owner", "manager", "cashier"])
    if not business_date:
        business_date = datetime.now(tz=JST).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        # 本締め済みなら保存済みレポートを返す
        closing = db.query(Closing).filter_by(store_id=store_id, business_date=business_date).first()
        if closing and closing.status == "final" and closing.report_json:
            return json.loads(closing.report_json)
        return _build_z_report(db, store_id, business_date)
    finally:
        db.close()


@router.post("/closing/preliminary")
def do_preliminary(
    store_id: int,
    business_date: Optional[str] = None,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    """仮締め"""
    require_role(x_role, ADMIN_ROLES)
    if not business_date:
        business_date = datetime.now(tz=JST).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        existing = db.query(Closing).filter_by(store_id=store_id, business_date=business_date).first()
        if existing and existing.status == "final":
            raise HTTPException(400, "本締め済みです。解除してからやり直してください。")

        report = _build_z_report(db, store_id, business_date)

        if existing:
            existing.status = "preliminary"
            existing.closed_by = x_role
            existing.preliminary_at = datetime.now(tz=timezone.utc)
            existing.report_json = json.dumps(report, ensure_ascii=False)
        else:
            existing = Closing(
                store_id=store_id,
                business_date=business_date,
                status="preliminary",
                closed_by=x_role,
                preliminary_at=datetime.now(tz=timezone.utc),
                report_json=json.dumps(report, ensure_ascii=False),
            )
            db.add(existing)
        db.commit()
        return {"ok": True, "status": "preliminary", "report": report}
    finally:
        db.close()


@router.post("/closing/final")
def do_final(
    store_id: int,
    business_date: Optional[str] = None,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    """本締め（仮締め後のみ）"""
    require_role(x_role, ADMIN_ROLES)
    if not business_date:
        business_date = datetime.now(tz=JST).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        existing = db.query(Closing).filter_by(store_id=store_id, business_date=business_date).first()
        if not existing or existing.status != "preliminary":
            raise HTTPException(400, "先に仮締めを行ってください。")

        # 最新レポートで更新
        report = _build_z_report(db, store_id, business_date)
        existing.status = "final"
        existing.final_at = datetime.now(tz=timezone.utc)
        existing.report_json = json.dumps(report, ensure_ascii=False)
        db.commit()

        # ポイントメール送信（バックグラウンド）
        try:
            from point_mail import trigger_point_mail
            trigger_point_mail(store_id, business_date, report)
        except Exception as e:
            print(f"[closing] point_mail trigger error: {e}")

        return {"ok": True, "status": "final", "report": report}
    finally:
        db.close()


@router.post("/closing/unlock")
def unlock_closing(
    store_id: int,
    business_date: str,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    """本締め解除（owner/managerのみ）"""
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        existing = db.query(Closing).filter_by(store_id=store_id, business_date=business_date).first()
        if not existing:
            raise HTTPException(404, "締めレコードが見つかりません")
        if existing.status != "final":
            raise HTTPException(400, "本締めされていません")
        existing.status = "preliminary"
        existing.unlocked_by = x_role
        existing.unlocked_at = datetime.now(tz=timezone.utc)
        db.commit()
        return {"ok": True, "status": "preliminary"}
    finally:
        db.close()


@router.get("/closing/status")
def closing_status(
    store_id: int,
    business_date: Optional[str] = None,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    """締めステータス取得"""
    require_role(x_role, ["owner", "manager", "cashier", "staff"])
    if not business_date:
        business_date = datetime.now(tz=JST).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        c = db.query(Closing).filter_by(store_id=store_id, business_date=business_date).first()
        if not c:
            return {"status": "open", "business_date": business_date}
        return {
            "status": c.status,
            "business_date": c.business_date,
            "preliminary_at": c.preliminary_at.isoformat() if c.preliminary_at else None,
            "final_at": c.final_at.isoformat() if c.final_at else None,
            "closed_by": c.closed_by,
        }
    finally:
        db.close()


@router.get("/closing/history")
def closing_history(
    store_id: int,
    limit: int = 30,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    """過去の締め履歴"""
    require_role(x_role, ADMIN_ROLES)
    db = SessionLocal()
    try:
        rows = (
            db.query(Closing)
            .filter_by(store_id=store_id)
            .order_by(Closing.business_date.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "business_date": r.business_date,
                "status": r.status,
                "closed_by": r.closed_by,
                "preliminary_at": r.preliminary_at.isoformat() if r.preliminary_at else None,
                "final_at": r.final_at.isoformat() if r.final_at else None,
                "total_sales": json.loads(r.report_json or "{}").get("total_sales", 0),
            }
            for r in rows
        ]
    finally:
        db.close()


# ---------- CSV Export ----------

@router.get("/closing/z-report/csv")
def export_csv(
    store_id: int,
    business_date: Optional[str] = None,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    require_role(x_role, ADMIN_ROLES)
    if not business_date:
        business_date = datetime.now(tz=JST).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        closing = db.query(Closing).filter_by(store_id=store_id, business_date=business_date).first()
        if closing and closing.report_json:
            report = json.loads(closing.report_json)
        else:
            report = _build_z_report(db, store_id, business_date)
    finally:
        db.close()

    buf = io.StringIO()
    buf.write('\ufeff')  # BOM for Excel
    w = csv.writer(buf)

    w.writerow(["Zレポート", business_date, f"店舗ID:{store_id}"])
    w.writerow([])

    # サマリー
    w.writerow(["■ サマリー"])
    w.writerow(["セッション数", report["session_count"]])
    w.writerow(["来客数", report["guest_count"]])
    w.writerow(["売上合計", report["total_sales"]])
    w.writerow(["入金合計", report["total_paid"]])
    w.writerow(["未収", report["unpaid"]])
    w.writerow([])

    # 支払手段別
    w.writerow(["■ 支払手段別"])
    w.writerow(["手段", "金額"])
    for method, amount in report.get("by_payment", {}).items():
        w.writerow([method, int(amount)])
    w.writerow([])

    # 卓別
    w.writerow(["■ 卓別"])
    w.writerow(["卓名", "組数", "来客数", "売上"])
    for tname, d in report.get("by_table", {}).items():
        w.writerow([tname, d["sessions"], d["guests"], d["sales"]])
    w.writerow([])

    # キャスト別
    w.writerow(["■ キャスト別"])
    w.writerow(["キャスト", "指名数", "売上"])
    for cname, d in report.get("by_cast", {}).items():
        w.writerow([cname, d["nomi_count"], d["total"]])
    w.writerow([])

    # 商品別
    w.writerow(["■ 商品別"])
    w.writerow(["商品名", "カテゴリ", "数量", "金額"])
    for iname, d in report.get("by_item", {}).items():
        w.writerow([iname, d["category"], d["qty"], d["amount"]])
    w.writerow([])

    # 時間帯別
    w.writerow(["■ 時間帯別"])
    w.writerow(["時間帯", "組数", "来客数", "売上"])
    for h, d in report.get("by_hour", {}).items():
        w.writerow([f"{h}時台", d["sessions"], d["guests"], d["sales"]])

    buf.seek(0)
    filename = f"z_report_{business_date}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------- PDF Export ----------

@router.get("/closing/z-report/pdf")
def export_pdf(
    store_id: int,
    business_date: Optional[str] = None,
    x_role: Optional[str] = Header(None, alias="X-Role"),
):
    """Zレポート PDF出力（HTML→ブラウザ印刷）"""
    require_role(x_role, ADMIN_ROLES)
    if not business_date:
        business_date = datetime.now(tz=JST).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        closing = db.query(Closing).filter_by(store_id=store_id, business_date=business_date).first()
        if closing and closing.report_json:
            report = json.loads(closing.report_json)
        else:
            report = _build_z_report(db, store_id, business_date)
    finally:
        db.close()

    def yen(v):
        return f"¥{int(v):,}"

    def tbl(headers, rows):
        h = "".join(f"<th>{c}</th>" for c in headers)
        body = ""
        for row in rows:
            body += "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
        return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"

    payment_rows = [[m, yen(a)] for m, a in report.get("by_payment", {}).items()]
    table_rows = [[t, d["sessions"], d["guests"], yen(d["sales"])] for t, d in report.get("by_table", {}).items()]
    cast_rows = [[c, d["nomi_count"], yen(d["total"])] for c, d in report.get("by_cast", {}).items()]
    item_rows = [[i, d["category"], d["qty"], yen(d["amount"])] for i, d in report.get("by_item", {}).items()]
    hour_rows = [[f"{h}時台", d["sessions"], d["guests"], yen(d["sales"])] for h, d in report.get("by_hour", {}).items()]

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Zレポート {business_date}</title>
<style>
@media print {{ @page {{ size: A4; margin: 12mm; }} }}
body {{ font-family: 'Helvetica Neue',sans-serif; font-size: 12px; color: #111; max-width: 700px; margin: 0 auto; padding: 20px; }}
h1 {{ font-size: 18px; border-bottom: 2px solid #111; padding-bottom: 6px; }}
h2 {{ font-size: 14px; margin-top: 18px; color: #333; border-left: 4px solid #0ea5e9; padding-left: 8px; }}
table {{ width: 100%; border-collapse: collapse; margin: 8px 0 16px; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: right; }}
th {{ background: #f0f4f8; text-align: center; font-weight: 600; }}
td:first-child {{ text-align: left; }}
.summary {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin: 12px 0; }}
.summary div {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px 12px; }}
.summary .label {{ font-size: 11px; color: #64748b; }}
.summary .val {{ font-size: 18px; font-weight: 700; }}
.no-print {{ text-align: center; margin: 16px 0; }}
@media print {{ .no-print {{ display: none; }} }}
</style></head><body>
<h1>Zレポート</h1>
<p>営業日: <b>{business_date}</b> ／ 店舗ID: {report['store_id']}</p>

<div class="summary">
  <div><div class="label">セッション数</div><div class="val">{report['session_count']}</div></div>
  <div><div class="label">来客数</div><div class="val">{report['guest_count']}</div></div>
  <div><div class="label">売上合計</div><div class="val">{yen(report['total_sales'])}</div></div>
  <div><div class="label">入金合計</div><div class="val">{yen(report['total_paid'])}</div></div>
  <div><div class="label">未収金</div><div class="val" style="color:#ef4444">{yen(report['unpaid'])}</div></div>
</div>

<h2>支払手段別</h2>
{tbl(["手段","金額"], payment_rows) if payment_rows else "<p>データなし</p>"}

<h2>卓別</h2>
{tbl(["卓名","組数","来客数","売上"], table_rows) if table_rows else "<p>データなし</p>"}

<h2>キャスト別</h2>
{tbl(["キャスト","指名数","売上"], cast_rows) if cast_rows else "<p>データなし</p>"}

<h2>商品別</h2>
{tbl(["商品名","カテゴリ","数量","金額"], item_rows) if item_rows else "<p>データなし</p>"}

<h2>時間帯別</h2>
{tbl(["時間帯","組数","来客数","売上"], hour_rows) if hour_rows else "<p>データなし</p>"}

<div class="no-print">
  <button onclick="window.print()" style="padding:10px 24px;font-size:14px;cursor:pointer;border-radius:8px;border:1px solid #0ea5e9;background:#0ea5e9;color:#fff">PDF保存 / 印刷</button>
  <button onclick="window.close()" style="padding:10px 24px;font-size:14px;cursor:pointer;border-radius:8px;border:1px solid #ccc;background:#fff;margin-left:8px">閉じる</button>
</div>
</body></html>"""
    return HTMLResponse(html)


# ---------- 締めUI ----------

@router.get("/ui/closing", response_class=HTMLResponse)
def closing_ui():
    return HTMLResponse(r"""<!DOCTYPE html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>締め・Zレポート</title>
<style>
:root{--bg:#0f172a;--card:#1e293b;--border:#334155;--text:#e2e8f0;--muted:#b0bec5;--accent:#0ea5e9;--warn:#f59e0b;--err:#ef4444;--ok:#22c55e}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Helvetica Neue',sans-serif;padding:20px;max-width:960px;margin:0 auto}
h1{font-size:22px;margin-bottom:16px;display:flex;align-items:center;gap:10px}
.badge{font-size:12px;padding:3px 10px;border-radius:12px;font-weight:600}
.badge.open{background:#334155;color:#94a3b8}
.badge.preliminary{background:#f59e0b22;color:#f59e0b;border:1px solid #f59e0b44}
.badge.final{background:#22c55e22;color:#22c55e;border:1px solid #22c55e44}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
.summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:12px 0}
.stat{background:#0f172a;border:1px solid var(--border);border-radius:8px;padding:10px 14px}
.stat .label{font-size:11px;color:var(--muted)}
.stat .val{font-size:20px;font-weight:700;margin-top:2px}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}
.btn{padding:8px 18px;border-radius:8px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer;font-size:13px;font-weight:600;transition:.2s}
.btn:hover{background:#334155}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.primary:hover{opacity:.85}
.btn.warn{background:var(--warn);border-color:var(--warn);color:#000}
.btn.danger{background:var(--err);border-color:var(--err);color:#fff}
.btn:disabled{opacity:.4;cursor:not-allowed}
table{width:100%;border-collapse:collapse;margin:8px 0;font-size:13px}
th,td{border:1px solid var(--border);padding:6px 10px}
th{background:#0f172a;text-align:center;font-weight:600;font-size:12px;color:var(--muted)}
td{text-align:right}
td:first-child{text-align:left}
.tabs{display:flex;gap:4px;margin:12px 0;flex-wrap:wrap}
.tab{padding:6px 14px;border-radius:8px 8px 0 0;border:1px solid var(--border);border-bottom:none;background:transparent;color:var(--muted);cursor:pointer;font-size:12px}
.tab.active{background:var(--card);color:var(--text);font-weight:600}
.tab-body{border:1px solid var(--border);border-radius:0 8px 8px 8px;padding:12px;background:var(--card)}
.history-row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)}
.history-row:last-child{border-bottom:none}
.controls{display:flex;gap:10px;align-items:center;margin-bottom:16px;flex-wrap:wrap}
select,input[type=date]{background:var(--card);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-size:13px}
.toast{position:fixed;top:16px;right:16px;padding:10px 20px;border-radius:8px;font-size:13px;z-index:9999;animation:fadeIn .3s}
.toast.ok{background:#22c55e;color:#fff}
.toast.err{background:#ef4444;color:#fff}
@keyframes fadeIn{from{opacity:0;transform:translateY(-10px)}to{opacity:1;transform:translateY(0)}}
</style></head><body>

<h1>締め・Zレポート <span id="statusBadge" class="badge open">未締め</span></h1>

<div class="controls">
  <label>店舗ID: <input id="storeId" type="number" value="1" style="width:60px"></label>
  <label>営業日: <input id="bizDate" type="date"></label>
  <button class="btn primary" onclick="loadReport()">読み込み</button>
</div>

<div class="card">
  <h2 style="font-size:15px;margin-bottom:8px">サマリー</h2>
  <div class="summary-grid" id="summaryGrid">
    <div class="stat"><div class="label">セッション数</div><div class="val" id="sSessions">-</div></div>
    <div class="stat"><div class="label">来客数</div><div class="val" id="sGuests">-</div></div>
    <div class="stat"><div class="label">売上合計</div><div class="val" id="sSales">-</div></div>
    <div class="stat"><div class="label">入金合計</div><div class="val" id="sPaid">-</div></div>
    <div class="stat"><div class="label">未収金</div><div class="val" id="sUnpaid" style="color:var(--err)">-</div></div>
  </div>
</div>

<div class="actions">
  <button class="btn warn" id="btnPrelim" onclick="doPreliminary()">仮締め</button>
  <button class="btn primary" id="btnFinal" onclick="doFinal()">本締め</button>
  <button class="btn danger" id="btnUnlock" onclick="doUnlock()" style="display:none">本締め解除</button>
  <span style="flex:1"></span>
  <button class="btn" onclick="exportCSV()">CSV出力</button>
  <button class="btn" onclick="exportPDF()">PDF出力</button>
</div>

<div class="tabs" id="tabBar">
  <div class="tab active" data-tab="payment">支払手段別</div>
  <div class="tab" data-tab="table">卓別</div>
  <div class="tab" data-tab="cast">キャスト別</div>
  <div class="tab" data-tab="item">商品別</div>
  <div class="tab" data-tab="hour">時間帯別</div>
  <div class="tab" data-tab="history">締め履歴</div>
</div>
<div class="tab-body" id="tabBody"></div>

<script>
const $=id=>document.getElementById(id);
const ROLE='owner'; // UI操作はowner/manager前提
let report=null, closingStatus='open';

function toast(msg,type='ok'){
  const d=document.createElement('div');d.className='toast '+type;d.textContent=msg;
  document.body.appendChild(d);setTimeout(()=>d.remove(),3000);
}
function yen(v){return '¥'+(Math.round(v||0)).toLocaleString();}

async function api(path,opts={}){
  const tk=sessionStorage.getItem('pos_token')||'';
  const h={'X-Role':ROLE,'X-Token':tk,...(opts.headers||{})};
  if(opts.body&&typeof opts.body==='object'){h['Content-Type']='application/json';opts.body=JSON.stringify(opts.body);}
  const r=await fetch(path,{...opts,headers:h});
  if(r.status===401){sessionStorage.clear();window.location.href='/';return;}
  if(!r.ok){const t=await r.text();throw new Error(t);}
  return r.json();
}

function initDate(){
  const d=new Date();$('bizDate').value=d.toLocaleDateString('sv-SE');// YYYY-MM-DD
}

async function loadStatus(){
  try{
    const s=await api(`/closing/status?store_id=${$('storeId').value}&business_date=${$('bizDate').value}`);
    closingStatus=s.status||'open';
    const badge=$('statusBadge');
    badge.textContent={open:'未締め',preliminary:'仮締め',final:'本締め'}[closingStatus]||closingStatus;
    badge.className='badge '+closingStatus;
    // ボタン制御
    $('btnPrelim').disabled=(closingStatus==='final');
    $('btnFinal').disabled=(closingStatus!=='preliminary');
    $('btnUnlock').style.display=(closingStatus==='final')?'inline-block':'none';
  }catch{}
}

async function loadReport(){
  try{
    report=await api(`/closing/z-report?store_id=${$('storeId').value}&business_date=${$('bizDate').value}`);
    renderSummary();
    await loadStatus();
    switchTab('payment');
  }catch(e){toast(e.message,'err');}
}

function renderSummary(){
  if(!report)return;
  $('sSessions').textContent=report.session_count;
  $('sGuests').textContent=report.guest_count;
  $('sSales').textContent=yen(report.total_sales);
  $('sPaid').textContent=yen(report.total_paid);
  $('sUnpaid').textContent=yen(report.unpaid);
}

// --- tabs ---
document.querySelectorAll('.tab').forEach(t=>{
  t.addEventListener('click',()=>switchTab(t.dataset.tab));
});
function switchTab(tab){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.tab===tab));
  if(tab==='history'){renderHistory();return;}
  if(!report){$('tabBody').innerHTML='<p style="color:var(--muted)">先にレポートを読み込んでください</p>';return;}
  const renderers={payment:renderPayment,table:renderTable,cast:renderCast,item:renderItem,hour:renderHour};
  (renderers[tab]||renderers.payment)();
}

function makeTable(headers,rows){
  if(!rows.length)return '<p style="color:var(--muted)">データなし</p>';
  let h=headers.map(c=>`<th>${c}</th>`).join('');
  let b=rows.map(r=>'<tr>'+r.map(c=>`<td>${c}</td>`).join('')+'</tr>').join('');
  return `<table><thead><tr>${h}</tr></thead><tbody>${b}</tbody></table>`;
}

function renderPayment(){
  const rows=Object.entries(report.by_payment||{}).map(([m,a])=>[m,yen(a)]);
  $('tabBody').innerHTML='<h3 style="font-size:14px;margin-bottom:8px">支払手段別</h3>'+makeTable(['手段','金額'],rows);
}
function renderTable(){
  const rows=Object.entries(report.by_table||{}).map(([t,d])=>[t,d.sessions,d.guests,yen(d.sales)]);
  $('tabBody').innerHTML='<h3 style="font-size:14px;margin-bottom:8px">卓別</h3>'+makeTable(['卓名','組数','来客数','売上'],rows);
}
function renderCast(){
  const rows=Object.entries(report.by_cast||{}).map(([c,d])=>[c, d.sessions||0, d.nomi_count||0, yen(d.total||0)]);
  $('tabBody').innerHTML='<h3 style="font-size:14px;margin-bottom:8px">キャスト別</h3>'+makeTable(['キャスト','組数','指名数','売上'],rows);
}
function renderItem(){
  const rows=Object.entries(report.by_item||{}).map(([i,d])=>[i,d.category,d.qty,yen(d.amount)]);
  $('tabBody').innerHTML='<h3 style="font-size:14px;margin-bottom:8px">商品別</h3>'+makeTable(['商品名','カテゴリ','数量','金額'],rows);
}
function renderHour(){
  const rows=Object.entries(report.by_hour||{}).map(([h,d])=>[h+'時台',d.sessions,d.guests,yen(d.sales)]);
  $('tabBody').innerHTML='<h3 style="font-size:14px;margin-bottom:8px">時間帯別</h3>'+makeTable(['時間帯','組数','来客数','売上'],rows);
}

async function renderHistory(){
  try{
    const list=await api(`/closing/history?store_id=${$('storeId').value}`);
    if(!list.length){$('tabBody').innerHTML='<p style="color:var(--muted)">履歴なし</p>';return;}
    $('tabBody').innerHTML='<h3 style="font-size:14px;margin-bottom:8px">締め履歴</h3>'+
      list.map(r=>`<div class="history-row">
        <span>${r.business_date}</span>
        <span class="badge ${r.status}">${{preliminary:'仮締め',final:'本締め'}[r.status]||r.status}</span>
        <span style="font-weight:700">${yen(r.total_sales)}</span>
        <span style="color:var(--muted);font-size:11px">${r.closed_by||''}</span>
      </div>`).join('');
  }catch(e){$('tabBody').innerHTML='<p style="color:var(--err)">'+e.message+'</p>';}
}

// --- actions ---
async function doPreliminary(){
  if(!confirm('仮締めを実行しますか？'))return;
  try{
    const r=await api(`/closing/preliminary?store_id=${$('storeId').value}&business_date=${$('bizDate').value}`,{method:'POST'});
    report=r.report;renderSummary();await loadStatus();switchTab('payment');
    toast('仮締めを実行しました');
  }catch(e){toast(e.message,'err');}
}
async function doFinal(){
  if(!confirm('本締めを実行しますか？\n本締め後はデータ編集がロックされます。'))return;
  try{
    const r=await api(`/closing/final?store_id=${$('storeId').value}&business_date=${$('bizDate').value}`,{method:'POST'});
    report=r.report;renderSummary();await loadStatus();switchTab('payment');
    toast('本締めを実行しました');
  }catch(e){toast(e.message,'err');}
}
async function doUnlock(){
  if(!confirm('本締めを解除しますか？'))return;
  try{
    await api(`/closing/unlock?store_id=${$('storeId').value}&business_date=${$('bizDate').value}`,{method:'POST'});
    await loadStatus();toast('本締めを解除しました');
  }catch(e){toast(e.message,'err');}
}
function exportCSV(){
  window.open(`/closing/z-report/csv?store_id=${$('storeId').value}&business_date=${$('bizDate').value}`,'_blank');
}
function exportPDF(){
  window.open(`/closing/z-report/pdf?store_id=${$('storeId').value}&business_date=${$('bizDate').value}`,'_blank');
}

// --- init ---
initDate();loadReport();
</script></body></html>""")
