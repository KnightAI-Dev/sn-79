# Order Book Market Maker — Quick Parameter Reference

## 🎯 Fast Parameter Selection Guide

### Scenario-Based Presets

#### 📊 **I'm just starting / Testing the agent**
```bash
--agent.params \
  base_order_size=0.3 \
  max_inventory_pct=0.20 \
  target_spread_bps=12 \
  min_edge_bps=3
```
- Lower risk, easier to understand behavior
- Good for learning the agent dynamics
- Expected Score: 3-4

---

#### 🎯 **I want maximum reward (recommended)**
```bash
--agent.params \
  base_order_size=0.5 \
  max_inventory_pct=0.30 \
  target_spread_bps=10 \
  min_edge_bps=2 \
  inventory_skew_factor=0.5
```
- Balanced risk/reward
- Optimized for validator scoring function
- Expected Score: 4-5

---

#### 🚀 **I want to be top 1% (advanced)**
```bash
--agent.params \
  base_order_size=0.8 \
  max_inventory_pct=0.35 \
  target_spread_bps=8 \
  min_edge_bps=1.5 \
  inventory_skew_factor=0.4 \
  expiry_seconds=45
```
- Higher volume, higher risk
- Requires monitoring for blow-ups
- Expected Score: 5-6+

---

#### 🛡️ **Books are highly correlated / Trending markets**
```bash
--agent.params \
  base_order_size=0.4 \
  max_inventory_pct=0.15 \
  target_spread_bps=15 \
  min_edge_bps=4 \
  inventory_skew_factor=0.7
```
- Very defensive
- Prevents catastrophic losses during regime shifts
- Expected Score: 3-4 (but consistent)

---

#### ⚡ **High HFT competition / Tight spreads**
```bash
--agent.params \
  base_order_size=0.6 \
  max_inventory_pct=0.25 \
  target_spread_bps=7 \
  min_edge_bps=1 \
  imbalance_depths=1,2,3 \
  expiry_seconds=45
```
- Faster signals (shallow depths)
- Tighter spreads to compete
- Higher frequency approach
- Expected Score: 4-5 (if you have low latency)

---

#### 🐢 **Low volatility / Slow markets**
```bash
--agent.params \
  base_order_size=1.0 \
  max_inventory_pct=0.35 \
  target_spread_bps=6 \
  min_edge_bps=1.5 \
  imbalance_depths=3,5,10,15,20 \
  expiry_seconds=90
```
- Larger sizes (more capital usage)
- Deeper signal (stability)
- Longer expiry (less cancellations)
- Expected Score: 4-5

---

## 📈 Parameter Impact Matrix

| Parameter | ↑ Increases | ↓ Decreases | Optimal Range |
|-----------|-------------|-------------|---------------|
| **max_inventory_pct** | Volume, Risk | Sharpe stability | 0.25-0.35 |
| **inventory_skew_factor** | Risk control | Volume | 0.4-0.6 |
| **target_spread_bps** | Edge per trade | Fill rate, Volume | 8-12 |
| **min_edge_bps** | Win rate, Quality | Volume | 1.5-3 |
| **base_order_size** | Volume, Capital usage | Flexibility | 0.3-0.8 |
| **expiry_seconds** | Fill probability | Stale order risk | 45-90 |

---

## 🔧 Real-Time Tuning Guide

### Problem: Score is low (<3.0)

**Diagnostic questions:**

1. **Is Sharpe < 1.5?**
   - ✅ **YES**: Reduce risk
     ```bash
     max_inventory_pct=0.20
     min_edge_bps=3
     ```
   - ❌ **NO**: Go to next question

2. **Is Activity Factor < 1.3?**
   - ✅ **YES**: Increase volume
     ```bash
     base_order_size=0.7
     target_spread_bps=9
     expiry_seconds=75
     ```
   - ❌ **NO**: Go to next question

3. **Is there an outlier penalty (>0.05)?**
   - ✅ **YES**: Tighten risk on all books
     ```bash
     max_inventory_pct=0.25
     inventory_skew_factor=0.6
     ```

---

### Problem: Getting too many fills but losing money

**Diagnosis:** Adverse selection

**Solution:**
```bash
min_edge_bps=3          # Be more selective
target_spread_bps=12    # Wider spreads
```

**Theory:** You're getting picked off on informed flow. Need more edge per trade.

---

### Problem: Not enough fills / Low volume

**Diagnosis:** Too conservative

**Solution:**
```bash
target_spread_bps=8     # Tighter spreads
min_edge_bps=1.5        # Less selective
base_order_size=0.8     # Larger orders
```

**Warning:** Monitor Sharpe carefully. If it drops below 1.5, revert changes.

---

### Problem: Inventory keeps hitting max limit

**Diagnosis:** Insufficient skewing or too-large max

**Solution Option 1 (more aggressive skew):**
```bash
inventory_skew_factor=0.7  # Skew harder
```

**Solution Option 2 (tighter limit):**
```bash
max_inventory_pct=0.25     # Lower limit
```

**Theory:** Either force inventory to mean-revert faster, or reduce the range it can grow.

---

### Problem: High variance across books (outliers)

**Diagnosis:** Parameters too aggressive for some book characteristics

**Solution:**
```bash
max_inventory_pct=0.25      # Uniform risk control
inventory_skew_factor=0.6   # Faster mean reversion
min_edge_bps=2.5            # More selective
```

