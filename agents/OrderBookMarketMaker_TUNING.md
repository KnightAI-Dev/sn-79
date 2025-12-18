# OrderBookMarketMaker: Parameter Tuning Guide

## Quick Start Configurations

### Configuration 1: Conservative Baseline
**Use when**: Starting out, testing on testnet, or uncertain market conditions

```bash
python OrderBookMarketMaker.py \
    --netuid 79 \
    --subtensor.chain_endpoint finney \
    --wallet.name your_wallet \
    --wallet.hotkey your_hotkey \
    --agent.name OrderBookMarketMaker \
    --agent.params \
        base_order_size=0.5 \
        max_order_size=2.0 \
        min_spread_fraction=0.35 \
        max_spread_fraction=0.75 \
        max_inventory_fraction=0.25 \
        inventory_skew_strength=2.0 \
        imbalance_lookback=5 \
        imbalance_depth=5 \
        imbalance_threshold=0.15 \
        trade_imbalance_threshold=0.30 \
        toxic_flow_penalty=2.0 \
        order_expiry=60000000000
```

**Expected Performance:**
- Sharpe: 1.5-2.5
- Activity Factor: 1.3-1.6
- Risk: Low
- Suitable for: First deployment, risk-averse miners

---

### Configuration 2: Balanced Aggressive
**Use when**: Comfortable with baseline, want to optimize rewards

```bash
python OrderBookMarketMaker.py \
    --netuid 79 \
    --subtensor.chain_endpoint finney \
    --wallet.name your_wallet \
    --wallet.hotkey your_hotkey \
    --agent.name OrderBookMarketMaker \
    --agent.params \
        base_order_size=1.0 \
        max_order_size=3.0 \
        min_spread_fraction=0.30 \
        max_spread_fraction=0.70 \
        max_inventory_fraction=0.30 \
        inventory_skew_strength=2.5 \
        imbalance_lookback=5 \
        imbalance_depth=5 \
        imbalance_threshold=0.12 \
        trade_imbalance_threshold=0.30 \
        toxic_flow_penalty=2.5 \
        order_expiry=45000000000
```

**Expected Performance:**
- Sharpe: 2.0-3.5
- Activity Factor: 1.6-1.9
- Risk: Medium
- Suitable for: Optimization phase, competitive mining

---

### Configuration 3: Maximum Volume
**Use when**: Confident in strategy, optimizing for activity factor

```bash
python OrderBookMarketMaker.py \
    --netuid 79 \
    --subtensor.chain_endpoint finney \
    --wallet.name your_wallet \
    --wallet.hotkey your_hotkey \
    --agent.name OrderBookMarketMaker \
    --agent.params \
        base_order_size=1.5 \
        max_order_size=4.0 \
        min_spread_fraction=0.25 \
        max_spread_fraction=0.65 \
        max_inventory_fraction=0.35 \
        inventory_skew_strength=3.0 \
        imbalance_lookback=5 \
        imbalance_depth=5 \
        imbalance_threshold=0.10 \
        trade_imbalance_threshold=0.35 \
        toxic_flow_penalty=3.0 \
        order_expiry=30000000000
```

**Expected Performance:**
- Sharpe: 1.8-3.0
- Activity Factor: 1.8-2.0 (near maximum)
- Risk: High
- Suitable for: Experienced miners, when outperforming competition is critical

**⚠️ Warning**: Higher risk of inventory swings and adverse selection. Monitor closely!

---

## Parameter-by-Parameter Guide

### 1. `base_order_size` (Critical: ★★★★★)

**What it does**: Base quantity for each limit order

**Impact on rewards**:
- ↑ Higher → More volume → Higher activity factor
- ↑ Higher → More risk → Lower Sharpe if not managed well

**Tuning process**:
```
Start: 0.5
Goal: Achieve activity_factor = 1.5-1.8

If activity_factor < 1.3:
    → Increase to 0.75, monitor for 1h
    → If Sharpe stable, increase to 1.0
    → Continue until activity_factor > 1.5

If Sharpe drops below 1.5:
    → Decrease by 25%
    → Check inventory management parameters
```

