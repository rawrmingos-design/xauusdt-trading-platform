# Improved Exit Model (PROJECT-STRATEGY-003)

Based on the findings from **PROJECT-BACKTEST-007**, the original exit model (1.5x ATR Stop Loss, 2.0x/2.5x RR Take Profit) suffered from a very high rate of "stopped before 1R" (~60%) and a poor expectancy despite decent entry quality. The strategy often achieved 0.8R to 1.0R in favorable excursion (MFE) before reversing and getting stopped out at the original stop loss.

## Implementation Details

A new configurable exit model was introduced to `ConfluenceStrategy` via the `improved_exit` boolean flag in `ConfluenceConfig`.

When `improved_exit=True`, the engine applies the following logic:

### 1. Partial Take Profit (1.0R)
A partial take profit target is automatically set at 1.0R (i.e. distance equal to the Stop Loss distance).
If price reaches this level:
- **50% of the position** is closed immediately.
- Fees and slippage are applied only to this closed half.
- A new trade record is appended with `is_partial=True` and `exit_reason="PARTIAL_TP"`.
- The remaining 50% stays open.

### 2. Break-Even Stop
Simultaneously with the Partial TP being hit, the Stop Loss for the *remaining* position is moved to the **Entry Price**.
- If the price continues in our favor, the remaining position will hit the Final TP.
- If the price reverses, the remaining position will be closed at Break-Even (yielding 0 PnL for the second half, securing the 0.5R profit from the first half).

### 3. Final Take Profit
The original `risk_reward_ratio` config value defines the Final TP target (e.g. 2.0x or 2.5x).
- If hit, the remaining 50% is closed and recorded as `exit_reason="TP"`.

### 4. ATR Stop Loosening
The default recommended ATR Stop Loss Multiplier has been increased from `1.5x` to `2.0x` (V1) or `2.5x` (V2) to provide more breathing room against short-term volatility ("whipsaws"), a key recommendation from BACKTEST-007.

## Engine Constraints & Ordering
Because the backtest uses OHLC 15m candles without intra-candle tick data, conservative ordering is maintained:
1. **SL before TP**: If both SL and Partial TP are touched in the same candle, the SL is executed first (worst-case assumption).
2. **Partial before Final TP**: If both Partial TP and Final TP are touched in the same candle, the engine currently executes Partial TP in one tick, then immediately executes Final TP on the next engine cycle if still applicable. (In practice, this is implemented as executing Partial TP first, and if the candle also reached Final TP, it will be executed either in the same iteration or the next tick depending on the signal loop).

## Limitations
- **Position Sizing rounding**: The engine natively handles float quantities, so `quantity / 2.0` is used. Exchanges may require rounding to lot sizes, which is not currently modeled in the backtest partial exits.
- **Fees/Slippage on BE**: A break-even stop actually incurs slippage and exchange fees on the exit order, meaning a "break-even" trade yields a slight negative PnL. The backtest correctly calculates this.
