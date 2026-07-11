from decimal import Decimal
from datetime import datetime, timezone, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session
import numpy as np
from app.models.trade import Trade
from app.models.position import Position
from app.models.features import FeatureSnapshot
from app.services.risk import compute_portfolio_var

FEATURE_LOOKBACK_TRADES = 200

def get_latest_price(recent_trades: list[Trade], ticker: str, trade: Trade) -> Decimal:
    if trade.ticker == ticker:
        return trade.price
    for t in recent_trades:
        if t.ticker == ticker and t.id <= trade.id:
            return t.price
    return Decimal("0.0000")

def get_ticker_prices(recent_trades: list[Trade], ticker: str, trade: Trade, limit: int) -> list[float]:
    ticker_trades = [t for t in recent_trades if t.ticker == ticker and t.id <= trade.id]
    prices = [float(t.price) for t in ticker_trades[:limit]]
    return list(reversed(prices))

def get_portfolio_historical_values(snapshots: list[FeatureSnapshot], current_val: Decimal, limit: int) -> list[float]:
    needed = snapshots[:limit - 1]
    return [float(s.exp_gross_exposure) for s in reversed(needed)] + [float(current_val)]

def get_volatility(prices: list[float]) -> float:
    if len(prices) < 2:
        return 0.0
    returns = []
    for i in range(1, len(prices)):
        p_prev = prices[i-1]
        p_curr = prices[i]
        if p_prev > 0 and p_curr > 0:
            returns.append(np.log(p_curr / p_prev))
    if len(returns) < 1:
        return 0.0
    return float(np.std(returns))

