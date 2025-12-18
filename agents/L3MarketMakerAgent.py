# SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT

import math
import traceback
from dataclasses import dataclass

import bittensor as bt

from taos.common.agents import launch
from taos.im.agents import FinanceSimulationAgent
from taos.im.protocol import MarketSimulationStateUpdate, FinanceAgentResponse
from taos.im.protocol.models import OrderDirection, STP, TimeInForce


@dataclass
class _BookState:
    base_target: float | None = None
    wealth0: float | None = None
    mid_last: float | None = None
    vol_ewma: float = 0.0
    cooldown_until: int = 0
    loss_cooldown_until: int = 0


class L3MarketMakerAgent(FinanceSimulationAgent):
    """Validator-aware L3 market-maker for sn-79.

    Reward-critical design:
    - Validator rewards **activity-weighted normalized Sharpe** of per-book marked-to-mid wealth deltas.
    - This agent aims for: low inventory variance (stable wealth), small positive drift (spread capture),
      and continuous trading activity (avoid activity-factor decay), while avoiding toxic intervals.
    """

    def initialize(self):
        # --- Core knobs (override via --params key=value) ---
        self.depth_levels: int = int(getattr(self.config, "depth_levels", 5))  # use top-N for imbalance

        # Quoting aggressiveness
        self.min_half_spread_ticks: int = int(getattr(self.config, "min_half_spread_ticks", 1))
        self.max_half_spread_ticks: int = int(getattr(self.config, "max_half_spread_ticks", 6))
        self.inside_tick_if_safe: int = int(getattr(self.config, "inside_tick_if_safe", 1))  # join+1tick when safe

        # Inventory control
        self.inv_cap_base: float = float(getattr(self.config, "inv_cap_base", 4.0))
        self.inv_soft_cap_base: float = float(getattr(self.config, "inv_soft_cap_base", 2.0))
        self.max_skew_ticks: int = int(getattr(self.config, "max_skew_ticks", 6))

        # Sizing
        self.min_order_size: float = float(getattr(self.config, "min_order_size", 0.2))
        self.max_order_size: float = float(getattr(self.config, "max_order_size", 2.5))
        self.size_frac_top: float = float(getattr(self.config, "size_frac_top", 0.015))

        # Order lifecycle
        self.max_order_age_mult: float = float(getattr(self.config, "max_order_age_mult", 2.2))
        self.reprice_ticks: int = int(getattr(self.config, "reprice_ticks", 1))

        # Toxicity filters (adverse selection avoidance)
        self.vol_ewma_alpha: float = float(getattr(self.config, "vol_ewma_alpha", 0.12))
        self.vol_toxic_threshold: float = float(getattr(self.config, "vol_toxic_threshold", 0.0015))  # ~15bp per step
        self.imb_toxic_threshold: float = float(getattr(self.config, "imb_toxic_threshold", 0.55))
        self.trade_imb_toxic_threshold: float = float(getattr(self.config, "trade_imb_toxic_threshold", 0.65))

        # Cooldowns
        self.cooldown_mult: float = float(getattr(self.config, "cooldown_mult", 1.5))
        self.loss_cooldown_mult: float = float(getattr(self.config, "loss_cooldown_mult", 6.0))

        # Loss control
        self.dd_stop_frac: float = float(getattr(self.config, "dd_stop_frac", 0.02))  # 2% of initial wealth per book
        self.hard_flatten_frac: float = float(getattr(self.config, "hard_flatten_frac", 0.80))  # flatten this fraction of excess

        # Internal
        self._books: dict[int, _BookState] = {}
        self._client_id: int = 10_000

    # ------------------------ helpers ------------------------
    def _tick(self, price_decimals: int) -> float:
        return 10 ** (-price_decimals)

    def _round_to_tick(self, x: float, tick: float, decimals: int) -> float:
        # tick-round first, then decimal-round for safety.
        return round(round(x / tick) * tick, decimals)

    def _clamp(self, x: float, lo: float, hi: float) -> float:
        return lo if x < lo else hi if x > hi else x

    def _best_levels(self, book) -> tuple[float | None, float | None, float, float]:
        if not book.bids or not book.asks:
            return None, None, 0.0, 0.0
        bid = book.bids[0].price
        ask = book.asks[0].price
        bidq = book.bids[0].quantity
        askq = book.asks[0].quantity
        return bid, ask, bidq, askq

    def _depth_imbalance(self, book, depth: int) -> float:
        bids = book.bids[:depth] if book.bids else []
        asks = book.asks[:depth] if book.asks else []
        bq = 0.0
        aq = 0.0
        for lvl in bids:
            bq += float(lvl.quantity)
        for lvl in asks:
            aq += float(lvl.quantity)
        denom = bq + aq
        if denom <= 0:
            return 0.0
        return (bq - aq) / denom

    def _trade_imbalance(self, book) -> float:
        # Signed taker-initiated flow: + buy-initiated, - sell-initiated.
        if not book.events:
            return 0.0
        buy = 0.0
        sell = 0.0
        for ev in book.events:
            # TradeInfo.type == 't'
            if getattr(ev, "type", None) == "t":
                if ev.side == 0:
                    buy += float(ev.quantity)
                else:
                    sell += float(ev.quantity)
        denom = buy + sell
        if denom <= 0:
            return 0.0
        return (buy - sell) / denom

    def _open_orders_by_side(self, account) -> tuple[list, list]:
        bids = []
        asks = []
        for o in account.orders:
            if o.price is None:
                continue
            if o.side == OrderDirection.BUY:
                bids.append(o)
            else:
                asks.append(o)
        return bids, asks

    def _queue_ahead(self, book, side: OrderDirection, price: float, my_order_id: int | None) -> float | None:
        # Uses detailed L3 (top config.detailedDepth levels). If level not detailed, return None.
        levels = book.bids if side == OrderDirection.BUY else book.asks
        # Simulator publishes L3 composition only for the top `detailedDepth` levels (default=5).
        for lvl in levels[:5]:
            if lvl.price != price:
                continue
            if not lvl.orders:
                return None
            if my_order_id is None:
                return float(sum(o.quantity for o in lvl.orders))
            ahead = 0.0
            for o in lvl.orders:
                if o.id == my_order_id:
                    break
                ahead += float(o.quantity)
            return float(ahead)
        return None

    # ------------------------ main strategy ------------------------
    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        response = FinanceAgentResponse(agent_id=self.uid)

        price_dec = state.config.priceDecimals
        vol_dec = state.config.volumeDecimals
        tick = self._tick(price_dec)
        publish_interval = int(state.config.publish_interval)

        # Global safety: if we ever need to dump risk quickly, do it deterministically and stop quoting.
        total_pnl = 0.0
        total_w0 = 0.0

        # First pass: initialize book state + compute global PnL
        for book_id, book in state.books.items():
            if book_id not in self._books:
                self._books[book_id] = _BookState()

            bs = self._books[book_id]
            account = self.accounts.get(book_id)
            if account is None:
                continue

            bid, ask, _, _ = self._best_levels(book)
            if bid is None or ask is None:
                continue
            mid = 0.5 * (bid + ask)

            if bs.base_target is None:
                bs.base_target = float(account.own_base)
            if bs.wealth0 is None:
                bs.wealth0 = float(account.own_quote) + float(account.own_base) * mid

            wealth = float(account.own_quote) + float(account.own_base) * mid
            pnl = wealth - float(bs.wealth0)
            total_pnl += pnl
            total_w0 += float(bs.wealth0)

        if total_w0 > 0 and total_pnl < -self.dd_stop_frac * total_w0:
            # Kill-switch: stop trading and cancel all open orders.
            for book_id, account in self.accounts.items():
                if account.orders:
                    response.cancel_orders(book_id, [o.id for o in account.orders if o.price is not None])
            return response

        # Second pass: per-book quoting
        for book_id, book in state.books.items():
            try:
                account = self.accounts.get(book_id)
                if account is None:
                    continue

                bs = self._books[book_id]
                now = int(state.timestamp)
                if now < bs.cooldown_until or now < bs.loss_cooldown_until:
                    # Risk-off window: keep cancellations only.
                    if account.orders:
                        response.cancel_orders(book_id, [o.id for o in account.orders if o.price is not None])
                    continue

                bid, ask, bidq0, askq0 = self._best_levels(book)
                if bid is None or ask is None:
                    continue

                spread = ask - bid
                if spread <= 0:
                    continue

                mid = 0.5 * (bid + ask)

                # EWMA of per-step mid moves (simple, fast, deterministic)
                if bs.mid_last is not None:
                    step_ret = abs(mid - bs.mid_last) / max(mid, 1e-12)
                    bs.vol_ewma = (1.0 - self.vol_ewma_alpha) * bs.vol_ewma + self.vol_ewma_alpha * step_ret
                bs.mid_last = mid

                depth = max(1, self.depth_levels)
                imb = self._depth_imbalance(book, depth)
                t_imb = self._trade_imbalance(book)

                # Toxicity gating: if conditions look like informed flow / volatility spike, widen or go one-sided.
                toxic = (bs.vol_ewma > self.vol_toxic_threshold) or (abs(imb) > self.imb_toxic_threshold and abs(t_imb) > self.trade_imb_toxic_threshold)
                if toxic:
                    bs.cooldown_until = now + int(self.cooldown_mult * publish_interval)

                # Inventory (keep base exposure near start to minimize mark-to-mid variance)
                base_target = float(bs.base_target or 0.0)
                inv = float(account.own_base) - base_target

                # Hard inventory cap -> immediate risk reduction.
                if abs(inv) > self.inv_cap_base:
                    # Cancel risk-increasing orders, and place a single aggressive IOC-ish limit at best to reduce exposure.
                    bids_open, asks_open = self._open_orders_by_side(account)
                    cancel_ids = []
                    if inv > 0:
                        cancel_ids.extend([o.id for o in bids_open])
                    else:
                        cancel_ids.extend([o.id for o in asks_open])
                    if cancel_ids:
                        response.cancel_orders(book_id, cancel_ids)

                    excess = abs(inv) - self.inv_soft_cap_base
                    if excess > 0:
                        qty = self._clamp(excess * self.hard_flatten_frac, self.min_order_size, self.max_order_size)
                        qty = round(qty, vol_dec)
                        if inv > 0:
                            # Sell at best bid to de-risk fast (still limit order; may execute immediately).
                            response.limit_order(
                                book_id=book_id,
                                direction=OrderDirection.SELL,
                                quantity=qty,
                                price=bid,
                                stp=STP.CANCEL_BOTH,
                                postOnly=False,
                                timeInForce=TimeInForce.IOC,
                            )
                        else:
                            response.limit_order(
                                book_id=book_id,
                                direction=OrderDirection.BUY,
                                quantity=qty,
                                price=ask,
                                stp=STP.CANCEL_BOTH,
                                postOnly=False,
                                timeInForce=TimeInForce.IOC,
                            )
                    # After forced de-risk, skip quoting this tick.
                    continue

                # Compute fair price (microprice-like tilt using imbalance + trade flow)
                # Keep tilt small vs spread to avoid trend-chasing inventory swings.
                micro = mid + 0.25 * imb * spread + 0.15 * t_imb * spread

                # Choose target half-spread in ticks.
                half_spread_ticks = int(round(0.5 * spread / tick))
                half_spread_ticks = self._clamp(half_spread_ticks, self.min_half_spread_ticks, self.max_half_spread_ticks)

                # Inventory skew: shift both quotes toward unwinding inventory.
                inv_norm = self._clamp(inv / max(self.inv_soft_cap_base, 1e-9), -1.0, 1.0)
                skew_ticks = int(round(inv_norm * self.max_skew_ticks))

                # Base quotes around micro
                bid_px = micro - half_spread_ticks * tick - skew_ticks * tick
                ask_px = micro + half_spread_ticks * tick - skew_ticks * tick

                # Clamp to not cross.
                bid_px = min(bid_px, ask - tick)
                ask_px = max(ask_px, bid + tick)

                # Improve fill probability in calm regimes: step inside by 1 tick if spread allows and not toxic.
                if not toxic and spread >= 3 * tick and self.inside_tick_if_safe:
                    # L3 queue-aware: if joining best means huge queue, step inside.
                    bids_open, asks_open = self._open_orders_by_side(account)
                    my_bid = bids_open[0] if bids_open else None
                    my_ask = asks_open[0] if asks_open else None

                    if bid_px <= bid:
                        qa = self._queue_ahead(book, OrderDirection.BUY, bid, my_bid.id if my_bid else None)
                        if qa is not None and (qa / max(float(bidq0), 1e-9)) > 0.70:
                            bid_px = bid + tick
                    if ask_px >= ask:
                        qa = self._queue_ahead(book, OrderDirection.SELL, ask, my_ask.id if my_ask else None)
                        if qa is not None and (qa / max(float(askq0), 1e-9)) > 0.70:
                            ask_px = ask - tick

                # One-sided quoting when flow is toxic: quote only on the "safer" side.
                quote_bid = True
                quote_ask = True
                if toxic:
                    if imb > 0:
                        quote_ask = False
                    elif imb < 0:
                        quote_bid = False

                # Size: proportional to top-of-book liquidity, bounded.
                top_liq = 0.5 * (float(bidq0) + float(askq0))
                qty = self._clamp(self.size_frac_top * top_liq, self.min_order_size, self.max_order_size)

                # Additional inventory-based sizing: reduce size when near soft cap.
                inv_abs = abs(inv)
                if inv_abs > 0.5 * self.inv_soft_cap_base:
                    shrink = self._clamp(1.0 - (inv_abs / max(self.inv_soft_cap_base, 1e-9)) * 0.7, 0.2, 1.0)
                    qty *= shrink

                qty = round(qty, vol_dec)

                # Round prices
                bid_px = self._round_to_tick(bid_px, tick, price_dec)
                ask_px = self._round_to_tick(ask_px, tick, price_dec)

                # Ensure non-cross postOnly constraints
                if bid_px >= ask:
                    bid_px = self._round_to_tick(ask - tick, tick, price_dec)
                if ask_px <= bid:
                    ask_px = self._round_to_tick(bid + tick, tick, price_dec)

                # Existing orders
                bids_open, asks_open = self._open_orders_by_side(account)
                best_bid_order = max(bids_open, key=lambda o: o.price) if bids_open else None
                best_ask_order = min(asks_open, key=lambda o: o.price) if asks_open else None

                # Cancel stale/misaligned orders (keep instruction count low)
                max_age = int(self.max_order_age_mult * publish_interval)
                cancel_ids: list[int] = []

                if best_bid_order is not None:
                    age = now - int(best_bid_order.timestamp)
                    if (not quote_bid) or age > max_age or abs(best_bid_order.price - bid_px) >= self.reprice_ticks * tick:
                        cancel_ids.append(best_bid_order.id)
                if best_ask_order is not None:
                    age = now - int(best_ask_order.timestamp)
                    if (not quote_ask) or age > max_age or abs(best_ask_order.price - ask_px) >= self.reprice_ticks * tick:
                        cancel_ids.append(best_ask_order.id)

                if cancel_ids:
                    response.cancel_orders(book_id, cancel_ids)

                expiry = int(2.2 * publish_interval)

                # Place new orders if needed.
                if quote_bid and qty >= self.min_order_size:
                    if best_bid_order is None or best_bid_order.id in cancel_ids:
                        self._client_id += 1
                        response.limit_order(
                            book_id=book_id,
                            direction=OrderDirection.BUY,
                            quantity=qty,
                            price=bid_px,
                            clientOrderId=self._client_id,
                            stp=STP.CANCEL_BOTH,
                            postOnly=True,
                            timeInForce=TimeInForce.GTT,
                            expiryPeriod=expiry,
                        )

                if quote_ask and qty >= self.min_order_size:
                    if best_ask_order is None or best_ask_order.id in cancel_ids:
                        self._client_id += 1
                        response.limit_order(
                            book_id=book_id,
                            direction=OrderDirection.SELL,
                            quantity=qty,
                            price=ask_px,
                            clientOrderId=self._client_id,
                            stp=STP.CANCEL_BOTH,
                            postOnly=True,
                            timeInForce=TimeInForce.GTT,
                            expiryPeriod=expiry,
                        )

                # Per-book loss cooldown: if PnL is meaningfully negative, back off to protect Sharpe.
                if bs.wealth0 is not None:
                    wealth = float(account.own_quote) + float(account.own_base) * mid
                    pnl = wealth - float(bs.wealth0)
                    if pnl < -self.dd_stop_frac * float(bs.wealth0):
                        bs.loss_cooldown_until = now + int(self.loss_cooldown_mult * publish_interval)
                        if account.orders:
                            response.cancel_orders(book_id, [o.id for o in account.orders if o.price is not None])

            except Exception as ex:
                bt.logging.error(
                    f"BOOK {book_id}: Exception while generating response at T={state.timestamp}: {ex}\n{traceback.format_exc()}"
                )

        return response


if __name__ == "__main__":
    """Local test example via proxy:

    python L3MarketMakerAgent.py --port 8888 --agent_id 0 --params \
        depth_levels=5 inv_cap_base=4 inv_soft_cap_base=2 min_order_size=0.2 max_order_size=2.5 \
        min_half_spread_ticks=1 max_half_spread_ticks=6 reprice_ticks=1 inside_tick_if_safe=1
    """

    launch(L3MarketMakerAgent)
