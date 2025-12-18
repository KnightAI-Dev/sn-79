# SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
import math
import traceback
import bittensor as bt

from taos.common.agents import launch
from taos.im.agents import FinanceSimulationAgent
from taos.im.protocol import MarketSimulationStateUpdate, FinanceAgentResponse
from taos.im.protocol.models import Book, TradeInfo
from taos.im.protocol.instructions import (
    OrderDirection,
    STP,
    TimeInForce,
)


class DominantImbalanceMM(FinanceSimulationAgent):
    """
    Reward-maximizing order book agent for sn-79.

    Design goals (reward-critical):
    - Stable intraday Sharpe on inventory value changes (primary score driver).
    - Sustained, not bursty, trading activity (volume multiplier + inactivity decay).
    - Minimal instruction count per book to reduce random per-instruction latency.
    - Deterministic decisions (no randomness) for validator stability.
    """

    def initialize(self):
        # Feature depth / smoothing
        self.imbalance_depth: int = int(getattr(self.config, "imbalance_depth", 10))
        self.vol_ewma_alpha: float = float(getattr(self.config, "vol_ewma_alpha", 0.05))

        # Quote placement controls
        self.min_halfspread_ticks: int = int(getattr(self.config, "min_halfspread_ticks", 1))
        self.vol_spread_coef: float = float(getattr(self.config, "vol_spread_coef", 2.0))

        # Signal -> price skew coefficients (multipliers on current spread)
        self.imbalance_coef: float = float(getattr(self.config, "imbalance_coef", 0.35))
        self.flow_coef: float = float(getattr(self.config, "flow_coef", 0.25))
        self.inventory_coef: float = float(getattr(self.config, "inventory_coef", 0.75))

        # Inventory & risk
        self.pos_limit_frac: float = float(getattr(self.config, "pos_limit_frac", 0.12))  # of miner_wealth
        self.inv_one_sided_threshold: float = float(getattr(self.config, "inv_one_sided_threshold", 0.65))
        self.max_maker_fee_rate: float = float(getattr(self.config, "max_maker_fee_rate", 0.004))

        # Order sizing
        self.min_qty: float = float(getattr(self.config, "min_qty", 0.05))
        self.max_qty: float = float(getattr(self.config, "max_qty", 2.0))
        self.qty_frac_of_max_pos: float = float(getattr(self.config, "qty_frac_of_max_pos", 0.06))
        self.toxic_qty_scale: float = float(getattr(self.config, "toxic_qty_scale", 0.7))

        # Time-in-force: keep orders ephemeral to avoid cancel spam.
        self.expiry_frac_of_publish_interval: float = float(
            getattr(self.config, "expiry_frac_of_publish_interval", 0.85)
        )
        self.expiry_min_ns: int = int(getattr(self.config, "expiry_min_ns", 50_000_000))  # 50ms

        # Toxicity controls
        self.informed_flow_threshold: float = float(getattr(self.config, "informed_flow_threshold", 1e-6))
        self.toxic_spread_add_ticks: int = int(getattr(self.config, "toxic_spread_add_ticks", 2))

        # Activity nudging (avoid inactivity decay): if no fills, temporarily tighten.
        self.no_fill_timeout_ns: int = int(getattr(self.config, "no_fill_timeout_ns", 120_000_000_000))  # 120s
        self.nudge_qty_frac: float = float(getattr(self.config, "nudge_qty_frac", 0.35))

        # Internal per-validator/book state
        self._last_mid: dict[str, dict[int, float]] = {}
        self._ewma_var: dict[str, dict[int, float]] = {}
        self._last_fill_ts: dict[str, dict[int, int]] = {}

    def onStart(self, event) -> None:
        # Conservative: reset all internal state at new simulation start.
        self._last_mid.clear()
        self._ewma_var.clear()
        self._last_fill_ts.clear()

    def onTrade(self, event) -> None:
        # Track own fill recency for activity nudging.
        try:
            book_id = getattr(event, "bookId", None)
            if book_id is None:
                return
            validator = getattr(self, "current_validator", None)
            if not validator:
                return
            if validator not in self._last_fill_ts:
                self._last_fill_ts[validator] = {}
            self._last_fill_ts[validator][int(book_id)] = int(event.timestamp)
        except Exception:
            # Never let event handling break respond()
            return

    @staticmethod
    def _clamp(x: float, lo: float, hi: float) -> float:
        return lo if x < lo else hi if x > hi else x

    def _book_imbalance(self, book: Book) -> float:
        # (bid - ask) / (bid + ask), using top N levels.
        depth = self.imbalance_depth
        bid_qty = 0.0
        ask_qty = 0.0
        for lvl in book.bids[:depth]:
            bid_qty += lvl.quantity
        for lvl in book.asks[:depth]:
            ask_qty += lvl.quantity
        denom = bid_qty + ask_qty
        if denom <= 0:
            return 0.0
        return (bid_qty - ask_qty) / denom

    def _trade_flow(self, book: Book) -> float:
        # Signed trade imbalance over last interval, normalized to [-1, 1].
        signed = 0.0
        total = 0.0
        if not book.events:
            return 0.0
        for ev in book.events:
            # TradeInfo.type == 't' per protocol.
            if getattr(ev, "type", None) != "t":
                continue
            t: TradeInfo = ev
            q = float(t.quantity)
            total += q
            signed += q if int(t.side) == int(OrderDirection.BUY) else -q
        if total <= 0:
            return 0.0
        return signed / total

    def _microprice(self, best_bid: float, best_ask: float, bid_qty: float, ask_qty: float) -> float:
        # Size-weighted top-of-book microprice. Shifted toward the side with less depth.
        denom = bid_qty + ask_qty
        if denom <= 0:
            return (best_bid + best_ask) / 2
        return (best_ask * bid_qty + best_bid * ask_qty) / denom

    def _update_vol(self, validator: str, book_id: int, mid: float) -> float:
        if validator not in self._last_mid:
            self._last_mid[validator] = {}
        if validator not in self._ewma_var:
            self._ewma_var[validator] = {}

        last_mid = self._last_mid[validator].get(book_id)
        if last_mid and last_mid > 0 and mid > 0:
            r = math.log(mid / last_mid)
            prev = self._ewma_var[validator].get(book_id, 0.0)
            a = self.vol_ewma_alpha
            self._ewma_var[validator][book_id] = (1 - a) * prev + a * (r * r)
        else:
            self._ewma_var[validator][book_id] = self._ewma_var[validator].get(book_id, 0.0)
        self._last_mid[validator][book_id] = mid
        return math.sqrt(max(self._ewma_var[validator][book_id], 0.0))

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        response = FinanceAgentResponse(agent_id=self.uid)
        validator = state.dendrite.hotkey
        # Used by onTrade() to attribute fills to the current validator session.
        self.current_validator = validator

        tick = 10 ** (-state.config.priceDecimals)
        expiry_ns = max(
            int(self.expiry_frac_of_publish_interval * state.config.publish_interval),
            self.expiry_min_ns,
        )

        for book_id, book in state.books.items():
            try:
                if not book.bids or not book.asks:
                    continue

                account = self.accounts[book_id]
                if account.fees and account.fees.maker_fee_rate > self.max_maker_fee_rate:
                    # High maker fees are poison to stable Sharpe; skip quoting until it normalizes.
                    continue

                best_bid = float(book.bids[0].price)
                best_ask = float(book.asks[0].price)
                if best_ask <= best_bid:
                    continue

                spread = best_ask - best_bid
                mid = (best_bid + best_ask) / 2.0

                # Previous mid for return/toxicity signals (must be read before vol update).
                prev_mid = self._last_mid.get(validator, {}).get(int(book_id))

                # Risk limits (in base units), based on miner_wealth and current mid.
                max_base_pos = (self.pos_limit_frac * float(state.config.miner_wealth)) / max(mid, tick)
                max_base_pos = max(max_base_pos, self.min_qty)

                net_base = float(account.base_balance.total) - float(account.base_loan) + float(account.base_collateral)
                inv_ratio = self._clamp(net_base / max_base_pos, -2.0, 2.0)

                # Features
                imb = self._book_imbalance(book)  # [-1, 1]
                flow = self._trade_flow(book)     # [-1, 1]
                sigma = self._update_vol(validator, int(book_id), mid)  # EWMA vol of log-mid

                # Detect "informed" flow proxy: flow aligned with recent mid move increases adverse selection.
                informed = 0.0
                if prev_mid and prev_mid > 0:
                    informed = flow * math.log(mid / prev_mid)  # positive => flow aligned with move

                bq = float(book.bids[0].quantity)
                aq = float(book.asks[0].quantity)
                micro = self._microprice(best_bid, best_ask, bq, aq)

                # Reservation price: skew by imbalance/flow, mean-revert inventory risk via inventory skew.
                r = micro
                r += (self.imbalance_coef * imb + self.flow_coef * flow - self.inventory_coef * inv_ratio) * spread

                # Spread control: widen when vol or informed flow rises; keep a minimum to stay maker.
                half = max(self.min_halfspread_ticks * tick, 0.5 * spread)
                half += self.vol_spread_coef * sigma * mid
                if informed > self.informed_flow_threshold:
                    half += self.toxic_spread_add_ticks * tick

                # Activity nudge: if no recent fills, temporarily tighten and small-size to avoid inactivity decay.
                last_fill = self._last_fill_ts.get(validator, {}).get(int(book_id), 0)
                if last_fill > 0:
                    needs_nudge = (int(state.timestamp) - int(last_fill) > self.no_fill_timeout_ns)
                else:
                    needs_nudge = int(state.timestamp) > self.no_fill_timeout_ns

                # If we're simply inactive (not toxic) and fairly inventory-neutral, temporarily tighten to regain fills.
                if needs_nudge and informed <= self.informed_flow_threshold and abs(inv_ratio) < self.inv_one_sided_threshold:
                    half = max(self.min_halfspread_ticks * tick, 0.5 * spread)

                # Base sizing (in base units)
                qty = self.qty_frac_of_max_pos * max_base_pos
                qty = self._clamp(qty, self.min_qty, self.max_qty)
                if informed > self.informed_flow_threshold:
                    qty *= (1.0 - self.toxic_qty_scale)
                if needs_nudge:
                    qty = max(self.min_qty, qty * self.nudge_qty_frac)

                qty = round(qty, state.config.volumeDecimals)
                if qty <= 0:
                    continue

                # Emergency: if near max_open_orders, clear existing orders with ONE cancel instruction.
                if state.config.max_open_orders and len(account.orders) >= int(state.config.max_open_orders) - 1:
                    response.cancel_orders(book_id=int(book_id), order_ids=[int(o.id) for o in account.orders])
                    continue

                # Ensure maker-only prices (avoid crossing). Allow improving inside spread when possible.
                max_maker_bid = best_ask - tick
                min_maker_ask = best_bid + tick

                desired_bid = r - half
                desired_ask = r + half

                bid_px = min(desired_bid, max_maker_bid)
                ask_px = max(desired_ask, min_maker_ask)
                bid_px = round(bid_px, state.config.priceDecimals)
                ask_px = round(ask_px, state.config.priceDecimals)

                if bid_px >= ask_px:
                    # Numerical safety: widen one tick.
                    bid_px = round(min(best_bid, ask_px - tick), state.config.priceDecimals)
                    ask_px = round(max(best_ask, bid_px + tick), state.config.priceDecimals)
                    if bid_px >= ask_px:
                        continue

                # One-sided quoting when inventory is too directional or flow is toxic.
                one_sided = abs(inv_ratio) >= self.inv_one_sided_threshold or informed > self.informed_flow_threshold

                # If long base, prefer selling; if short base, prefer buying.
                prefer_sell = inv_ratio > 0.0
                prefer_buy = inv_ratio < 0.0

                if one_sided:
                    if prefer_sell:
                        response.limit_order(
                            book_id=int(book_id),
                            direction=OrderDirection.SELL,
                            quantity=qty,
                            price=ask_px,
                            stp=STP.CANCEL_BOTH,
                            timeInForce=TimeInForce.GTT,
                            expiryPeriod=expiry_ns,
                        )
                    elif prefer_buy:
                        response.limit_order(
                            book_id=int(book_id),
                            direction=OrderDirection.BUY,
                            quantity=qty,
                            price=bid_px,
                            stp=STP.CANCEL_BOTH,
                            timeInForce=TimeInForce.GTT,
                            expiryPeriod=expiry_ns,
                        )
                    else:
                        # Flat but toxic: quote only with-flow side to reduce adverse selection.
                        if flow >= 0:
                            response.limit_order(
                                book_id=int(book_id),
                                direction=OrderDirection.BUY,
                                quantity=qty,
                                price=bid_px,
                                stp=STP.CANCEL_BOTH,
                                timeInForce=TimeInForce.GTT,
                                expiryPeriod=expiry_ns,
                            )
                        else:
                            response.limit_order(
                                book_id=int(book_id),
                                direction=OrderDirection.SELL,
                                quantity=qty,
                                price=ask_px,
                                stp=STP.CANCEL_BOTH,
                                timeInForce=TimeInForce.GTT,
                                expiryPeriod=expiry_ns,
                            )
                else:
                    # Two-sided: stable spread capture + frequent small fills.
                    response.limit_order(
                        book_id=int(book_id),
                        direction=OrderDirection.BUY,
                        quantity=qty,
                        price=bid_px,
                        stp=STP.CANCEL_BOTH,
                        timeInForce=TimeInForce.GTT,
                        expiryPeriod=expiry_ns,
                    )
                    response.limit_order(
                        book_id=int(book_id),
                        direction=OrderDirection.SELL,
                        quantity=qty,
                        price=ask_px,
                        stp=STP.CANCEL_BOTH,
                        timeInForce=TimeInForce.GTT,
                        expiryPeriod=expiry_ns,
                    )

            except Exception as ex:
                bt.logging.error(
                    f"VALI {validator} BOOK {book_id}: Exception in DominantImbalanceMM: {ex}\n"
                    f"{traceback.format_exc()}"
                )
                continue

        return response


if __name__ == "__main__":
    """
    Example command for local standalone testing execution using Proxy:
    python DominantImbalanceMM.py --port 8888 --agent_id 0 --params imbalance_depth=10 pos_limit_frac=0.12 expiry_frac_of_publish_interval=0.85
    """
    launch(DominantImbalanceMM)