def generate_features_for_trade(db: Session, trade: Trade):
    db.flush()
    portfolio_id = trade.portfolio_id
    
    # --- 1. Read Phase ---
    positions = db.query(Position).filter(Position.portfolio_id == portfolio_id).all()
    
    recent_trades = db.query(Trade).filter(
        Trade.portfolio_id == portfolio_id,
        Trade.id <= trade.id
    ).order_by(Trade.id.desc()).limit(FEATURE_LOOKBACK_TRADES).all()
    
    trade_ts = trade.timestamp.replace(tzinfo=None) if trade.timestamp.tzinfo is not None else trade.timestamp
    one_hour_ago = trade_ts - timedelta(hours=1)
    
    if recent_trades:
        recent_oldest_ts = recent_trades[-1].timestamp.replace(tzinfo=None) if recent_trades[-1].timestamp.tzinfo is not None else recent_trades[-1].timestamp
        if recent_oldest_ts <= one_hour_ago:
            trades_1h = [
                t for t in recent_trades
                if (t.timestamp.replace(tzinfo=None) if t.timestamp.tzinfo is not None else t.timestamp) >= one_hour_ago
            ]
        else:
            trades_1h = db.query(Trade).filter(
                Trade.portfolio_id == portfolio_id,
                Trade.timestamp >= one_hour_ago,
                Trade.timestamp <= trade_ts
            ).all()
    else:
        trades_1h = []
        
    snapshots = db.query(FeatureSnapshot).filter(
        FeatureSnapshot.portfolio_id == portfolio_id,
        FeatureSnapshot.snapshot_type == "PORTFOLIO",
        FeatureSnapshot.timestamp < trade_ts
    ).order_by(FeatureSnapshot.timestamp.desc()).limit(31).all()
    
    var_res = compute_portfolio_var(db, portfolio_id, as_of=trade_ts)
    var_95 = Decimal(str(var_res["var_value"])) if not var_res["insufficient_data"] else Decimal("0.0000")
    
    # --- 2. Compute Phase ---
    mark_prices = {}
    position_values = {}
    total_portfolio_value = Decimal("0.0000")
    for pos in positions:
        price = get_latest_price(recent_trades, pos.ticker, trade)
        mark_prices[pos.ticker] = price
        val = pos.net_quantity * price
        position_values[pos.ticker] = val
        total_portfolio_value += abs(val)

    port_trade_count = len(trades_1h)
    if trades_1h:
        port_avg_size = Decimal(str(np.mean([float(t.quantity) for t in trades_1h])))
        port_volume = sum(t.quantity * t.price for t in trades_1h)
    else:
        port_avg_size = Decimal("0.0000")
        port_volume = Decimal("0.0000")

    trades_1h_by_ticker = {}
    for t in trades_1h:
        trades_1h_by_ticker.setdefault(t.ticker, []).append(t)

    port_values_5t = get_portfolio_historical_values(snapshots, total_portfolio_value, 6)
    port_vol_5t = get_volatility(port_values_5t)
    
    port_values_30t = get_portfolio_historical_values(snapshots, total_portfolio_value, 31)
    port_vol_30t = get_volatility(port_values_30t)

    hhi = Decimal("0.0000")
    if total_portfolio_value > 0:
        for pos in positions:
            weight = abs(position_values[pos.ticker]) / total_portfolio_value
            hhi += weight * weight

    # --- 3. Write Phase ---
    portfolio_snapshot = FeatureSnapshot(
        portfolio_id=portfolio_id,
        ticker=None,
        trade_id=trade.id,
        timestamp=trade.timestamp,
        snapshot_type="PORTFOLIO",
        exp_net_exposure=sum(position_values.values()),
        exp_gross_exposure=total_portfolio_value,
        exp_weight=Decimal("1.0000"),
        beh_trade_count_1h=port_trade_count,
        beh_avg_trade_size=port_avg_size,
        beh_volume_1h=port_volume,
        pos_net_quantity=None,
        pos_avg_price=None,
        pos_unrealized_pnl=None,
        risk_var_95=var_95,
        risk_hhi_concentration=hhi,
        vol_rolling_volatility_5t=Decimal(str(port_vol_5t)),
        vol_rolling_volatility_30t=Decimal(str(port_vol_30t))
    )
    db.add(portfolio_snapshot)

    for pos in positions:
        ticker = pos.ticker
        net_qty = pos.net_quantity
        avg_price = pos.avg_price
        unrealized_pnl = pos.unrealized_pnl
        pos_val = position_values[ticker]
        weight = abs(pos_val) / total_portfolio_value if total_portfolio_value > 0 else Decimal("0.0000")

        tick_trades = trades_1h_by_ticker.get(ticker, [])
        tick_trade_count = len(tick_trades)
        if tick_trades:
            tick_avg_size = Decimal(str(np.mean([float(t.quantity) for t in tick_trades])))
            tick_volume = sum(t.quantity * t.price for t in tick_trades)
        else:
            tick_avg_size = Decimal("0.0000")
            tick_volume = Decimal("0.0000")

        prices_5t = get_ticker_prices(recent_trades, ticker, trade, 6)
        tick_vol_5t = get_volatility(prices_5t)
        
        prices_30t = get_ticker_prices(recent_trades, ticker, trade, 31)
        tick_vol_30t = get_volatility(prices_30t)

        position_snapshot = FeatureSnapshot(
            portfolio_id=portfolio_id,
            ticker=ticker,
            trade_id=trade.id,
            timestamp=trade.timestamp,
            snapshot_type="POSITION",
            exp_net_exposure=pos_val,
            exp_gross_exposure=abs(pos_val),
            exp_weight=weight,
            beh_trade_count_1h=tick_trade_count,
            beh_avg_trade_size=tick_avg_size,
            beh_volume_1h=tick_volume,
            pos_net_quantity=net_qty,
            pos_avg_price=avg_price,
            pos_unrealized_pnl=unrealized_pnl,
            risk_var_95=None,
            risk_hhi_concentration=None,
            vol_rolling_volatility_5t=Decimal(str(tick_vol_5t)),
            vol_rolling_volatility_30t=Decimal(str(tick_vol_30t))
        )
        db.add(position_snapshot)

    return portfolio_snapshot
