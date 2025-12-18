# Quick Reference Card - Adaptive Market Maker Agent

## 📁 Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `ORDER_BOOK_STRATEGIES.md` | 615 | Strategy research & decision framework |
| `agents/AdaptiveMarketMakerAgent.py` | 515 | Agent implementation (production code) |
| `agents/ADAPTIVE_MARKET_MAKER_GUIDE.md` | 593 | Parameter guide & troubleshooting |
| `ORDER_BOOK_AGENT_README.md` | 523 | Main documentation & quick start |
| `EXECUTIVE_SUMMARY.md` | 471 | Executive summary & performance analysis |
| **TOTAL** | **2,717** | **Complete solution** |

---

## 🚀 Deploy in 3 Commands

### Testnet
```bash
cd /workspace
./run_miner.sh -e finney -u 366 -w testnet -h test_hotkey \
    -n AdaptiveMarketMakerAgent \
    -m "base_spread_bps=10.0 base_order_size=0.5 max_inventory=5.0"
```

### Mainnet
```bash
cd /workspace
./run_miner.sh -e finney -u 79 -w taos -h miner \
    -n AdaptiveMarketMakerAgent \
    -m "base_spread_bps=10.0 base_order_size=0.5"
```

---

## 🎯 Key Parameters (Quick Tune)

| Parameter | Default | Conservative | Aggressive | Effect |
|-----------|---------|--------------|------------|--------|
| `base_spread_bps` | 10.0 | 12.0 | 8.0 | Profit per trade vs fill rate |
| `base_order_size` | 0.5 | 0.3 | 1.0 | Volume generation |
| `max_inventory` | 5.0 | 3.0 | 8.0 | Risk tolerance |
| `risk_aversion` | 0.5 | 0.6 | 0.4 | Spread widening in volatility |
| `imbalance_threshold` | 0.30 | 0.35 | 0.25 | Signal sensitivity |

**Quick Fix Low Volume**: Reduce `base_spread_bps` to 8.0
**Quick Fix Low Sharpe**: Increase `risk_aversion` to 0.6, reduce `max_inventory` to 3.0

---

## 📊 Performance Targets

| Metric | Target | Alert If |
|--------|--------|----------|
| Sharpe (per book) | 0.5-0.8 | < 0.3 (any book) |
| Activity Factor | 1.4-1.8 | < 1.2 or > 1.9 |
| Response Time | < 0.5s | > 1.0s |
| Fill Rate | 40-60% | < 20% or > 80% |
| **Final Score** | **0.70-1.00** | **< 0.60** |

**Top 10% threshold**: Score > 0.95

---

## 🔬 Strategy Core (3 Components)

1. **Market Making** (80% of logic)
   - Place limit orders both sides
   - Capture spread on fills
   - Volume generation

2. **Inventory Control** (15% of logic)
   - Track net position
   - Skew quotes when imbalanced
   - Rebalance if extreme

3. **Risk Management** (5% of logic)
   - Volatility-adaptive spreads
   - Position limits
   - Volume caps

---

## 📚 Documentation Map

**New to sn-79?**
→ Start with `ORDER_BOOK_AGENT_README.md`

**Want to understand strategies?**
→ Read `ORDER_BOOK_STRATEGIES.md`

**Ready to deploy?**
→ Use `agents/ADAPTIVE_MARKET_MAKER_GUIDE.md`

**Need quick overview?**
→ Read `EXECUTIVE_SUMMARY.md`

**Troubleshooting?**
→ See `agents/ADAPTIVE_MARKET_MAKER_GUIDE.md` (Troubleshooting section)

---

## 🆘 Quick Troubleshooting

### Problem: Low volume (activity_factor < 1.2)
```bash
# Solution: Tighter spreads + larger sizes
--params base_spread_bps=8.0 base_order_size=0.8
```

### Problem: Low Sharpe (< 0.4)
```bash
# Solution: Wider spreads + tighter inventory control
--params base_spread_bps=12.0 max_inventory=3.0 risk_aversion=0.6
```

### Problem: Outlier books
```bash
# Solution: Stronger inventory control
--params inventory_skew_factor=0.6 max_inventory=4.0
```

### Problem: Slow response (> 1s)
```bash
# Solution: Enable lazy loading
--params lazy_load=1
```

---

## 🎓 Academic References (Top 3)

1. **Avellaneda & Stoikov (2008)** - "High-frequency trading in a limit order book"
   - Foundation for market making with inventory control

2. **Cont, Kukanov & Stoikov (2014)** - "The price impact of order book events"
   - Order book imbalance signals

3. **Guéant et al. (2013)** - "Dealing with the inventory risk"
   - Modern inventory management techniques

---

## 💰 Expected ROI Timeline

| Week | Activity | Expected Score | Percentile |
|------|----------|----------------|------------|
| 1 | Deploy + monitor | 0.80-0.86 | ~30% |
| 2 | Initial tuning | 0.86-0.92 | ~20% |
| 3 | Optimization | 0.92-0.98 | ~15% |
| 4+ | Fine-tuning | 0.98-1.05 | ~10% |

**Break-even**: Week 1 (competitive baseline)
**Target performance**: Week 3-4 (top 10-15%)

---

## 🔗 Quick Links

- **Dashboard**: https://taos.simulate.trading
- **Discord**: https://discord.com/channels/799672011265015819/1353733356470276096
- **GitHub**: https://github.com/taos-im/sn-79
- **Whitepaper**: https://simulate.trading/taos-im-paper

---

## ✅ Pre-Flight Checklist

Before deployment:
- [ ] Agent file in `~/.taos/agents/` or `/workspace/agents/`
- [ ] Wallet registered on subnet (testnet 366 or mainnet 79)
- [ ] Sufficient TAO for registration
- [ ] Understanding of basic parameters
- [ ] Monitoring plan (Discord alerts, dashboard checks)

After deployment:
- [ ] Check response time (< 1s)
- [ ] Verify orders being placed (check logs)
- [ ] Monitor Sharpe ratios per book
- [ ] Track activity factors
- [ ] Watch for outlier books

---

*Last Updated: December 2025*
*Version: 1.0.0*
