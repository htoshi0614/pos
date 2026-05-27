"""weather_service.py — 天気情報取得・出勤人数調整 + /ui/weather"""
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
import httpx

from db_shared import Base, SessionLocal

JST = ZoneInfo("Asia/Tokyo")
router = APIRouter(tags=["weather"])

# ─────────────────────────── DB Models ───────────────────────────

class WeatherConfig(Base):
    __tablename__ = "weather_configs"
    id           = Column(Integer, primary_key=True)
    store_id     = Column(Integer, unique=True)
    city_name    = Column(String, default="Tokyo")
    latitude     = Column(Float, default=35.6762)   # 東京
    longitude    = Column(Float, default=139.6503)
    # 天気ベース出勤数
    base_staff   = Column(Integer, default=5)
    rainy_adj    = Column(Integer, default=-1)   # 雨天時 ±N
    cold_adj     = Column(Integer, default=-1)   # 寒冷時（<10℃）
    hot_adj      = Column(Integer, default=-1)   # 猛暑時（>35℃）

class StaffSchedule(Base):
    __tablename__ = "staff_schedules"
    id            = Column(Integer, primary_key=True)
    store_id      = Column(Integer)
    schedule_date = Column(String)   # YYYY-MM-DD
    weather_code  = Column(Integer, default=0)
    temperature   = Column(Float, default=0.0)
    suggested     = Column(Integer, default=0)
    confirmed     = Column(Integer, default=0)
    note          = Column(String, default="")
    created_at    = Column(DateTime, default=datetime.utcnow)

# ─────────────────────────── Pydantic ───────────────────────────

class WeatherConfigIn(BaseModel):
    city_name:  str   = "Tokyo"
    latitude:   float = 35.6762
    longitude:  float = 139.6503
    base_staff: int   = 5
    rainy_adj:  int   = -1
    cold_adj:   int   = -1
    hot_adj:    int   = -1

class ScheduleConfirmIn(BaseModel):
    confirmed: int
    note: str = ""

# ─────────────────────────── Weather Logic ───────────────────────────

WMO_DESCRIPTIONS = {
    0: "快晴", 1: "晴れ", 2: "一部曇り", 3: "曇り",
    45: "霧", 48: "霧氷",
    51: "霧雨（弱）", 53: "霧雨", 55: "霧雨（強）",
    61: "雨（弱）", 63: "雨", 65: "雨（強）",
    71: "雪（弱）", 73: "雪", 75: "雪（強）",
    80: "にわか雨（弱）", 81: "にわか雨", 82: "にわか雨（強）",
    95: "雷雨", 96: "雷雨＋ひょう", 99: "雷雨＋大ひょう",
}

def is_rainy(code: int) -> bool:
    return code in {51,53,55,61,63,65,80,81,82,95,96,99,71,73,75}

async def fetch_weather(lat: float, lon: float) -> dict:
    """Open-Meteo API（無料・APIキー不要）から現在天気を取得"""
    url = (f"https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat}&longitude={lon}"
           f"&current=temperature_2m,weathercode,windspeed_10m"
           f"&timezone=Asia/Tokyo")
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    cur = data.get("current", {})
    code = int(cur.get("weathercode", 0))
    temp = float(cur.get("temperature_2m", 20))
    return {
        "temperature": temp,
        "weather_code": code,
        "description": WMO_DESCRIPTIONS.get(code, f"コード {code}"),
        "is_rainy": is_rainy(code),
    }

def suggest_staff(config: WeatherConfig, weather: dict) -> int:
    n = config.base_staff
    if weather["is_rainy"]:
        n += config.rainy_adj
    if weather["temperature"] < 10:
        n += config.cold_adj
    if weather["temperature"] > 35:
        n += config.hot_adj
    return max(1, n)

# ─────────────────────────── API Routes ───────────────────────────

@router.get("/weather-config/{store_id}")
def get_weather_config(store_id: int,
                       x_role: Optional[str] = Header(None, alias="X-Role")):
    db = SessionLocal()
    try:
        cfg = db.query(WeatherConfig).filter_by(store_id=store_id).first()
        return {k: v for k, v in cfg.__dict__.items() if not k.startswith("_")} if cfg else None
    finally:
        db.close()

