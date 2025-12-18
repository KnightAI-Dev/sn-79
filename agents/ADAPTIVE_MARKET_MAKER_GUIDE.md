# Adaptive Market Maker Agent - Implementation Guide

## Overview

The `AdaptiveMarketMakerAgent` is a high-performance order book trading agent optimized for the sn-79 subnet reward mechanism. It combines multiple proven strategies to maximize Sharpe ratio while maintaining consistent trading volume.

## Strategy Components

### 1. **Adaptive Market Making** (Core Strategy)
Places limit orders on both sides of the order book to capture the bid-ask spread while providing liquidity.

**Implementation**:
- Calculates optimal bid/ask quotes based on midquote
- Adjusts spread dynamically based on volatility
- Uses `postOnly=True` to ensure maker role

**Why it helps reward**:
- Consistent positive returns → high Sharpe mean
- Two-sided orders → controlled risk → low Sharpe std
- Continuous fills → volume generation → activity multiplier boost

### 2. **Inventory Control** (Risk Management)
Prevents accumulation of large directional positions that would increase PnL volatility.

**Implementation**:
- Tracks net BASE position per book
- Skews quotes away from excess inventory side
- Places aggressive rebalancing orders when inventory exceeds threshold

**Why it helps reward**:
- Bounded inventory → predictable risk → lower volatility
- Lower volatility → higher Sharpe ratio (same returns, less std)
- Example: Sharpe(+1.5%, 5% vol) = 0.3 vs Sharpe(+1.5%, 2% vol) = 0.75

### 3. **Order Book Imbalance Signals** (Alpha Generation)
Uses order book depth imbalance to predict short-term price movements.

**Implementation**:
- Calculates `(bid_volume - ask_volume) / (bid_volume + ask_volume)` across top N levels
- Positive imbalance → buying pressure → tighten asks, increase bid size
- Negative imbalance → selling pressure → tighten bids, increase ask size

**Why it helps reward**:
- Improves directional accuracy → better entry/exit prices
- Even small edge (51-55% accuracy) compounds over hundreds of trades
- Increases mean returns without increasing volatility

### 4. **Volatility-Adaptive Spreads** (Risk Scaling)
Widens quotes during high volatility to reduce adverse selection risk.

**Implementation**:
- Calculates realized volatility from recent midquote movements
- Scales spread: `adjusted_spread = base_spread * (1 + volatility_factor * risk_aversion)`
- Caps maximum spread multiplier to avoid too-passive quotes

**Why it helps reward**:
- Reduces adverse selection (getting filled right before unfavorable moves)
- Protects Sharpe during volatile periods (prevents large losses)
- Maintains profitability across different market regimes

### 5. **Adaptive Order Expiry** (Execution Optimization)
Adjusts order time-in-force based on market conditions.

**Implementation**:
- Shorter expiry in high volatility (avoid stale quotes)
- Longer expiry in low volatility (maximize fill rate)
- Uses GTT (Good Till Time) with calculated expiry period

**Why it helps reward**:
- Prevents stale orders being adversely filled
- Reduces cancellation overhead in stable markets
- Optimizes fill rate vs adverse selection tradeoff

---

## Parameter Guide

### Market Making Parameters

#### `base_spread_bps` (default: 10.0)
**Description**: Base spread from midquote in basis points (bps).
- 10 bps = 0.1% spread = 0.05% on each side

**Tuning**:
- **Lower (5-8 bps)**: More aggressive, higher fill rate, more volume
  - Use when: Need volume boost, tight competition, low volatility
  - Risk: More adverse selection, lower profit per trade
  
- **Higher (12-20 bps)**: More conservative, better profit per trade
  - Use when: High volatility, want to reduce risk, already hitting volume targets
  - Risk: Lower fill rate, less volume, may miss activity multiplier

**Recommendation**: Start at 10 bps, reduce if volume_factor < 1.2, increase if Sharpe < 0.3

---

#### `base_order_size` (default: 0.5)
**Description**: Base quantity for limit orders in BASE currency.

