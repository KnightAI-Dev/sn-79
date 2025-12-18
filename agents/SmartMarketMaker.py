# SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT

import time
import traceback
import numpy as np
import bittensor as bt
from typing import List, Dict

from taos.common.agents import launch
from taos.im.utils import duration_from_timestamp
from taos.im.agents import FinanceSimulationAgent, StateHistoryManager
from taos.im.protocol.models import *
from taos.im.protocol.instructions import *
from taos.im.protocol import MarketSimulationStateUpdate, FinanceAgentResponse

class SmartMarketMaker(FinanceSimulationAgent):
    """
    An advanced market making agent that combines:
    1. Inventory Management (Avellaneda-Stoikov style skew)
    2. Order Book Imbalance (OBI) for fair price adjustment
    3. Volatility-based spread scaling
    4. Laddered quotes to capture volatility
    """
    def initialize(self):
        """
        Initializes the agent with advanced market making parameters.
        """
        # Risk & Strategy Parameters
        self.risk_aversion = float(getattr(self.config, 'risk_aversion', 0.5))       # Gamma: Inventory risk aversion
        self.obi_factor = float(getattr(self.config, 'obi_factor', 0.3))             # Alpha: OBI impact on fair price
        self.base_spread_bps = float(getattr(self.config, 'base_spread_bps', 20.0))  # Base spread in basis points
        self.vol_window = int(getattr(self.config, 'vol_window', 20))                # Lookback for volatility
        self.levels = int(getattr(self.config, 'levels', 5))                         # Number of ladder levels
        self.level_spacing_bps = float(getattr(self.config, 'level_spacing_bps', 5.0)) # Spacing between levels
        self.order_size_pct = float(getattr(self.config, 'order_size_pct', 0.01))    # % of equity per order
        
        # System Parameters
        self.expiry_period = int(getattr(self.config, 'expiry_period', 10_000_000_000)) # 10s default
        self.parallel_history_workers = int(getattr(self.config, 'parallel_history_workers', 0))

        # State History Manager for Volatility/Signals
        self.history_manager = StateHistoryManager(
            history_retention_mins=getattr(self.config, 'history_retention_mins', 60),
            log_dir=self.log_dir,
            parallel_workers=self.parallel_history_workers
        )

        bt.logging.info(f"SmartMarketMaker Initialized | Risk Aversion: {self.risk_aversion} | OBI Factor: {self.obi_factor}")

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        # Wait for history update to ensure consistency
        while self.history_manager.updating:
            time.sleep(0.01)

        response = FinanceAgentResponse(agent_id=self.uid)
        validator = state.dendrite.hotkey

        # Iterate over all books
        for book_id, book in state.books.items():
            try:
                if not book.bids or not book.asks:
                    continue

                # --- 1. Market Data Extraction ---
                best_bid = book.bids[0]
                best_ask = book.asks[0]
                mid_price = (best_bid.price + best_ask.price) / 2.0
                
                # --- 2. Volatility Calculation ---
                # Use history if available to calculate recent volatility
                vol = 0.001 # Default fallback
                recent_prices = []
                
                if validator in self.history_manager and book_id in self.history_manager[validator]:
                    hist = self.history_manager[validator][book_id]
                    # Get last N snapshots
                    sorted_times = sorted(hist.snapshots.keys())[-self.vol_window:]
                    for t in sorted_times:
                        s = hist.snapshots[t]
                        if s.bids and s.asks:
                            recent_prices.append((s.bids[0].price + s.asks[0].price) / 2.0)
                
                # Add current
                recent_prices.append(mid_price)

                if len(recent_prices) > 2:
                    prices = np.array(recent_prices)
                    returns = np.diff(prices) / prices[:-1]
                    vol = np.std(returns)
                    vol = max(vol, 1e-5) # Floor to avoid zero division/singularities
                
                # --- 3. Order Book Imbalance (OBI) ---
                # Calculate depth-weighted OBI
                # Simple version: Sum of top 5 levels
                bid_vol = sum([l.quantity for l in book.bids[:5]])
                ask_vol = sum([l.quantity for l in book.asks[:5]])
                total_vol = bid_vol + ask_vol
                obi = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0
                
                # --- 4. Inventory Position ---
                account = state.accounts[self.uid][book_id]
                base_inv = account.base_balance.total
                
                # Calculate total equity in Quote approx
                equity = account.quote_balance.total + (base_inv * mid_price)
                if equity <= 0: equity = 1000.0 # Fallback to avoid div zero if busted
                
                # Normalized inventory (fraction of equity in base)
                # target is 0 base (market neutral)
                # q_norm = (base_inv * mid_price) / equity
                # But standard A-S uses raw units scaled by gamma. 
                # Let's use raw units but scale gamma by price/vol to make it dimensionless-ish.
                
                # --- 5. Pricing Logic ---
                
                # Base Spread (volatility adjusted)
                # Vol multiplier: if vol is 1% (0.01), we want spread to widen.
                # Let's say at 0 vol, we use base_spread.
                # At high vol, we multiply.
                spread = mid_price * (self.base_spread_bps / 10000.0) * (1 + (vol * 100.0))
                
                # Fair Price (OBI adjusted)
                # Shifts the center of our quote
                fair_price = mid_price + (obi * self.obi_factor * spread)
                
                # Inventory Skew (Inventory risk adjustment)
                # If long (base_inv > 0), skew is negative (lower quotes to sell)
                # Skew proportional to spread is robust
                # If inventory is large, skew dominates
                skew = -self.risk_aversion * base_inv * (spread / 10.0) # Heuristic scaling
                
                final_center = fair_price + skew
                
                # --- 6. Execution (Ladder) ---
                
                # Cancel existing orders to refresh
                if account.orders:
                    response.cancel_orders(book_id=book_id, order_ids=[o.id for o in account.orders])
                
                # Calculate quantity per order
                # Target: 1% of equity per ladder level? Or total?
                # Let's do small size to be safe.
                qty_per_order = (equity * self.order_size_pct) / mid_price
                qty_per_order = max(qty_per_order, 1.0 / (10**state.config.volumeDecimals)) # Min size
                
                for i in range(self.levels):
                    # Widen spread for deeper levels
                    level_spread = spread * (1 + (i * self.level_spacing_bps / 10.0))
                    half_spread = level_spread / 2.0
                    
                    bid_p = final_center - half_spread
                    ask_p = final_center + half_spread
                    
                    # Rounding
                    bid_p = round(bid_p, state.config.priceDecimals)
                    ask_p = round(ask_p, state.config.priceDecimals)
                    qty = round(qty_per_order, state.config.volumeDecimals)
                    
                    if qty <= 0: continue

                    # Safety: Ensure we don't cross mid too aggressively or invert
                    # (Though crossing mid with postOnly=True will just reject, which is fine)
                    if bid_p >= ask_p:
                        continue
                        
                    # Submit Bid
                    response.limit_order(
                        book_id=book_id,
                        direction=OrderDirection.BUY,
                        quantity=qty,
                        price=bid_p,
                        timeInForce=TimeInForce.GTT,
                        expiryPeriod=self.expiry_period,
                        postOnly=True
                    )
                    
                    # Submit Ask
                    response.limit_order(
                        book_id=book_id,
                        direction=OrderDirection.SELL,
                        quantity=qty,
                        price=ask_p,
                        timeInForce=TimeInForce.GTT,
                        expiryPeriod=self.expiry_period,
                        postOnly=True
                    )

            except Exception as ex:
                bt.logging.error(f"VALI {validator} BOOK {book_id} : Exception in SmartMarketMaker: {ex}")
                bt.logging.debug(traceback.format_exc())

        # Update history
        self.history_manager.update_async(state.model_copy(deep=True))
        return response

if __name__ == "__main__":
    launch(SmartMarketMaker)
