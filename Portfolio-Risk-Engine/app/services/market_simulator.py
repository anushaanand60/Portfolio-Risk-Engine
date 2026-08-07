import math
import random
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.models.trade import Trade
from app.models.position import Position
from app.models.portfolio import Portfolio
from app.models.features import FeatureSnapshot
from app.models.alert import Alert
from app.services.position import update_position_logic
from app.services.alerts import run_post_trade_alerts
from app.services.feature_generation import generate_features_for_trade
from app.services.anomaly_detector import score_anomaly
from app.services.market_data import get_historical_prices

TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"]

EXPOSURE_SHOCK_PROB = 0.03
NORMAL_QTY_RANGE = (10, 100)
SHOCK_QTY_RANGE = (3000, 12000)

def generate_simulation_data(
    db: Session,
    portfolio_id: int,
    num_trades: int,
    start_date: datetime = None,
) -> None:
    db.query(Alert).filter(Alert.portfolio_id == portfolio_id).delete()
    db.query(Trade).filter(Trade.portfolio_id == portfolio_id).delete()
    db.query(Position).filter(Position.portfolio_id == portfolio_id).delete()
    db.query(FeatureSnapshot).filter(FeatureSnapshot.portfolio_id == portfolio_id).delete()
    db.flush()
    if not db.query(Portfolio).filter(Portfolio.id == portfolio_id).first():
        db.add(Portfolio(id=portfolio_id, name=f"SimPortfolio-{portfolio_id}"))
        db.commit()

    end_date = datetime.now(timezone.utc)
    if start_date is None:
        days_to_fetch = max(num_trades, int(num_trades * 1.5)) + 10
        start_date = end_date - timedelta(days=days_to_fetch)

    prices_data = get_historical_prices(TICKERS, start_date, end_date)

    available_data = []
    for ticker, data_list in prices_data.items():
        for item in data_list:
            available_data.append((ticker, item['date'], item['price']))

    available_data.sort(key=lambda x: x[1])

    if not available_data:
        return

    indices = sorted(random.choices(range(len(available_data)), k=num_trades))

    for i in indices:
        ticker, date_val, price_val = available_data[i]
        side = "BUY" if random.random() < 0.70 else "SELL"

        is_exposure_shock = random.random() < EXPOSURE_SHOCK_PROB
        if is_exposure_shock:
            quantity = Decimal(str(random.randint(*SHOCK_QTY_RANGE)))
        else:
            quantity = Decimal(str(random.randint(*NORMAL_QTY_RANGE)))

        price = Decimal(str(round(price_val, 4)))

        if side == "SELL":
            existing = db.query(Position).filter(
                Position.portfolio_id == portfolio_id,
                Position.ticker == ticker,
            ).first()
            if existing is None or existing.net_quantity < quantity:
                side = "BUY"

        trade_time = date_val.replace(tzinfo=timezone.utc) + timedelta(minutes=random.randint(0, 390))
        trade = Trade(
            portfolio_id=portfolio_id,
            ticker=ticker,
            quantity=quantity,
            price=price,
            side=side,
            timestamp=trade_time,
        )
        db.add(trade)
        db.flush()
        update_position_logic(db, trade)
        run_post_trade_alerts(db, trade)
        snapshot = generate_features_for_trade(db, trade)
        score_anomaly(db, snapshot)
    db.commit()