**Safe range**: 0.3 - 2.0
**Optimal for most**: 0.8 - 1.2

---

### 2. `min_spread_fraction` (Critical: ★★★★★)

**What it does**: How far inside the spread to place orders (as fraction of total spread)

**Impact on rewards**:
- ↓ Lower (more aggressive) → More fills → Higher volume
- ↓ Lower → More adverse selection → Lower Sharpe
- ↑ Higher (more passive) → Fewer fills → Lower volume

**Tuning process**:
```
Start: 0.35 (safe)
Goal: Maximize fills while maintaining Sharpe > 2.0

Monitor fill_rate (% of orders that execute):
- If fill_rate < 20% and Sharpe > 2.5:
    → Decrease to 0.30 (more aggressive)
    → Monitor for adverse selection
    
- If Sharpe < 1.5 or frequent adverse selection:
    → Increase to 0.40 (more passive)
    → Accept lower fill rate for better Sharpe
```

**Safe range**: 0.25 - 0.45
**Optimal for most**: 0.28 - 0.35

**Trade-off curve**:
```
0.20: Very aggressive, high volume, high adverse selection risk
0.30: Balanced, good volume, moderate protection
0.40: Conservative, lower volume, strong protection
0.50: Very passive, low volume, minimal adverse selection
```

---

### 3. `inventory_skew_strength` (Critical: ★★★★★)

**What it does**: How aggressively to skew quotes based on inventory position

**Impact on rewards**:
- ↑ Higher → Stronger inventory control → Lower variance → Higher Sharpe
- ↑ Higher → May reduce fill rate on profitable side

**Tuning process**:
```
Start: 2.0
Goal: Keep inventory_position between -0.3 and +0.3 most of the time

Monitor inventory_volatility (std dev of inventory_position):
- If inventory_volatility > 0.4:
    → Inventory swinging too much
    → Increase to 2.5, then 3.0 if needed
    
- If inventory_volatility < 0.15:
    → Too conservative, missing opportunities
    → Decrease to 1.5
```

**Safe range**: 1.5 - 4.0
**Optimal for most**: 2.0 - 2.5

**Visual guide**:
```
inventory_skew_strength = 1.0: Weak control, large swings
inventory_skew_strength = 2.0: Balanced
inventory_skew_strength = 3.0: Strong control, tight range
inventory_skew_strength = 5.0: Over-constrained, may miss fills
```

---

### 4. `imbalance_threshold` (Medium: ★★★)

**What it does**: Minimum imbalance value to trigger directional bias

**Impact on rewards**:
- ↓ Lower → More signals → More directional trades → Higher returns if signal valid
- ↓ Lower → More noise → May trade on false signals

**Tuning process**:
```
Start: 0.15
Goal: Trade on strong signals, ignore noise

Track signal_accuracy (% of imbalance signals that predict correct direction):
- If signal_accuracy > 60% with threshold=0.15:
    → Signal is strong, decrease to 0.12 to capture more
    
- If signal_accuracy < 55%:
    → Too much noise, increase to 0.18 or 0.20
```

**Safe range**: 0.08 - 0.25
**Optimal for most**: 0.10 - 0.15

---

### 5. `imbalance_depth` (Medium: ★★★)

**What it does**: Number of order book levels to include in imbalance calculation

**Impact on rewards**:
- ↑ Higher → More information → Smoother signal
- ↓ Lower → Emphasize top-of-book → More responsive

**Tuning process**:
```
Start: 5
Goal: Balance signal quality vs responsiveness

Typical behavior by market:
- Liquid, tight spread markets: depth=3 (top levels most informative)
- Less liquid markets: depth=7 (need deeper book context)
- Very liquid markets: depth=5 (balanced)
```

**Safe range**: 3 - 10
**Optimal for most**: 5