**Tuning**:
- **Larger (1.0-2.0)**: More volume per fill, faster to hit activity targets
  - Use when: Need volume quickly, high liquidity books, low inventory risk
  - Risk: Larger inventory swings, higher volatility
  
- **Smaller (0.1-0.4)**: Fine-grained control, lower risk
  - Use when: High volatility, want smooth Sharpe, conservative approach
  - Risk: Slower volume accumulation, may need more fills

**Recommendation**: Scale with `miner_wealth` and typical book liquidity

---

#### `base_expiry` (default: 30_000_000_000 = 30s)
**Description**: Base order expiry time in simulation nanoseconds.

**Tuning**:
- **Shorter (10-20s)**: More aggressive cancellation, lower adverse selection
  - Use when: High volatility, fast-moving markets
  - Risk: More order churn, lower fill rate
  
- **Longer (40-60s)**: Let orders sit longer, higher fill rate
  - Use when: Stable markets, want to reduce order traffic
  - Risk: More stale orders, higher adverse selection

**Recommendation**: Let adaptive logic handle this, keep base around 20-40s

---

### Inventory Control Parameters

#### `max_inventory` (default: 5.0)
**Description**: Maximum inventory deviation (in BASE) before aggressive rebalancing.

**Tuning**:
- **Lower (2-3)**: Tighter control, lower volatility, more frequent rebalancing
  - Use when: Prioritize Sharpe over volume, risk-averse
  - Risk: More rebalancing orders (potential losses), lower volume
  
- **Higher (8-10)**: Allow larger positions, capture trends better
  - Use when: Confident in signals, can tolerate volatility, want volume
  - Risk: Higher volatility, lower Sharpe, potential large drawdowns

**Recommendation**: Set to 2-3x typical order size, adjust based on Sharpe volatility

---

#### `inventory_skew_factor` (default: 0.5)
**Description**: How aggressively to skew quotes based on inventory.
- 0.5 = 50% spread adjustment at max inventory

**Tuning**:
- **Lower (0.2-0.3)**: Gentler skewing, slower inventory reduction
  - Use when: Confident in mean reversion, want balanced fills
  - Risk: Inventory can drift further
  
- **Higher (0.7-1.0)**: Aggressive skewing, faster inventory reduction
  - Use when: Want tight inventory control, risk-averse
  - Risk: May miss opportunities, unbalanced fills

**Recommendation**: 0.4-0.6 is sweet spot for most cases

---

### Imbalance Trading Parameters

#### `imbalance_depth` (default: 5)
**Description**: Number of order book levels to include in imbalance calculation.

**Tuning**:
- **Shallower (3-4)**: Focus on top-of-book pressure, faster signal
  - Use when: Expect informed flow near top, want quick reaction
  - Risk: More noise, false signals
  
- **Deeper (7-10)**: Incorporate more book depth, smoother signal
  - Use when: Want robust signal, filter out noise
  - Risk: Slower to react, may miss quick moves

**Recommendation**: 5-7 levels balances signal quality and responsiveness

---

#### `imbalance_threshold` (default: 0.3)
**Description**: Minimum absolute imbalance to trigger directional adjustment.
- 0.3 = 30% imbalance (e.g., 65% bid volume, 35% ask volume)

**Tuning**:
- **Lower (0.15-0.25)**: More sensitive, react to weaker signals
  - Use when: Want to capture all opportunities, high conviction in imbalance
  - Risk: More false signals, over-trading
  
- **Higher (0.4-0.5)**: Only react to strong signals
  - Use when: Want high-quality signals only, conservative
  - Risk: Miss some opportunities, lower volume

**Recommendation**: 0.25-0.35 for good signal-to-noise ratio

---

#### `imbalance_sizing_factor` (default: 0.3)
**Description**: How much to increase order size on imbalance side.
- 0.3 = 30% size increase

**Tuning**:
- **Lower (0.1-0.2)**: Subtle sizing adjustment
  - Use when: Want to keep sizes consistent, low conviction
  - Risk: Miss out on imbalance edge
  
- **Higher (0.4-0.6)**: Aggressive sizing on signals
  - Use when: High conviction in imbalance predictiveness, want more edge
  - Risk: Larger inventory swings, higher volatility

