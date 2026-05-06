#!/usr/bin/env python3
"""
demo_seed.py — POSStart デモ用サンプルデータ投入スクリプト

使い方:
  python demo_seed.py          # デモデータを投入（既存データがあればスキップ）
  python demo_seed.py --reset  # DBを完全リセットしてからデモデータを投入
"""

import sys
import os
from datetime import datetime, timedelta, timezone

# Windows での日本語・絵文字出力対応
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# カレントディレクトリのモジュールを読む
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_shared import Base, engine, SessionLocal

# モデルを全部ロード（table作成のため）
import pos
import stripe_service
import cast_salary
import bottle_keep
import customer_crm
import closing
import management
import tab_management

# StripeSubscription モデルを直接使う
from stripe_service import StripeSubscription

# pos.py で定義されているモデル
from pos import Store, Table, Cast, Item

# cast_salary で定義されているモデル
from cast_salary import CastSalaryConfig

RESET = "--reset" in sys.argv

def main():
    if RESET:
        print("⚠️  DBを完全リセットします...")
        Base.metadata.drop_all(engine)
        print("   テーブルを再作成中...")

    Base.metadata.create_all(engine)
    db = SessionLocal()

    try:
        # ───────────────────────────────────────────
        # 店舗
        # ───────────────────────────────────────────
        store = db.query(Store).filter_by(name="Bar LUNA（デモ）").first()
        if not store:
            store = Store(name="Bar LUNA（デモ）")
            db.add(store)
            db.flush()
            print(f"✅ 店舗作成: {store.name}  (id={store.id})")
        else:
            print(f"  店舗は既存: {store.name}  (id={store.id})")

        store_id = store.id

        # ───────────────────────────────────────────
        # サブスクリプション（デモ用: 2099年まで有効）
        # ───────────────────────────────────────────
        sub = db.query(StripeSubscription).filter_by(store_id=store_id).first()
        if not sub:
            sub = StripeSubscription(
                store_id=store_id,
                stripe_customer_id="cus_demo",
                stripe_sub_id="sub_demo",
                plan_name="demo",
                status="active",
                current_period_end=datetime(2099, 12, 31, tzinfo=timezone.utc),
                cancel_at_end=False,
                payment_method="manual",
            )
            db.add(sub)
            print(f"✅ サブスクリプション作成: active (2099-12-31まで)")
        else:
            # 期限をリセット
            sub.status = "active"
            sub.current_period_end = datetime(2099, 12, 31, tzinfo=timezone.utc)
            print(f"  サブスクリプション更新: active (2099-12-31まで)")

        # ───────────────────────────────────────────
        # テーブル (T1〜T8)
        # ───────────────────────────────────────────
        table_names = ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"]
        existing_tables = {t.name for t in db.query(Table).filter_by(store_id=store_id).all()}
        for tname in table_names:
            if tname not in existing_tables:
                db.add(Table(store_id=store_id, name=tname))
        tables_added = [t for t in table_names if t not in existing_tables]
        if tables_added:
            print(f"✅ テーブル作成: {', '.join(tables_added)}")
        else:
            print(f"  テーブルは既存: {', '.join(table_names)}")

        # ───────────────────────────────────────────
        # キャスト
        # ───────────────────────────────────────────
        cast_data = [
            {"name": "ゆかり", "rank": "エース"},
            {"name": "なな",   "rank": "レギュラー"},
            {"name": "りん",   "rank": "レギュラー"},
            {"name": "さくら", "rank": "レギュラー"},
            {"name": "ほのか", "rank": "新人"},
            {"name": "みお",   "rank": "新人"},
        ]
        existing_casts = {c.name for c in db.query(Cast).filter_by(store_id=store_id).all()}
        for cd in cast_data:
            if cd["name"] not in existing_casts:
                db.add(Cast(store_id=store_id, name=cd["name"], rank=cd["rank"], is_active=True))
        casts_added = [c["name"] for c in cast_data if c["name"] not in existing_casts]
        if casts_added:
            print(f"✅ キャスト作成: {', '.join(casts_added)}")
        else:
            print(f"  キャストは既存: {', '.join([c['name'] for c in cast_data])}")

        # ───────────────────────────────────────────
        # メニュー
        # ───────────────────────────────────────────
        item_data = [
            # セット料金
            {"name": "1時間セット",       "category": "set",    "price": 4000.0},
            {"name": "フリードリンク90分", "category": "set",    "price": 6000.0},
            # ドリンク
            {"name": "ハイボール",         "category": "drink",  "price": 800.0},
            {"name": "ビール",             "category": "drink",  "price": 800.0},
            {"name": "レモンサワー",        "category": "drink",  "price": 800.0},
            {"name": "カシスオレンジ",      "category": "drink",  "price": 900.0},
            {"name": "カクテル",           "category": "drink",  "price": 900.0},
            {"name": "ソフトドリンク",      "category": "drink",  "price": 500.0},
            {"name": "シャンパン（グラス）", "category": "drink",  "price": 1500.0},
            # ボトル
            {"name": "ウイスキーボトル",    "category": "bottle", "price": 12000.0, "keepable": True, "capacity_ml": 700},
            {"name": "シャンパンボトル",    "category": "bottle", "price": 25000.0, "keepable": False, "capacity_ml": 750},
            {"name": "焼酎ボトル",         "category": "bottle", "price": 8000.0,  "keepable": True, "capacity_ml": 720},
            # フード
            {"name": "おつまみ盛合せ",      "category": "food",   "price": 1500.0},
            {"name": "フライドポテト",      "category": "food",   "price": 800.0},
        ]
        existing_items = {i.name for i in db.query(Item).filter_by(store_id=store_id).all()}
        for it in item_data:
            if it["name"] not in existing_items:
                db.add(Item(
                    store_id=store_id,
                    name=it["name"],
                    category=it["category"],
                    price=it["price"],
                    stock=99,
                    keepable=it.get("keepable", False),
                    capacity_ml=it.get("capacity_ml", 0),
                ))
        items_added = [i["name"] for i in item_data if i["name"] not in existing_items]
        if items_added:
            print(f"✅ メニュー作成: {', '.join(items_added)}")
        else:
            print(f"  メニューは既存")

        # ───────────────────────────────────────────
        # キャスト給与設定（デフォルト値）
        # ───────────────────────────────────────────
        try:
            from cast_salary import CastSalaryConfig
            db.flush()  # autoflush=Falseなので、キャストIDを確定させるために先にflush
            all_casts = db.query(Cast).filter_by(store_id=store_id, is_active=True).all()
            for c in all_casts:
                cfg = db.query(CastSalaryConfig).filter_by(cast_id=c.id).first()
                if not cfg:
                    # エースはバック高め、新人は低め
                    if c.rank == "エース":
                        db.add(CastSalaryConfig(
                            cast_id=c.id,
                            store_id=store_id,
                            drink_back_rate=0.30,   # ドリンクバック30%
                            nom_fee_hon=3000,        # 本指名バック3,000円
                            nom_fee_dohan=5000,      # 同伴バック5,000円
                            floor_rate=1000,         # 場内バック1,000円/件
                            hourly_rate=1500,
                        ))
                    elif c.rank == "レギュラー":
                        db.add(CastSalaryConfig(
                            cast_id=c.id,
                            store_id=store_id,
                            drink_back_rate=0.25,
                            nom_fee_hon=2000,
                            nom_fee_dohan=4000,
                            floor_rate=800,
                            hourly_rate=1300,
                        ))
                    else:  # 新人
                        db.add(CastSalaryConfig(
                            cast_id=c.id,
                            store_id=store_id,
                            drink_back_rate=0.20,
                            nom_fee_hon=1500,
                            nom_fee_dohan=3000,
                            floor_rate=500,
                            hourly_rate=1200,
                        ))
            print(f"✅ キャスト給与設定: {len(all_casts)}名分")
        except Exception as e:
            print(f"  キャスト給与設定スキップ: {e}")

        db.commit()
        print()
        print("━" * 50)
        print("🍸 デモデータの投入が完了しました！")
        print(f"   店舗名 : Bar LUNA（デモ）")
        print(f"   テーブル: T1〜T8")
        print(f"   キャスト: ゆかり、なな、りん、さくら、ほのか、みお")
        print(f"   ログインパスワード: posstart2024")
        print("━" * 50)

    except Exception as e:
        db.rollback()
        print(f"❌ エラー: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    main()