**Rule of thumb**: If spread < 0.1% of price → depth=3, else depth=5-7

---

### 6. `toxic_flow_penalty` (Medium: ★★★)

**What it does**: Multiplier to widen spreads when adverse selection detected

**Impact on rewards**:
- ↑ Higher → Stronger protection → Better Sharpe in volatile periods
- ↑ Higher → May miss legitimate opportunities

**Tuning process**:
```
Start: 2.0
Goal: Protect from adverse selection without being overly cautious

Track adverse_selection_pnl (PnL during toxic flow periods):
- If losing money consistently during toxic flow:
    → Increase to 2.5 or 3.0
    
- If rarely detecting toxic flow but Sharpe is good:
    → Keep at 2.0, detection is working
    
- If detecting toxic flow too often (>20% of time):
    → May be over-sensitive
    → Increase trade_imbalance_threshold instead
```

**Safe range**: 1.5 - 4.0
**Optimal for most**: 2.0 - 2.5

---

### 7. `order_expiry` (Low: ★★)

**What it does**: How long (in nanoseconds) before orders automatically cancel

**Impact on rewards**:
- ↓ Lower → Orders stay fresh → Better price tracking
- ↓ Lower → More cancellation messages → Slightly slower
- ↑ Higher → Less overhead → May accumulate stale orders

**Tuning process**:
```
Start: 60000000000 (60 seconds)
Goal: Balance freshness vs overhead

Typical settings by update frequency:
- publish_interval = 1s: order_expiry = 30s
- publish_interval = 5s: order_expiry = 60s
- publish_interval = 10s: order_expiry = 120s
```

**Safe range**: 20_000_000_000 - 120_000_000_000 (20s - 120s)
**Optimal for most**: 45_000_000_000 (45s)

---

## Diagnostic Decision Tree

### Problem: Low Sharpe Ratio (< 1.5)

```
Low Sharpe
├─ Is inventory_volatility > 0.4?
│  ├─ YES → Increase inventory_skew_strength to 2.5-3.0
│  └─ NO → Continue
│
├─ Is adverse_selection_detected > 15% of time?
│  ├─ YES → Increase toxic_flow_penalty to 2.5-3.0
│  │        or increase min_spread_fraction to 0.35-0.40
│  └─ NO → Continue
│
├─ Is fill_rate > 50%?
│  ├─ YES → Too aggressive, increase min_spread_fraction to 0.35
│  └─ NO → Continue
│
└─ Check for systematic losses
   └─ Review logs for patterns
```

### Problem: Low Activity Factor (< 1.5)

```
Low Activity Factor
├─ Is fill_rate < 25%?
│  ├─ YES → Decrease min_spread_fraction to 0.28-0.30
│  │        or increase base_order_size by 50%
│  └─ NO → Continue
│
├─ Is base_order_size < 0.8?
│  ├─ YES → Increase to 1.0 (monitor Sharpe)
│  └─ NO → Continue
│
├─ Are orders being cancelled too often?
│  ├─ YES → Increase order_expiry to 60-90s
│  └─ NO → Continue
│
└─ Check for order rejections
   └─ Review notices for placement failures
```

### Problem: High Outlier Penalty

```
High Outlier Penalty
├─ Are 1-2 books performing much worse than others?
│  ├─ YES → Those books may have different characteristics
│  │        → Consider book-specific parameter adaptation
│  └─ NO → Continue
│
├─ Is overall Sharpe good but median much lower?
│  ├─ YES → Some books pulling down performance
│  │        → Implement fallback: if book_sharpe < 0.5, go passive
│  └─ NO → Continue
│
└─ Check for cross-book correlation issues
   └─ May need to reduce global risk exposure
```

---

## Real-Time Monitoring Checklist

### Every 15 Minutes
- [ ] Check response time (should be < 0.5s)
- [ ] Verify no error messages in logs
- [ ] Confirm orders are being placed and filled

