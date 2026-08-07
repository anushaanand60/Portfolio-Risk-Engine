import os
import sys
import time
import json
import random
import argparse
from decimal import Decimal
from datetime import datetime, timezone, timedelta

os.environ["TESTING"] = "True"

import numpy as np

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.portfolio import Portfolio
from app.models.trade import Trade
from app.models.position import Position
from app.models.alert import Alert
from app.models.features import FeatureSnapshot
from app.services.position import update_position_logic
from app.services.alerts import run_post_trade_alerts
from app.services.feature_generation import generate_features_for_trade
from app.services.anomaly_detector import (
    train_global_anomaly_model, score_anomaly,
    FEATURES as ANOMALY_FEATURES, MODEL_PATH as ANOMALY_MODEL_PATH,
)
from app.services.market_simulator import (
    generate_simulation_data,
)

BENCH_DB = "./benchmark_run.db"
REPORT_PATH = "benchmark_report.json"
TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"]

SEP = "=" * 65
SUBSEP = "-" * 65


def make_session():
    engine = create_engine(BENCH_DB.replace("./", "sqlite:///./"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SM = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, SM()


def model_size_mb(path: str) -> float:
    return os.path.getsize(path) / (1024 * 1024) if os.path.exists(path) else 0.0


def fmt(label: str, value) -> str:
    return f"  {label:<42} {value}"

def generate_data(db, n_trades: int):
    print(f"\n{SEP}")
    print(f"  DATA GENERATION  ({n_trades} trades, yfinance live simulator)")
    print(SEP)
    print("  Fetching historical data and distributing trades...")
    print()

    t0 = time.perf_counter()
    generate_simulation_data(db, portfolio_id=1, num_trades=n_trades)
    sim_ms = (time.perf_counter() - t0) * 1000

    n_snap = db.query(FeatureSnapshot).filter(
        FeatureSnapshot.snapshot_type == "PORTFOLIO"
    ).count()
    print(fmt("Trades inserted:", n_trades))
    print(fmt("Portfolio snapshots:", n_snap))
    print(fmt("Simulation time:", f"{sim_ms:,.1f} ms"))
    return n_trades, n_snap, sim_ms


# Phase 2: Anomaly Detection
def bench_anomaly(db, latency_runs: int):
    print(f"\n{SEP}")
    print("  PHASE 2 — ANOMALY DETECTION  (IsolationForest)")
    print(SEP)

    res = train_global_anomaly_model(db, contamination=0.01)
    size_mb = model_size_mb(ANOMALY_MODEL_PATH)
    print(fmt("Training rows:", res["training_rows"]))
    print(fmt("Contamination:", res["contamination"]))
    print(fmt("Anomalies flagged:", res["anomaly_count"]))
    print(fmt("Anomaly rate:", f"{res['anomaly_rate']:.4f}  ({res['anomaly_rate']*100:.2f} %)"))
    print(fmt("Training time:", f"{res['training_time_ms']:,.1f} ms"))
    print(fmt("Model size:", f"{size_mb:.3f} MB"))

    sample = (
        db.query(FeatureSnapshot)
        .filter(FeatureSnapshot.snapshot_type == "PORTFOLIO")
        .order_by(FeatureSnapshot.id.desc()).first()
    )
    lats = []
    for _ in range(latency_runs):
        t0 = time.perf_counter()
        score_anomaly(db, sample)
        lats.append((time.perf_counter() - t0) * 1000)
    lats = np.array(lats)
    print(fmt("Inference latency (median):", f"{np.median(lats):.3f} ms"))
    print(fmt("Inference latency (p95):", f"{np.percentile(lats, 95):.3f} ms"))
    print(fmt("Inference latency (p99):", f"{np.percentile(lats, 99):.3f} ms"))

    return {
        "algorithm": "IsolationForest",
        "contamination": res["contamination"],
        "training_rows": res["training_rows"],
        "anomaly_count": res["anomaly_count"],
        "anomaly_rate": round(res["anomaly_rate"], 4),
        "training_time_ms": round(res["training_time_ms"], 2),
        "model_size_mb": round(size_mb, 4),
        "features": ANOMALY_FEATURES,
        "feature_means": {k: round(v, 4) for k, v in res["means"].items()},
        "feature_stds":  {k: round(v, 4) for k, v in res["stds"].items()},
        "latency": {
            "median_ms": round(float(np.median(lats)), 3),
            "p95_ms": round(float(np.percentile(lats, 95)), 3),
            "p99_ms": round(float(np.percentile(lats, 99)), 3),
            "runs": latency_runs,
        },
    }


def bench_e2e(db, latency_runs: int):
    print(f"\n{SEP}")
    print("  END-TO-END PIPELINE LATENCY")
    print(SEP)

    portfolio_id = 1
    _= db.query(Position).filter(Position.portfolio_id == portfolio_id).first()

    feat_lats, anomaly_lats, total_lats = [], [], []

    for _ in range(latency_runs):
        trade = Trade(
            portfolio_id=portfolio_id,
            ticker=random.choice(TICKERS),
            quantity=Decimal(str(random.randint(10, 50))),
            price=Decimal(str(round(random.uniform(100.0, 400.0), 4))),
            side="BUY",
        )
        db.add(trade)
        db.flush()
        update_position_logic(db, trade)

        t_total = time.perf_counter()

        t0 = time.perf_counter()
        snapshot = generate_features_for_trade(db, trade)
        feat_lats.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        score_anomaly(db, snapshot)
        anomaly_lats.append((time.perf_counter() - t0) * 1000)

        total_lats.append((time.perf_counter() - t_total) * 1000)

        db.rollback()

    def stats(arr):
        a = np.array(arr)
        return {
            "mean_ms":   round(float(np.mean(a)),   3),
            "median_ms": round(float(np.median(a)),  3),
            "p95_ms":    round(float(np.percentile(a, 95)), 3),
            "p99_ms":    round(float(np.percentile(a, 99)), 3),
        }

    rows = [
        ("Feature generation",    feat_lats),
        ("Anomaly scoring",       anomaly_lats),
        ("Full pipeline",         total_lats),
    ]
    print(f"  {'Stage':<30} {'Mean':>8} {'Median':>8} {'P95':>8} {'P99':>8}")
    print(f"  {SUBSEP[:66]}")
    for label, arr in rows:
        a = np.array(arr)
        print(f"  {label:<30} {np.mean(a):>7.3f}  {np.median(a):>7.3f}  "
              f"{np.percentile(a,95):>7.3f}  {np.percentile(a,99):>7.3f}  ms")

    return {
        "runs": latency_runs,
        "feature_generation": stats(feat_lats),
        "anomaly_scoring":    stats(anomaly_lats),
        "full_pipeline":      stats(total_lats),
    }


def main():
    parser = argparse.ArgumentParser(description="Portfolio-Risk-Engine - Benchmark")
    parser.add_argument("--trades", type=int, default=350,
                        help="Number of trades to simulate (default: 350)")
    parser.add_argument("--latency-runs", type=int, default=100,
                        help="Inference runs per stage (default: 100)")
    args = parser.parse_args()

    random.seed(42)
    np.random.seed(42)

    if os.path.exists(BENCH_DB.lstrip("./")):
        os.remove(BENCH_DB.lstrip("./"))

    engine, db = make_session()

    print(f"\n{'#'*65}")
    print("  PORTFOLIO-RISK-ENGINE - BENCHMARK")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  trades={args.trades}  latency_runs={args.latency_runs}")
    print(f"{'#'*65}")

    n_trades, n_snap, sim_ms = generate_data(db, args.trades)
    anomaly_metrics = bench_anomaly(db, args.latency_runs)
    e2e_metrics = bench_e2e(db, args.latency_runs)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "n_trades": n_trades,
            "n_portfolio_snapshots": n_snap,
            "tickers": TICKERS,
            "simulation_time_ms": round(sim_ms, 2),
        },
        "training_thresholds": {
            "anomaly_min_snapshots": 100,
            "var_window_days": 30,
            "var_confidence_level": 0.95,
            "concentration_alert_threshold": 0.40,
            "var_alert_threshold": 1000000.00,
            "train_test_split": "80 / 20  (chronological)",
        },
        "api_inventory": {
            "portfolios": [
                "POST /portfolios",
                "GET  /portfolios/{id}/positions",
                "GET  /portfolios/{id}/var",
            ],
            "trades": [
                "POST /trades",
                "GET  /portfolios/{id}/trades",
            ],
            "simulation": [
                "POST /portfolios/{id}/simulate-data",
                "POST /simulate",
            ],
            "features": [
                "GET  /portfolios/{id}/features",
            ],
            "anomaly_detection": [
                "POST /anomaly/train",
                "GET  /portfolios/{id}/anomaly/score",
                "GET  /portfolios/{id}/anomaly/history",
            ],
            "system": [
                "GET  /health",
            ],
        },
        "phase2_anomaly_detection": anomaly_metrics,
        "end_to_end_latency": e2e_metrics,
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n{SEP}")
    print(f"  Report written -> {REPORT_PATH}")
    print(SEP)
    print(f"\n  End-to-end median pipeline latency: "
          f"{e2e_metrics['full_pipeline']['median_ms']:.2f} ms")
    print(f"  End-to-end p99 pipeline latency:    "
          f"{e2e_metrics['full_pipeline']['p99_ms']:.2f} ms\n")

    db.close()
    engine.dispose()
    for path in [BENCH_DB.lstrip("./"), ANOMALY_MODEL_PATH]:
        if os.path.exists(path):
            os.remove(path)

if __name__ == "__main__":
    main()