**Theory:** Outliers occur when one book behaves differently (higher vol, more correlation, etc.). Global risk controls prevent blow-ups.

---

## 🎓 Advanced: When to Deviate from Defaults

### Increase `max_inventory_pct` (>0.30) when:
- ✓ Books are uncorrelated
- ✓ Volatility is low and stable
- ✓ You're confident in signal quality
- ✓ Current score is good but want more volume
- ⚠️ **Risk:** Blow-up if market regime changes

### Decrease `max_inventory_pct` (<0.25) when:
- ✓ Books are correlated (all move together)
- ✓ High volatility / trending markets
- ✓ Experiencing outlier penalties
- ✓ Prioritizing Sharpe over volume
- ⚠️ **Risk:** Low activity factor (<1.3)

### Increase `inventory_skew_factor` (>0.55) when:
- ✓ Inventory frequently hits limits
- ✓ Books show momentum (trending)
- ✓ Experiencing large drawdowns
- ⚠️ **Risk:** Lower volume due to one-sided quoting

### Decrease `inventory_skew_factor` (<0.45) when:
- ✓ Mean-reversion is strong
- ✓ Rarely hit inventory limits
- ✓ Want more volume
- ⚠️ **Risk:** Inventory blow-ups

### Tighten `target_spread_bps` (<9) when:
- ✓ Fill rate is low (<25%)
- ✓ Activity factor is low (<1.3)
- ✓ Competition is tight (many miners)
- ⚠️ **Risk:** Adverse selection, negative edge

### Widen `target_spread_bps` (>11) when:
- ✓ Win rate is low (<50%)
- ✓ Getting picked off frequently
- ✓ High volatility environment
- ⚠️ **Risk:** Low fill rate, low volume

---

## 🧪 A/B Testing Recommendations

### Test #1: Inventory Management
**Control:**
```bash
max_inventory_pct=0.30 inventory_skew_factor=0.5
```

**Treatment:**
```bash
max_inventory_pct=0.35 inventory_skew_factor=0.4
```

**Hypothesis:** Higher max with lower skew = more volume but maintain risk control

**Measure:** Sharpe × Activity, outlier frequency

---

### Test #2: Spread Optimization
**Control:**
```bash
target_spread_bps=10 min_edge_bps=2
```

**Treatment:**
```bash
target_spread_bps=8 min_edge_bps=2.5
```

**Hypothesis:** Tighter spread but more selective = same volume, better quality

**Measure:** Fill rate, win rate, Sharpe

---

### Test #3: Signal Depth
**Control:**
```bash
imbalance_depths=1,3,5,10
```

**Treatment:**
```bash
imbalance_depths=3,5,10,15,20
```

**Hypothesis:** Deeper signals = lower variance, more consistency

**Measure:** Cross-book Sharpe variance, outlier penalties

---

## ⚡ Emergency Controls

### If Sharpe drops below 0.5 (losing money):
**STOP TRADING IMMEDIATELY**

```bash
# Set very tight controls
max_inventory_pct=0.10
min_edge_bps=5
target_spread_bps=20

# Or disable trading temporarily, investigate root cause
```

### If Outlier penalty > 0.15 (one book destroying score):
**Identify problem book, apply book-specific limits**

```python
# Future enhancement: Per-book parameter overrides
if book_id == problematic_book:
    max_inventory_pct = 0.10  # Very tight on this book
```

---

## 💡 Pro Tips

### Tip #1: Start Conservative, Scale Up
Begin with the "just starting" preset, monitor for 2-3 simulation runs, then gradually move toward "maximum reward" preset.

### Tip #2: Log Everything
Track metrics per-book:
- Sharpe ratio
- Fill rate
- Win rate
- Max inventory reached
- Number of outlier events

### Tip #3: Correlation is the Enemy
If books move together, your effective risk is higher than `max_inventory_pct` × N_books. Reduce limits accordingly.

### Tip #4: Response Time Matters
Every 100ms of response time costs ~10ms of execution delay. Optimize your setup:
- Enable `lazy_load=1`
- Use fast hardware
- Minimize logging in hot path

### Tip #5: Volume ≠ Reward
The scoring function is **multiplicative**. 2.0× activity with 0.5 Sharpe = score of 1.0. But 1.5× activity with 2.0 Sharpe = score of 3.0. **Always prioritize Sharpe.**

---

## 📞 Quick Decision Flowchart

```
Start
  │
  ├─ Score < 3.0?
  │   ├─ Sharpe < 1.5? → Reduce max_inventory_pct, increase min_edge_bps
  │   ├─ Activity < 1.3? → Increase base_order_size, reduce target_spread_bps
  │   └─ Outliers? → Tighten inventory_skew_factor globally
  │
  ├─ Score 3.0-4.5? (Good)
  │   └─ Slowly optimize toward "maximum reward" preset
  │
  └─ Score > 4.5? (Excellent)
      └─ Consider "top 1%" preset if comfortable with higher risk
```

---

## 📚 Further Reading

See `ORDER_BOOK_STRATEGY_GUIDE.md` for:
- Detailed academic justification
- Microstructure explanations
- Common failure modes
- Advanced tuning strategies

---

**Remember:** 
- Test changes one parameter at a time
- Monitor for 500+ simulation steps before judging
- Cross-book consistency beats single-book excellence
- Sharpe > Volume (always)

Good luck! 🚀
