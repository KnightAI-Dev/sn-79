# SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""
EliteImbalanceMarketMaker

Design goal for sn-79:
- Maximize *stable* risk-adjusted performance (validator uses activity-weighted Sharpe of inventory-value deltas).
- Maintain consistent trading activity (activity factor decays when inactive).
- Avoid "one-book blowups" (validator penalizes left-tail outliers across books).

Core idea:
Two-sided market making with dynamic quote skew (order book imbalance + short-horizon trade pressure),
and aggressive inventory control + toxicity filters to avoid adverse selection in discrete-time updates.
"""

from __future__ import annotations

import math
import traceback
import bittensor as bt

from taos.common.agents import launch
from taos.im.agents import FinanceSimulationAgent
from taos.im.protocol import MarketSimulationStateUpdate, FinanceAgentResponse
from taos.im.protocol.instructions import (
    OrderDirection,
    STP,
    TimeInForce,
    LoanSettlementOption,
)


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


class EliteImbalanceMarketMaker(FinanceSimulationAgent):
    """
    Production-ready miner agent.

    Parameters (via --agent.params):
        min_qty (float): Minimum per-order BASE quantity.
        max_qty (float): Maximum per-order BASE quantity.
        target_notional (float): Target per-order notional in QUOTE (converted to BASE via midquote).
        quote_levels (int): How many levels (0..N) to aggregate for imbalance.
        min_halfspread_ticks (int): Minimum half-spread in ticks for quoting.
        cancel_tolerance_ticks (int): Requote only if our order drifts by this many ticks.
        expiry_ns (int): GTT expiry in simulation nanoseconds for our quotes.
        max_maker_fee_rate (float): If maker fee exceeds this, stop quoting (fees dominate spread capture).

        inv_target_frac (float): Target fraction of wealth in BASE value (usually 0.0).
        inv_limit_frac (float): Hard limit on abs(BASE-value / total-wealth); triggers de-risking.
        inv_skew_strength (float): How strongly to skew reservation price against inventory.

        imb_skew_strength (float): How strongly to skew quotes using L2 imbalance.
        flow_skew_strength (float): How strongly to skew quotes using trade imbalance / price move.

        toxic_move_threshold (float): If abs(interval_return) exceeds this, treat flow as toxic.
        toxic_flow_threshold (float): If abs(trade_imbalance / (trade_volume_base + eps)) exceeds this, treat as toxic.
        retreat_ticks (int): Extra ticks to widen on the toxic side (reduces adverse selection).
        emergency_unwind_frac (float): Fraction of inventory to unwind when beyond hard limit (uses market order).

    Notes:
    - Avoids randomness for reproducibility / stability.
    - Keeps computation O(depth) per book.
    """

    def initialize(self):
        # --- Sizing / activity ---
        self.min_qty: float = float(getattr(self.config, "min_qty", 0.05))
        self.max_qty: float = float(getattr(self.config, "max_qty", 1.00))
        self.target_notional: float = float(getattr(self.config, "target_notional", 250.0))

        # --- Book microstructure signals ---
        self.quote_levels: int | None = (
            int(getattr(self.config, "quote_levels", 10)) if hasattr(self.config, "quote_levels") else 10
        )
        self.imb_skew_strength: float = float(getattr(self.config, "imb_skew_strength", 0.60))
        self.flow_skew_strength: float = float(getattr(self.config, "flow_skew_strength", 0.35))

        # --- Quoting / order lifecycle ---
        self.min_halfspread_ticks: int = int(getattr(self.config, "min_halfspread_ticks", 2))
        self.cancel_tolerance_ticks: int = int(getattr(self.config, "cancel_tolerance_ticks", 2))
        self.expiry_ns: int | None = int(getattr(self.config, "expiry_ns", 0)) or None
        self.max_maker_fee_rate: float = float(getattr(self.config, "max_maker_fee_rate", 0.0015))

        # --- Inventory control (reward-critical) ---
        self.inv_target_frac: float = float(getattr(self.config, "inv_target_frac", 0.0))
        self.inv_limit_frac: float = float(getattr(self.config, "inv_limit_frac", 0.20))
        self.inv_skew_strength: float = float(getattr(self.config, "inv_skew_strength", 1.00))
        self.emergency_unwind_frac: float = float(getattr(self.config, "emergency_unwind_frac", 0.35))

        # --- Toxic flow filters ---
        self.toxic_move_threshold: float = float(getattr(self.config, "toxic_move_threshold", 0.006))
        self.toxic_flow_threshold: float = float(getattr(self.config, "toxic_flow_threshold", 0.55))
        self.retreat_ticks: int = int(getattr(self.config, "retreat_ticks", 3))

        # Per-validator/per-book last mid for simple realized return proxy
        self._last_mid: dict[str, dict[int, float]] = {}

    def _tick(self) -> float:
        return 10 ** (-self.simulation_config.priceDecimals)

    def _round_price(self, p: float) -> float:
        # Prices are rounded by simulator anyway; we round early to be deterministic.
        return round(p, self.simulation_config.priceDecimals)

    def _round_qty(self, q: float) -> float:
        return round(q, self.simulation_config.volumeDecimals)

    def _compute_mid_spread(self, book) -> tuple[float, float, float, float]:
        if not book.bids or not book.asks:
            return 0.0, 0.0, 0.0, 0.0
        best_bid = float(book.bids[0].price)
        best_ask = float(book.asks[0].price)
        mid = 0.5 * (best_bid + best_ask)
        spread = max(best_ask - best_bid, 0.0)
        return best_bid, best_ask, mid, spread

    def _l2_imbalance(self, book, timestamp: int) -> float:
        # Depth-limited L2 imbalance using current snapshot (fast).
        snap = book.snapshot(timestamp)
        depth = self.quote_levels if self.quote_levels and self.quote_levels > 0 else None
        try:
            return float(snap.imbalance(depth))
        except ZeroDivisionError:
            return 0.0

    def _interval_flow_signal(self, book, mid: float, validator: str, book_id: int) -> tuple[float, bool]:
        """
        Returns:
            flow_signal: signed value in [-1, 1] (positive => upward pressure)
            toxic: whether we should retreat from the toxic side
        """
        # No events => no flow signal.
        if not book.events:
            return 0.0, False

        # Price move proxy: last-mid vs previous update mid (per validator/book).
        last_mid = self._last_mid.get(validator, {}).get(book_id, 0.0)
        interval_ret = 0.0 if last_mid <= 0.0 else (mid / last_mid - 1.0)

        # Trade imbalance normalized by traded BASE quantity.
        trades = book.trades
        if not trades:
            return _clip(interval_ret / self.toxic_move_threshold, -1.0, 1.0), abs(interval_ret) > self.toxic_move_threshold

        buy_qty = 0.0
        sell_qty = 0.0
        for t in trades.values():
            if t.side == OrderDirection.BUY:
                buy_qty += t.quantity
            else:
                sell_qty += t.quantity
        tot_qty = buy_qty + sell_qty
        eps = 1e-12
        flow = (buy_qty - sell_qty) / (tot_qty + eps)

        # "Toxicity": big directional prints + price move in same direction.
        toxic_by_move = abs(interval_ret) > self.toxic_move_threshold
        toxic_by_flow = abs(flow) > self.toxic_flow_threshold
        toxic = toxic_by_move and toxic_by_flow and (math.copysign(1.0, interval_ret) == math.copysign(1.0, flow))

        # Combine (bounded): prefer flow when trades exist, else rely on return proxy.
        flow_signal = _clip(0.5 * flow + 0.5 * _clip(interval_ret / max(self.toxic_move_threshold, 1e-9), -1.0, 1.0), -1.0, 1.0)
        return flow_signal, toxic

    def _target_order_qty(self, mid: float) -> float:
        if mid <= 0:
            return self._round_qty(self.min_qty)
        q = self.target_notional / mid
        q = _clip(q, self.min_qty, self.max_qty)
        return self._round_qty(q)

    def _inventory_frac(self, account, mid: float) -> float:
        """
        Inventory fraction in [-1, 1] approx: (base_value / total_value) - target.
        Uses totals (including reserved) to keep risk consistent.
        """
        base = float(account.base_balance.total)
        quote = float(account.quote_balance.total)
        total_val = quote + base * mid
        if total_val <= 0.0 or mid <= 0.0:
            return 0.0
        frac = (base * mid) / total_val
        return frac

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        response = FinanceAgentResponse(agent_id=self.uid)
        validator = state.dendrite.hotkey

        if validator not in self._last_mid:
            self._last_mid[validator] = {}

        tick = self._tick()

        for book_id, book in state.books.items():
            try:
                if not book.bids or not book.asks:
                    continue

                account = self.accounts[book_id]
                best_bid, best_ask, mid, spread = self._compute_mid_spread(book)
                if mid <= 0.0:
                    continue

                # Fee sanity: if maker fee is too high, spread capture becomes negative EV.
                # (We still may want to cancel stale orders.)
                maker_fee = account.fees.maker_fee_rate if account.fees else 0.0
                fees_too_high = maker_fee > self.max_maker_fee_rate

                # Compute signals.
                imb = self._l2_imbalance(book, state.timestamp)  # [-1, 1]
                flow_signal, toxic = self._interval_flow_signal(book, mid, validator, book_id)  # [-1,1], bool

                # Inventory fraction and deviation from target.
                inv_frac = self._inventory_frac(account, mid)
                inv_dev = inv_frac - self.inv_target_frac

                # --- Build reservation price shift in *spread units* (robust across price scales) ---
                # Positive shift => quote higher (bullish); negative => quote lower.
                # Inventory pushes reservation *against* current inventory to mean-revert exposure.
                shift_units = (
                    self.imb_skew_strength * imb
                    + self.flow_skew_strength * flow_signal
                    - self.inv_skew_strength * _clip(inv_dev / max(self.inv_limit_frac, 1e-9), -1.0, 1.0)
                )
                shift_units = _clip(shift_units, -1.5, 1.5)

                # Base halfspread: at least a few ticks; widen when spread is already wide.
                # (Discrete-time updates => stale quotes are punished; we prefer fewer, safer fills.)
                min_hs = self.min_halfspread_ticks * tick
                halfspread = max(min_hs, 0.5 * spread)

                # Reservation price and desired quotes.
                reservation = mid + shift_units * halfspread

                desired_bid = reservation - halfspread
                desired_ask = reservation + halfspread

                # Force maker-side quotes (avoid accidental taker execution).
                desired_bid = min(desired_bid, best_ask - tick)
                desired_ask = max(desired_ask, best_bid + tick)

                # Toxic retreat: widen specifically on the side that gets picked off.
                # If flow is upward & toxic => retreat asks (avoid selling just before further up-move).
                # If flow is downward & toxic => retreat bids (avoid buying just before further down-move).
                if toxic:
                    if flow_signal > 0:
                        desired_ask += self.retreat_ticks * tick
                    elif flow_signal < 0:
                        desired_bid -= self.retreat_ticks * tick

                desired_bid = self._round_price(desired_bid)
                desired_ask = self._round_price(desired_ask)

                # If book is too tight for two-sided quoting, pick one side that reduces inventory risk.
                if desired_bid >= desired_ask:
                    if inv_dev > 0:  # long base => prefer selling
                        desired_bid = 0.0
                        desired_ask = self._round_price(max(best_ask, best_bid + tick))
                    elif inv_dev < 0:  # short base => prefer buying
                        desired_ask = 0.0
                        desired_bid = self._round_price(min(best_bid, best_ask - tick))
                    else:
                        continue

                # Decide target size, then skew size when inventory is near limits.
                base_qty = self._target_order_qty(mid)
                # Reduce size on the side that increases inventory when already near the hard limit.
                inv_pressure = _clip(abs(inv_dev) / max(self.inv_limit_frac, 1e-9), 0.0, 2.0)
                size_scale = _clip(1.0 - 0.5 * max(inv_pressure - 0.7, 0.0), 0.25, 1.0)

                bid_qty = self._round_qty(base_qty * (size_scale if inv_dev < 0 else 1.0))
                ask_qty = self._round_qty(base_qty * (size_scale if inv_dev > 0 else 1.0))

                # Order management: cancel stale/out-of-policy orders before placing new ones.
                open_orders = list(account.orders) if account.orders else []
                bid_orders = [o for o in open_orders if o.side == OrderDirection.BUY]
                ask_orders = [o for o in open_orders if o.side == OrderDirection.SELL]

                tol = self.cancel_tolerance_ticks * tick
                cancel_ids: list[int] = []

                # Cancel orders if fees too high (immediately stop donating edge).
                if fees_too_high:
                    cancel_ids = [o.id for o in open_orders]
                else:
                    # Keep at most one quote per side near desired price.
                    # Cancel anything far away or on the wrong side given inventory hard-limit.
                    for o in open_orders:
                        if o.price is None:
                            continue
                        if o.side == OrderDirection.BUY and desired_bid > 0:
                            if abs(o.price - desired_bid) > tol:
                                cancel_ids.append(o.id)
                        elif o.side == OrderDirection.SELL and desired_ask > 0:
                            if abs(o.price - desired_ask) > tol:
                                cancel_ids.append(o.id)
                        else:
                            cancel_ids.append(o.id)

                    # If we're beyond hard inventory limit, stop quoting the risky side entirely.
                    if abs(inv_dev) > self.inv_limit_frac:
                        if inv_dev > 0:  # too long base => cancel bids
                            cancel_ids.extend([o.id for o in bid_orders])
                            desired_bid = 0.0
                        else:  # too short base => cancel asks
                            cancel_ids.extend([o.id for o in ask_orders])
                            desired_ask = 0.0

                if cancel_ids:
                    # Deduplicate to avoid simulator errors.
                    response.cancel_orders(book_id=book_id, order_ids=sorted(set(cancel_ids)))

                # Emergency unwind: if beyond hard limit, reduce exposure immediately with a small market order.
                # This is *reward-critical* for avoiding outlier left-tail books.
                if abs(inv_dev) > self.inv_limit_frac and self.emergency_unwind_frac > 0:
                    base_total = float(account.base_balance.total)
                    unwind_qty = self._round_qty(abs(base_total) * self.emergency_unwind_frac)
                    if unwind_qty > 0:
                        if inv_dev > 0 and account.base_balance.free >= unwind_qty:
                            response.market_order(
                                book_id=book_id,
                                direction=OrderDirection.SELL,
                                quantity=unwind_qty,
                                stp=STP.CANCEL_BOTH,
                                leverage=0.0,
                                settlement_option=LoanSettlementOption.FIFO,
                            )
                        elif inv_dev < 0:
                            # To cover a short base exposure, buy using quote balance.
                            max_affordable = account.quote_balance.free / max(best_ask, tick)
                            buy_qty = self._round_qty(min(unwind_qty, max_affordable))
                            if buy_qty > 0:
                                response.market_order(
                                    book_id=book_id,
                                    direction=OrderDirection.BUY,
                                    quantity=buy_qty,
                                    stp=STP.CANCEL_BOTH,
                                    leverage=0.0,
                                    settlement_option=LoanSettlementOption.FIFO,
                                )

                # Place new quotes if allowed.
                expiry = self.expiry_ns
                if expiry is None:
                    # Default expiry: slightly less than publish interval to reduce stale-quote exposure.
                    expiry = int(0.9 * state.config.publish_interval)

                if not fees_too_high:
                    if desired_bid > 0 and bid_qty > 0:
                        if account.quote_balance.free >= bid_qty * desired_bid:
                            response.limit_order(
                                book_id=book_id,
                                direction=OrderDirection.BUY,
                                quantity=bid_qty,
                                price=desired_bid,
                                stp=STP.CANCEL_BOTH,
                                timeInForce=TimeInForce.GTT,
                                expiryPeriod=expiry,
                            )
                    if desired_ask > 0 and ask_qty > 0:
                        if account.base_balance.free >= ask_qty:
                            response.limit_order(
                                book_id=book_id,
                                direction=OrderDirection.SELL,
                                quantity=ask_qty,
                                price=desired_ask,
                                stp=STP.CANCEL_BOTH,
                                timeInForce=TimeInForce.GTT,
                                expiryPeriod=expiry,
                            )

                # Update last mid for next interval return proxy.
                self._last_mid[validator][book_id] = mid

            except Exception as ex:
                bt.logging.error(
                    f"VALI {validator} BOOK {book_id}: Exception in EliteImbalanceMarketMaker: {ex}\n{traceback.format_exc()}"
                )

        return response


if __name__ == "__main__":
    """
    Example command for local standalone testing execution using Proxy:
    python EliteImbalanceMarketMaker.py --port 8888 --agent_id 0 --params \
      min_qty=0.05 max_qty=1.0 target_notional=250 \
      quote_levels=10 min_halfspread_ticks=2 cancel_tolerance_ticks=2 expiry_ns=0 \
      max_maker_fee_rate=0.0015 \
      inv_target_frac=0.0 inv_limit_frac=0.20 inv_skew_strength=1.0 emergency_unwind_frac=0.35 \
      imb_skew_strength=0.6 flow_skew_strength=0.35 \
      toxic_move_threshold=0.006 toxic_flow_threshold=0.55 retreat_ticks=3
    """
    launch(EliteImbalanceMarketMaker)