### Every Hour
- [ ] Review Sharpe ratio per book (target: > 1.5)
- [ ] Check activity factor (target: > 1.5)
- [ ] Monitor inventory positions (should be bounded)
- [ ] Review adverse selection detection rate (< 15%)

### Every 4 Hours
- [ ] Calculate fill rate (target: 30-50%)
- [ ] Review PnL trend (should be steadily positive)
- [ ] Check for books with poor performance
- [ ] Assess need for parameter adjustments

### Daily
- [ ] Calculate overall score vs competitors
- [ ] Review outlier penalty
- [ ] Analyze failure modes
- [ ] Plan parameter optimizations

---

## Advanced: Market-Adaptive Parameters

For experienced miners, implement dynamic parameter adjustment:

```python
# Pseudo-code for adaptive parameters
def adapt_parameters(self, book_id, recent_performance):
    """Adjust parameters based on book-specific performance"""
    
    # If Sharpe is low on this book
    if recent_sharpe[book_id] < 1.0:
        # Go more conservative
        self.min_spread_fraction[book_id] *= 1.1
        self.inventory_skew_strength[book_id] *= 1.2
    
    # If Sharpe is high but activity is low
    elif recent_sharpe[book_id] > 2.5 and activity_factor[book_id] < 1.5:
        # Be more aggressive
        self.base_order_size[book_id] *= 1.2
        self.min_spread_fraction[book_id] *= 0.95
    
    # If detecting adverse selection frequently
    if adverse_selection_rate[book_id] > 0.20:
        self.toxic_flow_penalty[book_id] *= 1.1
```

---

## Testing Protocol

### Phase 1: Local Testing (Proxy)
1. Set up local simulator proxy
2. Run with conservative config for 1-2 hours
3. Verify: No crashes, orders placed correctly, reasonable PnL

### Phase 2: Testnet (netuid 366)
1. Deploy conservative config
2. Run for 6-12 hours
3. Monitor: Sharpe > 1.0, activity_factor > 1.2, no critical errors

### Phase 3: Mainnet Conservative (netuid 79)
1. Deploy conservative config
2. Run for 24-48 hours
3. Target: Sharpe > 1.5, activity_factor > 1.3

### Phase 4: Mainnet Optimization
1. Gradually increase aggressiveness
2. Test one parameter change at a time
3. Wait 4-6 hours between changes
4. Target: Sharpe > 2.0, activity_factor > 1.6

---

## Emergency Troubleshooting

### Issue: Losing Money Rapidly
**Immediate action**:
1. Increase `min_spread_fraction` to 0.45 (very passive)
2. Reduce `base_order_size` by 50%
3. Monitor for 1 hour
4. Investigate root cause before re-optimizing

### Issue: No Orders Executing
**Immediate action**:
1. Check balance (may be insufficient funds)
2. Decrease `min_spread_fraction` to 0.25
3. Verify no order rejections in logs
4. Check max_open_orders not exceeded

### Issue: Timeout Errors
**Immediate action**:
1. Optimize code (remove unnecessary logging)
2. Reduce number of calculations
3. Consider using lazy loading for state
4. Check hardware resources

---

## Summary: Safe Optimization Path

```
Week 1: Conservative baseline
  ├─ Verify stability
  ├─ Build confidence
  └─ Learn market dynamics

Week 2: Increase volume
  ├─ base_order_size: 0.5 → 1.0
  ├─ Monitor Sharpe stability
  └─ Target activity_factor > 1.5

Week 3: Optimize spreads
  ├─ min_spread_fraction: 0.35 → 0.30
  ├─ Balance fills vs adverse selection
  └─ Target Sharpe > 2.0

Week 4: Fine-tune risk
  ├─ Adjust inventory_skew_strength
  ├─ Optimize adverse selection protection
  └─ Target outlier_penalty < 5%

Ongoing: Monitor and adapt
  ├─ Track competitive position
  ├─ Respond to market changes
  └─ Continuous improvement
```

**Remember**: Slow and steady wins in SN-79. Stability beats aggression. Good luck! 🎯
