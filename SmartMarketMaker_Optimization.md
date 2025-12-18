# SmartMarketMaker Optimization Guide

This agent implements a high-performance market making strategy designed for the sn-79 subnet. It combines Avellaneda-Stoikov inventory management with Microstructure signals (OBI).

## Core Logic

1.  **Fair Price Calculation**:
    $$P_{fair} = P_{mid} + (Spread \times \alpha \times OBI)$$
    Where $\alpha$ is `obi_factor`. This shifts our center price towards the pressure direction (buying into buying pressure, selling into selling pressure).

2.  **Spread Sizing**:
    $$Spread = P_{mid} \times BaseSpread \times (1 + 100 \times \sigma)$$
    We widen the spread when volatility ($\sigma$) increases to protect against adverse selection.

3.  **Inventory Skew**:
    $$Skew = -\gamma \times Inventory \times Spread$$
    Where $\gamma$ is `risk_aversion`. If we hold long inventory, we lower both bid and ask to encourage selling and discourage buying.

## Parameter Tuning

### 1. `base_spread_bps` (Default: 20.0)
*   **Impact**: Controls baseline profitability and fill rate.
*   **Tuning**:
    *   **Decrease** if volume is too low (not getting fills).
    *   **Increase** if Sharpe ratio is negative (getting run over by toxic flow).
*   **Safe Range**: 5.0 - 50.0 bps.

### 2. `risk_aversion` (Default: 0.5)
*   **Impact**: Controls how aggressively the agent dumps inventory.
*   **Tuning**:
    *   **Increase** if you frequently get stuck with large positions that drawdown PnL.
    *   **Decrease** if the agent is "panic dumping" too often and paying crossing spreads unnecessarily.
*   **Safe Range**: 0.1 - 2.0.

### 3. `obi_factor` (Default: 0.3)
*   **Impact**: Controls sensitivity to order book imbalance.
*   **Tuning**:
    *   **Increase** to capture trend momentum (alpha).
    *   **Decrease** if you are getting "spoofed" or suffering from false signals.
*   **Safe Range**: 0.0 - 0.8.

### 4. `levels` (Default: 5) and `level_spacing_bps` (Default: 5.0)
*   **Impact**: Creates a "ladder" of liquidity.
*   **Benefit**: Captures volatility spikes without committing all capital at the tightest spread.
*   **Tuning**: More levels = smoother PnL but higher complexity/msg rate.

## Deployment Strategy

1.  **Dry Run**: Run locally with `proxy` to ensure no errors.
2.  **Testnet**: Deploy with conservative settings (`base_spread_bps=30`, `risk_aversion=1.0`).
3.  **Production**: Gradually tighten spread until volume target is met, then optimize Sharpe.

## Common Pitfalls

*   **Latency**: If the agent is too slow calculating history, it misses the `publish_interval`. Ensure `parallel_history_workers` is set if using heavy history features.
*   **Over-trading**: If `risk_aversion` is too high, the agent might oscillate (buy -> panic sell -> buy).
*   **Toxic Flow**: If `vol_window` is too slow, the agent won't widen spreads fast enough during crashes.

## Alpha Sources

*   **Inventory Control**: The primary source of Sharpe in this subnet is NOT predicting price, but **surviving** price moves by being flat.
*   **OBI**: Provides a slight edge in directionality.