@router.post("/weather-config/{store_id}")
def save_weather_config(store_id: int, payload: WeatherConfigIn,
                        x_role: Optional[str] = Header(None, alias="X-Role")):
    db = SessionLocal()
    try:
        cfg = db.query(WeatherConfig).filter_by(store_id=store_id).first()
        if cfg:
            for k, v in (payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()).items():
                setattr(cfg, k, v)
        else:
            cfg = WeatherConfig(store_id=store_id, **(payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()))
            db.add(cfg)
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@router.get("/weather/{store_id}/now")
async def get_current_weather(store_id: int,
                              x_role: Optional[str] = Header(None, alias="X-Role")):
    db = SessionLocal()
    try:
        cfg = db.query(WeatherConfig).filter_by(store_id=store_id).first()
        lat = cfg.latitude  if cfg else 35.6762
        lon = cfg.longitude if cfg else 139.6503
        try:
            weather = await fetch_weather(lat, lon)
        except Exception as e:
            raise HTTPException(502, f"天気APIエラー: {e}")

        suggested = suggest_staff(cfg, weather) if cfg else 5

        # 今日のスケジュールを自動作成/更新
        today = datetime.now(tz=JST).strftime("%Y-%m-%d")
        sched = db.query(StaffSchedule).filter_by(store_id=store_id, schedule_date=today).first()
        if not sched:
            sched = StaffSchedule(store_id=store_id, schedule_date=today,
                                  weather_code=weather["weather_code"],
                                  temperature=weather["temperature"],
                                  suggested=suggested, confirmed=suggested)
            db.add(sched)
        else:
            sched.weather_code = weather["weather_code"]
            sched.temperature  = weather["temperature"]
            sched.suggested    = suggested
        db.commit()

        return {**weather, "suggested_staff": suggested, "schedule_id": sched.id,
                "confirmed_staff": sched.confirmed}
    finally:
        db.close()

@router.get("/weather/{store_id}/schedules")
def get_schedules(store_id: int,
                  x_role: Optional[str] = Header(None, alias="X-Role")):
    db = SessionLocal()
    try:
        rows = (db.query(StaffSchedule)
                .filter_by(store_id=store_id)
                .order_by(StaffSchedule.schedule_date.desc())
                .limit(30).all())
        return [{k: v for k, v in r.__dict__.items() if not k.startswith("_")} for r in rows]
    finally:
        db.close()

@router.post("/weather/{store_id}/schedules/{schedule_id}/confirm")
def confirm_schedule(store_id: int, schedule_id: int, payload: ScheduleConfirmIn,
                     x_role: Optional[str] = Header(None, alias="X-Role")):
    db = SessionLocal()
    try:
        sched = db.query(StaffSchedule).filter_by(id=schedule_id, store_id=store_id).first()
        if not sched:
            raise HTTPException(404, "Schedule not found")
        sched.confirmed = payload.confirmed
        sched.note      = payload.note
        db.commit()
        return {"ok": True}
    finally:
        db.close()

# ─────────────────────────── Weather UI ───────────────────────────