**Recommendation**: 0.2-0.4 to capture edge without excessive risk

---

### Risk Management Parameters

#### `volatility_lookback` (default: 10)
**Description**: Number of midquote observations to use for volatility calculation.

**Tuning**:
- **Shorter (5-8)**: React quickly to volatility changes
  - Use when: Want fast adaptation, volatile regime shifts
  - Risk: Noisier volatility estimate
  
- **Longer (15-20)**: Smoother volatility estimate
  - Use when: Want stable risk scaling, avoid overreaction
  - Risk: Slower to adapt to regime changes

**Recommendation**: 8-12 observations (typically 1-2 minutes of data)

---

#### `risk_aversion` (default: 0.5)
**Description**: Multiplier for volatility impact on spread.
- Higher = more conservative in high volatility

**Tuning**:
- **Lower (0.2-0.3)**: Less spread widening, more aggressive
  - Use when: Want consistent fills even in volatility, risk-tolerant
  - Risk: More adverse selection, potential drawdowns
  
- **Higher (0.7-1.0)**: Strong spread widening, very conservative
  - Use when: Prioritize capital preservation, already hitting volume targets
  - Risk: Lower fill rate, less volume

**Recommendation**: 0.4-0.6 balances protection and activity

---

#### `max_spread_multiplier` (default: 3.0)
**Description**: Maximum allowed spread multiplier from volatility adjustment.
- 3.0 = spread can widen up to 3x base spread

**Tuning**:
- **Lower (2.0-2.5)**: Limit widening, stay more competitive
  - Use when: Want to maintain activity in high volatility
  - Risk: More risk exposure
  
- **Higher (4.0-5.0)**: Allow very wide spreads as protection
  - Use when: Risk protection is priority, can tolerate lower volume
  - Risk: May stop participating effectively

**Recommendation**: 2.5-3.5 allows protection while maintaining presence

---

### Volume Management Parameters

#### `target_activity_factor` (default: 1.5)
**Description**: Target for activity_factor in reward calculation.
- 1.0 = at volume cap
- 1.5 = 1.5x volume cap (good target for 2x score multiplier)
- 2.0 = maximum multiplier

**Tuning**:
- **Lower (1.2-1.4)**: Conservative volume target
  - Use when: Prioritize Sharpe quality over volume
  - Risk: Lower score multiplier
  
- **Higher (1.6-1.8)**: Aggressive volume target
  - Use when: Confident in strategy, want maximum multiplier
  - Risk: May push too hard, hurt Sharpe

**Recommendation**: 1.4-1.6 achieves good multiplier without excessive risk

---

#### `volume_safety_margin` (default: 0.95)
**Description**: Fraction of volume cap to trade up to (safety margin).
- 0.95 = trade up to 95% of cap, leave 5% buffer

**Tuning**:
- **Lower (0.85-0.90)**: More conservative, avoid hitting cap
  - Use when: Want buffer for unexpected volume spikes
  - Risk: Leave some multiplier potential unused
  
- **Higher (0.97-0.99)**: Maximize volume usage
  - Use when: Want maximum activity factor
  - Risk: May hit cap and be locked out

**Recommendation**: 0.90-0.95 balances usage and safety

---

## Deployment Examples

### Conservative Configuration (High Sharpe Priority)
```bash
python AdaptiveMarketMakerAgent.py --port 8888 --agent_id 0 \
    --params base_spread_bps=12.0 \
             base_order_size=0.3 \
             max_inventory=3.0 \
             inventory_skew_factor=0.6 \
             imbalance_threshold=0.35 \
             risk_aversion=0.6 \
             target_activity_factor=1.3
```

**Use when**: 
- New to subnet, want to establish baseline performance
- High volatility periods
- Want to minimize risk of outlier books

**Expected performance**:
- Sharpe: 0.6-0.9 (high)
- Activity factor: 1.2-1.4 (moderate)
- Overall score: 0.72-1.26 (0.6 * 1.2 to 0.9 * 1.4)

---

