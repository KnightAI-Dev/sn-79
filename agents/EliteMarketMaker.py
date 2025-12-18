# SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT

import time
import numpy as np
import bittensor as bt
from collections import deque

from taos.common.agents import launch
from taos.im.agents import FinanceSimulationAgent
from taos.im.protocol.models import OrderDirection, TimeInForce, STP
from taos.im.protocol import MarketSimulationStateUpdate, FinanceAgentResponse

class EliteMarketMaker(FinanceSimulationAgent):
    """
    Reward-Maximizing Order Book Trading Agent for sn-79.
    
    Strategy: Hybrid Inventory-Skewed Order Book Imbalance (OBI) Market Maker.
    
    Phases:
    1. Analysis: Calculates Order Book Imbalance (OBI) and Volatility.
    2. Inventory Management: Adjusts reservation price based on current inventory risk (Avellaneda-Stoikov inspired).
    3. Signal Combination: Combines OBI and Inventory Skew to determine 'Fair Value'.
    4. Execution: Places limit orders around Fair Value, managing spread width dynamically.
    
    Key Features:
    - OBI Alpha: Front-runs order flow pressure.
    - Inventory Risk Aversion: Skews quotes to mean-revert inventory to zero.
    - Volatility-Adaptive Spreads: Widens spreads in high volatility to avoid adverse selection.
    - Toxic Flow Avoidance: Backs off (widens spread) when market moves fast.
    """

    def initialize(self):
        """
        Initialize agent parameters and state trackers.
        """
        # --- Strategy Parameters (Tunable) ---
        # Risk Aversion (gamma): Higher = stronger desire to be flat inventory.
        self.risk_aversion = float(getattr(self.config, 'risk_aversion', 0.1))
        
        # OBI Weight (alpha): Higher = stronger price adjustment to imbalance.
        self.obi_weight = float(getattr(self.config, 'obi_weight', 0.05))
        
        # Base Spread (in basis points): Minimum spread to capture.
        self.base_spread_bps = float(getattr(self.config, 'base_spread_bps', 10.0))
        
        # Volatility Multiplier: How much to widen spread per unit of volatility.
        self.vol_multiplier = float(getattr(self.config, 'vol_multiplier', 2.0))
        
        # Max Inventory (Base): Absolute limit before forced reduction.
        self.max_inventory = float(getattr(self.config, 'max_inventory', 10.0))
        
        # Order Size: Standard clip size.
        self.order_quantity = float(getattr(self.config, 'order_quantity', 1.0))
        
        # Expiry: Time in nanoseconds for orders to live (2 seconds default).
        self.expiry_period = int(getattr(self.config, 'expiry_period', 2_000_000_000))

        # --- Internal State ---
        # Track price history for volatility calculation: {book_id: deque(maxlen=20)}
        self.price_history = {}
        self.history_len = 20

    def get_mid_price(self, book):
        best_bid = book.bids[0].price if book.bids else None
        best_ask = book.asks[0].price if book.asks else None
        
        if best_bid is None or best_ask is None:
            return None
        return (best_bid + best_ask) / 2.0

    def calculate_obi(self, book, depth=5):
        """
        Calculate Order Book Imbalance (OBI) at top `depth` levels.
        OBI = (BidVol - AskVol) / (BidVol + AskVol)
        Range: [-1, 1]. Positive = Buy Pressure.
        """
        bid_vol = sum(level.quantity for level in book.bids[:depth])
        ask_vol = sum(level.quantity for level in book.asks[:depth])
        
        total_vol = bid_vol + ask_vol
        if total_vol == 0:
            return 0.0
            
        return (bid_vol - ask_vol) / total_vol

    def calculate_volatility(self, book_id, current_price):
        """
        Calculate simple realized volatility (standard deviation of returns).
        """
        if book_id not in self.price_history:
            self.price_history[book_id] = deque(maxlen=self.history_len)
        
        self.price_history[book_id].append(current_price)
        
        if len(self.price_history[book_id]) < 5:
            return 0.0
            
        prices = list(self.price_history[book_id])
        returns = np.diff(prices) / prices[:-1]
        return np.std(returns)

    def get_inventory(self, state, book_id):
        """
        Get current inventory (Base Asset) for this book.
        """
        if not state.accounts:
            return 0.0
        
        # Check if agent has account for this book
        agent_accounts = state.accounts.get(self.uid, {})
        book_account = agent_accounts.get(book_id)
        
        if book_account:
            return book_account.base_balance.total
        return 0.0

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        """
        Main trading loop.
        """
        response = FinanceAgentResponse(agent_id=self.uid)
        
        # Cancel all existing orders first (simple re-quoting strategy)
        # In production, modifying orders is more efficient, but verify if modify is supported.
        # The API supports cancel_orders. We will cancel all and replace to ensure fresh quotes.
        # Ideally, we only cancel if we need to move the quote significantly.
        # For this implementation, we re-quote every step to capture latest alpha.
        
        # Get active orders to cancel
        if state.accounts and self.uid in state.accounts:
             for book_id, account in state.accounts[self.uid].items():
                if account.orders:
                    order_ids = [o.id for o in account.orders]
                    if order_ids:
                        response.cancel_orders(book_id=book_id, order_ids=order_ids)

        for book_id, book in state.books.items():
            # 1. Market Data Analysis
            mid_price = self.get_mid_price(book)
            if mid_price is None:
                continue # Book is empty, skip

            obi = self.calculate_obi(book)
            volatility = self.calculate_volatility(book_id, mid_price)
            inventory = self.get_inventory(state, book_id)

            # 2. Fair Value Calculation (Microstructure Alpha)
            # Reservation Price = MidPrice - (Inventory * RiskAversion * Volatility^2)
            # Note: We simplify the term (T-t) to just a constant scaling factor effectively absorbed in risk_aversion
            
            # Inventory Skew:
            # If long (inv > 0), skew negative (lower price) to sell.
            # If short (inv < 0), skew positive (raise price) to buy.
            # We normalize inventory by order_quantity to keep skew proportional.
            q = inventory / self.order_quantity 
            inv_skew_bps = -1 * q * self.risk_aversion
            
            # OBI Skew:
            # If OBI > 0 (Buy Pressure), skew positive (raise price) to front-run/join.
            obi_skew_bps = obi * self.obi_weight
            
            # Combined Skew (in basis points)
            total_skew_bps = inv_skew_bps + obi_skew_bps
            
            # Calculate Fair Price
            fair_price = mid_price * (1 + total_skew_bps / 10000.0)
            
            # 3. Spread Management
            # Wider spread when volatility is high
            current_spread_bps = self.base_spread_bps + (volatility * 10000 * self.vol_multiplier)
            half_spread_bps = current_spread_bps / 2.0
            
            # Calculate Quotes
            my_bid = fair_price * (1 - half_spread_bps / 10000.0)
            my_ask = fair_price * (1 + half_spread_bps / 10000.0)
            
            # Rounding to tick size (using priceDecimals from config)
            decimals = state.config.priceDecimals
            my_bid = round(my_bid, decimals)
            my_ask = round(my_ask, decimals)
            
            # 4. Risk & Sanity Checks
            
            # Ensure we don't cross the book aggressively unless intended (Post-Only logic manually)
            # If our skew makes us cross the spread, we might want to be a taker if alpha is strong enough.
            # But for stability, we usually clamp to best bid/ask unless OBI is extreme.
            
            best_bid = book.bids[0].price
            best_ask = book.asks[0].price
            
            # Clamp to micro-penny inside spread if not crossing
            # If my_bid >= best_ask, we are taking liquidity.
            # Only take if OBI is very strong (> 0.8) or inventory is critical.
            
            is_taker_buy = my_bid >= best_ask
            is_taker_sell = my_ask <= best_bid
            
            strong_signal = abs(obi) > 0.8
            
            if is_taker_buy and not strong_signal:
                my_bid = best_ask - 10**(-decimals) # Join best ask or just below? Best bid + 1 tick.
                # Actually, standard MM is to sit at Best Bid + 1 tick (Penny jump)
                # But here we calculated a theoretical price. Let's trust the theoretical price 
                # but ensure we don't accidentally take unless we mean to.
                my_bid = min(my_bid, best_ask - 10**(-decimals))
                
            if is_taker_sell and not strong_signal:
                my_ask = max(my_ask, best_bid + 10**(-decimals))
            
            # Inventory Limit Check
            # If too long, don't buy.
            if inventory > self.max_inventory:
                my_bid = 0.0 # Effectively cancel buy
                
            # If too short (assuming short selling is allowed and tracked as negative inventory), don't sell.
            # Note: sn-79 allows shorting? Balances are typically positive. 
            # If we sell without balance, we borrow? The `Account` object has free/reserved.
            # `leverage` param suggests borrowing.
            # If we are just spot trading, we can't sell what we don't have.
            # We need to check `free` balance.
            
            account = state.accounts.get(self.uid, {}).get(book_id)
            free_base = account.base_balance.free if account else 0
            free_quote = account.quote_balance.free if account else 0
            
            # Place Orders
            if my_bid > 0 and free_quote > (my_bid * self.order_quantity):
                response.limit_order(
                    book_id=book_id,
                    direction=OrderDirection.BUY,
                    quantity=self.order_quantity,
                    price=my_bid,
                    timeInForce=TimeInForce.GTT,
                    expiryPeriod=self.expiry_period,
                    postOnly=False # Allow taking if our price calculation says so (and we passed checks)
                )
                
            if my_ask > 0 and free_base >= self.order_quantity:
                response.limit_order(
                    book_id=book_id,
                    direction=OrderDirection.SELL,
                    quantity=self.order_quantity,
                    price=my_ask,
                    timeInForce=TimeInForce.GTT,
                    expiryPeriod=self.expiry_period,
                    postOnly=False
                )

        return response

if __name__ == "__main__":
    launch(EliteMarketMaker)