@router.get("/ui/weather", response_class=HTMLResponse)
def ui_weather():
    return HTMLResponse(r"""
<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>天気/シフト - Girls Bar POS</title>
<style>
:root{--bg:#fafafa;--card:#ffffff;--card2:#ffffff;--line:#eaeaef;--border:#eaeaef;--text:#0a0a0f;--ink:#0a0a0f;--muted:#8a8a95;--body:#4a4a55;--accent:#d64583;--accent-soft:#fdf0f7;--accent-dark:#b03468;--gold:#c9a96e;--gold-soft:#faf3e3;--warn:#f59e0b;--amber:#f59e0b;--err:#ef4444;--red:#ef4444;--ok:#22c55e;--green:#22c55e;--blue:#3b82f6;--purple:#a855f7;}
*{box-sizing:border-box;font-family:-apple-system,system-ui,"Noto Sans JP",sans-serif}
body{margin:0;background:var(--bg);color:var(--text)}
header{position:sticky;top:0;z-index:40;display:flex;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);background:rgba(11,18,32,.95)}
header h1{margin:0;font-size:17px}
.nav a{color:var(--accent);text-decoration:none;font-size:14px;padding:6px 10px;border-radius:8px;border:1px solid var(--line)}
.container{max-width:900px;margin:0 auto;padding:20px 16px;display:flex;flex-direction:column;gap:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px}
.card h2{margin:0 0 14px;font-size:15px;border-bottom:1px solid var(--line);padding-bottom:10px}
label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--muted)}
input,select{font-size:14px;padding:7px 10px;border-radius:8px;border:1px solid #263244;background:#0a1220;color:var(--text)}
.btn{cursor:pointer;font-size:14px;padding:8px 16px;border-radius:10px;border:1px solid #334155;background:#111827;color:var(--text)}
.btn.solid{background:var(--accent);border-color:var(--accent);color:#001018;font-weight:700}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.weather-big{font-size:48px;text-align:center;padding:10px}
.weather-sub{text-align:center;font-size:18px;color:var(--muted)}
.suggest-num{font-size:56px;font-weight:900;color:var(--accent);text-align:center}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--muted)}
@media(max-width:700px){
  .grid3{grid-template-columns:1fr}
  .container{padding:12px 10px}
  table{font-size:11px;display:block;overflow-x:auto}
  .weather-big{font-size:36px}
  .suggest-num{font-size:40px}
  header{flex-wrap:wrap;gap:8px}
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

</style></head><body>
<header>
  <h1>天気 / 出勤シフト</h1>
  <div class="nav" style="display:flex;gap:8px;margin-left:auto">
    <a href="/ui">← フロア</a>
    <a href="/ui/pricing">料金設定</a>
    <a href="/ui/salary">給与管理</a>
    <a href="/ui/subscription">サブスク</a>
  </div>
</header>

<div class="container">
  <div class="row">
    <label style="flex-direction:row;align-items:center;gap:6px">店舗 <input id="storeId" type="number" value="1" style="width:70px"></label>
    <button class="btn solid" onclick="loadWeather()">天気を取得</button>
    <button class="btn" onclick="loadSchedules()">シフト履歴</button>
  </div>

  <!-- 今日の天気 -->
  <div class="card" id="weatherCard" style="display:none">
    <h2>本日の天気</h2>
    <div class="weather-big" id="weatherEmoji">🌤</div>
    <div class="weather-sub" id="weatherDesc">取得中...</div>
    <div class="weather-sub" id="weatherTemp" style="margin-top:4px"></div>
    <hr style="border-color:var(--line);margin:16px 0">
    <div style="font-size:13px;color:var(--muted);text-align:center">推奨出勤人数</div>
    <div class="suggest-num" id="suggestNum">-</div>
    <div style="text-align:center;margin-top:12px">
      <label style="flex-direction:row;align-items:center;justify-content:center;gap:8px">
        確定人数 <input id="confirmNum" type="number" min="1" style="width:70px">
        <input id="confirmNote" placeholder="メモ（任意）" style="width:180px">
        <button class="btn solid" onclick="confirmSchedule()">確定</button>
      </label>
    </div>
  </div>

  <!-- 天気設定 -->
  <div class="card">
    <h2>天気設定</h2>
    <div class="grid3">
      <label>地域
        <select id="city" onchange="onCityChange()">
          <option value="東京" data-lat="35.6762" data-lon="139.6503">東京都</option>
          <option value="横浜" data-lat="35.4437" data-lon="139.6380">神奈川県（横浜）</option>
          <option value="さいたま" data-lat="35.8617" data-lon="139.6455">埼玉県（さいたま）</option>
          <option value="千葉" data-lat="35.6073" data-lon="140.1063">千葉県</option>
          <option value="水戸" data-lat="36.3414" data-lon="140.4468">茨城県（水戸）</option>
          <option value="宇都宮" data-lat="36.5551" data-lon="139.8829">栃木県（宇都宮）</option>
          <option value="前橋" data-lat="36.3912" data-lon="139.0608">群馬県（前橋）</option>
        </select>
      </label>
      <input id="lat" type="hidden" value="35.6762">
      <input id="lon" type="hidden" value="139.6503">
      <label>基準出勤数<input id="base" type="number" value="5"></label>
      <label>雨天時調整（±）<input id="rain_adj" type="number" value="-1"></label>
      <label>寒冷時（<10℃）調整<input id="cold_adj" type="number" value="-1"></label>
      <label>猛暑時（>35℃）調整<input id="hot_adj" type="number" value="-1"></label>
    </div>
    <div class="row" style="margin-top:12px;justify-content:flex-end">
      <button class="btn solid" onclick="saveConfig()">設定保存</button>
    </div>
  </div>

  <!-- シフト履歴 -->
  <div class="card" id="schedCard" style="display:none">
    <h2>シフト履歴（直近30日）</h2>
    <table>
      <thead><tr><th>日付</th><th>天気</th><th>気温</th><th>推奨</th><th>確定</th><th>メモ</th></tr></thead>
      <tbody id="schedBody"></tbody>
    </table>
  </div>
</div>

<script>
const $ = id=>document.getElementById(id);
let currentScheduleId = null;

async function api(path,opt={}){
  const tk=sessionStorage.getItem('pos_token')||'';
  const o={method:'GET',headers:{'Content-Type':'application/json','X-Role':'owner','X-Token':tk},...opt};
  if(o.body&&typeof o.body!=='string') o.body=JSON.stringify(o.body);
  const r=await fetch(path,o);
  if(r.status===401){sessionStorage.clear();window.location.href='/';return;}
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}

const WMO_EMOJI = {0:'☀️',1:'🌤',2:'⛅',3:'☁️',45:'🌫',48:'🌫',
  51:'🌦',53:'🌦',55:'🌧',61:'🌧',63:'🌧',65:'⛈',
  71:'🌨',73:'❄️',75:'❄️',80:'🌧',81:'🌧',82:'⛈',95:'⛈',96:'⛈',99:'⛈'};

async function loadWeather(){
  const s=$('storeId').value;
  try{
    const w=await api(`/weather/${s}/now`);
    $('weatherCard').style.display='';
    $('weatherEmoji').textContent=WMO_EMOJI[w.weather_code]||'🌤';
    $('weatherDesc').textContent=w.description||'';
    $('weatherTemp').textContent=`${w.temperature}℃ / ${w.is_rainy?'☂️ 雨':'晴れ系'}`;
    $('suggestNum').textContent=w.suggested_staff;
    $('confirmNum').value=w.confirmed_staff||w.suggested_staff;
    currentScheduleId=w.schedule_id;
  }catch(e){alert('天気取得エラー: '+e.message)}
}

async function confirmSchedule(){
  if(!currentScheduleId) return;
  const s=$('storeId').value;
  try{
    await api(`/weather/${s}/schedules/${currentScheduleId}/confirm`,{method:'POST',body:{
      confirmed:parseInt($('confirmNum').value),
      note:$('confirmNote').value
    }});
    alert('確定しました');
  }catch(e){alert(e.message)}
}

async function loadSchedules(){
  const s=$('storeId').value;
  try{
    const rows=await api(`/weather/${s}/schedules`);
    $('schedCard').style.display='';
    const tb=$('schedBody'); tb.innerHTML='';
    rows.forEach(r=>{
      const tr=document.createElement('tr');
      tr.innerHTML=`<td>${r.schedule_date}</td>
        <td>${WMO_EMOJI[r.weather_code]||'?'} ${r.weather_code}</td>
        <td>${r.temperature}℃</td>
        <td>${r.suggested}</td><td><b>${r.confirmed}</b></td><td>${r.note||''}</td>`;
      tb.appendChild(tr);
    });
  }catch(e){alert(e.message)}
}

function onCityChange(){
  const sel=$('city');
  const opt=sel.options[sel.selectedIndex];
  $('lat').value=opt.dataset.lat;
  $('lon').value=opt.dataset.lon;
}

async function saveConfig(){
  const s=$('storeId').value;
  onCityChange();
  try{
    await api(`/weather-config/${s}`,{method:'POST',body:{
      city_name:$('city').value,
      latitude:parseFloat($('lat').value),
      longitude:parseFloat($('lon').value),
      base_staff:parseInt($('base').value),
      rainy_adj:parseInt($('rain_adj').value),
      cold_adj:parseInt($('cold_adj').value),
      hot_adj:parseInt($('hot_adj').value),
    }});
    alert('設定を保存しました');
  }catch(e){alert(e.message)}
}

// 設定を自動読み込み
(async()=>{
  const s=$('storeId').value;
  try{
    const cfg=await api(`/weather-config/${s}`);
    if(cfg){
      // セレクトの値を復元
      const cityName=cfg.city_name||'東京';
      const sel=$('city');
      let found=false;
      for(let i=0;i<sel.options.length;i++){
        if(sel.options[i].value===cityName){sel.selectedIndex=i;found=true;break;}
      }
      if(!found) sel.selectedIndex=0;
      onCityChange();
      $('base').value=cfg.base_staff||5;
      $('rain_adj').value=cfg.rainy_adj??-1;
      $('cold_adj').value=cfg.cold_adj??-1;
      $('hot_adj').value=cfg.hot_adj??-1;
    }
  }catch{}
})();
</script>
</body></html>
""")