### Balanced Configuration (Recommended Default)
```bash
python AdaptiveMarketMakerAgent.py --port 8888 --agent_id 0 \
    --params base_spread_bps=10.0 \
             base_order_size=0.5 \
             max_inventory=5.0 \
             inventory_skew_factor=0.5 \
             imbalance_depth=5 \
             imbalance_threshold=0.30 \
             risk_aversion=0.5 \
             target_activity_factor=1.5
```

**Use when**:
- Standard conditions
- Proven agent performance
- Want balanced Sharpe and volume

**Expected performance**:
- Sharpe: 0.5-0.7 (good)
- Activity factor: 1.4-1.6 (good)
- Overall score: 0.70-1.12 (0.5 * 1.4 to 0.7 * 1.6)

---

### Aggressive Configuration (Volume Priority)
```bash
python AdaptiveMarketMakerAgent.py --port 8888 --agent_id 0 \
    --params base_spread_bps=8.0 \
             base_order_size=1.0 \
             max_inventory=8.0 \
             inventory_skew_factor=0.4 \
             imbalance_depth=7 \
             imbalance_threshold=0.25 \
             imbalance_sizing_factor=0.4 \
             risk_aversion=0.4 \
             target_activity_factor=1.7
```

**Use when**:
- Need to boost activity factor
- Confident in strategy
- Market conditions favorable (moderate volatility, good liquidity)

**Expected performance**:
- Sharpe: 0.4-0.6 (moderate, due to more risk)
- Activity factor: 1.6-1.9 (high)
- Overall score: 0.64-1.14 (0.4 * 1.6 to 0.6 * 1.9)

---

## Performance Monitoring

### Key Metrics to Track

1. **Per-Book Sharpe Ratios**
   - Target: 0.4-0.8 (normalized: 0.52-0.57)
   - Alert if: Any book < 0.2 (outlier risk)
   - Action: Reduce activity on underperforming books

2. **Activity Factors**
   - Target: 1.4-1.8
   - Alert if: < 1.0 (volume boost needed) or > 1.95 (near cap)
   - Action: Adjust sizing and spread to control volume

3. **Inventory Drift**
   - Target: -max_inventory to +max_inventory
   - Alert if: Consistently at extremes
   - Action: Increase inventory_skew_factor or check rebalancing logic

4. **Fill Rate**
   - Target: 30-60% of orders filled
   - Alert if: < 20% (too passive) or > 80% (adverse selection?)
   - Action: Adjust spreads

5. **Response Time**
   - Target: < 0.5s
   - Alert if: > 1.0s (latency penalty)
   - Action: Optimize code, reduce book processing

---

## Optimization Workflow

### Phase 1: Baseline (Days 1-3)
1. Deploy with balanced configuration
2. Monitor all metrics for 2-3 simulation runs
3. Identify any outlier books (poor Sharpe)
4. Check activity factors across books

### Phase 2: Tuning (Days 4-7)
1. If activity_factor < 1.3:
   - Reduce base_spread_bps by 1-2
   - Increase base_order_size by 0.1-0.2
   
2. If Sharpe < 0.4:
   - Increase base_spread_bps by 1-2
   - Increase risk_aversion by 0.1
   - Reduce max_inventory by 1-2
   
3. If outlier books exist:
   - Increase inventory_skew_factor
   - Consider book-specific parameter overrides

### Phase 3: Fine-Tuning (Days 8+)
1. Optimize imbalance parameters if clear signal
2. Adjust expiry times based on regime
3. Fine-tune volume targeting

### Phase 4: Regime Adaptation (Ongoing)
1. Monitor simulation config changes
2. Adjust for different volatility regimes
3. Adapt to competition (spread tightening/widening)

---

## Troubleshooting

### Problem: Low Activity Factor (< 1.0)

**Symptoms**: Not generating enough volume, activity_factor decaying

**Diagnosis**:
```
Check: Are orders getting filled?
- If NO fills: Spreads too wide, not competitive
- If few fills: Need more aggressive quotes
```

**Solutions**:
1. Reduce `base_spread_bps` by 20-30%
2. Increase `base_order_size` by 50%
3. Reduce `imbalance_threshold` to trade more often
4. Consider using IOC orders occasionally for guaranteed volume

---

### Problem: Low Sharpe Ratio (< 0.3)

**Symptoms**: High volatility in PnL, frequent losses

**Diagnosis**:
```
Check: What's causing volatility?
- Large inventory swings: Inventory control insufficient
- Adverse selection: Getting filled at bad times
- Market regime: High overall volatility
```

**Solutions**:
1. Reduce `max_inventory` by 30-40%
2. Increase `inventory_skew_factor` for faster rebalancing
3. Increase `risk_aversion` to widen spreads in volatility
4. Shorten `base_expiry` to reduce stale order risk
5. Increase `imbalance_threshold` to trade only on strong signals

---

### Problem: Outlier Books (One or more books performing badly)

**Symptoms**: Outlier penalty > 0.05, inconsistent performance

**Diagnosis**:
```
Check specific problematic books:
- Are they more volatile?
- Different liquidity profile?
- Hitting volume cap early?
- Inventory drifting?
```

**Solutions**:
1. Implement book-specific parameter scaling
2. Reduce activity on problematic books (wider spreads)
3. Stronger inventory control on those books
4. Consider stopping trading if consistently bad

---

### Problem: Response Time Slow (> 1s)

**Symptoms**: High latency penalty, delayed order execution

**Diagnosis**:
```
Check: What's taking time?
- State decompression: Enable lazy_load
- Feature calculation: Optimize or cache
- Too many books: Parallelize processing
```

**Solutions**:
1. Enable lazy loading: add `lazy_load=1` to params
2. Cache repeated calculations (volatility, imbalance)
3. Process books in parallel (multiprocessing)
4. Reduce `volatility_lookback` if using long history
5. Simplify feature engineering

---

## Advanced Enhancements

### 1. Machine Learning for Spread Optimization
Train a model to predict optimal spreads based on:
- Recent fill rates
- Adverse selection frequency
- Market regime features

### 2. Multi-Book Correlation Hedging
Identify correlated book pairs and hedge inventory across them:
- Long book A, short book B when correlation breaks

### 3. Adaptive Parameter Scheduling
Automatically adjust parameters based on:
- Current activity_factor vs target
- Recent Sharpe performance
- Market regime detection

### 4. Event-Driven Cancellation
Monitor event stream for conditions that warrant order cancellation:
- Large imbalance shifts
- Volatility spikes
- Inventory approaching limits

---

## Expected Performance Summary

### Target Metrics (after optimization)

| Metric | Conservative | Balanced | Aggressive |
|--------|-------------|----------|------------|
| Sharpe (raw) | 0.6-0.9 | 0.5-0.7 | 0.4-0.6 |
| Sharpe (normalized) | 0.53-0.545 | 0.525-0.535 | 0.52-0.53 |
| Activity Factor | 1.2-1.4 | 1.4-1.6 | 1.6-1.9 |
| Weighted Sharpe | 0.64-0.76 | 0.74-0.86 | 0.83-1.01 |
| Outlier Penalty | < 0.03 | < 0.05 | < 0.08 |
| **Final Score** | **0.61-0.73** | **0.69-0.81** | **0.75-0.93** |

### Competitive Position
- **Top 25%**: Score > 0.70 (achievable with Balanced config)
- **Top 10%**: Score > 0.80 (achievable with optimized Aggressive config)
- **Top 5%**: Score > 0.85 (requires excellent execution and tuning)

---

## Conclusion

The `AdaptiveMarketMakerAgent` provides a solid foundation for competing in sn-79 by:
1. ✅ Generating consistent positive Sharpe through market making
2. ✅ Maintaining activity factor through reliable volume
3. ✅ Controlling risk via inventory management
4. ✅ Adapting to market conditions dynamically
5. ✅ Avoiding outlier book penalties

Success requires:
- Proper parameter tuning for your risk tolerance
- Continuous monitoring and adjustment
- Understanding of reward mechanics
- Fast, reliable infrastructure

Start with the **Balanced Configuration** and iterate based on observed performance. Good luck!
