# SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT

import bittensor as bt
from taos.common.agents import launch
from taos.im.agents import FinanceSimulationAgent
from taos.im.protocol.models import *
from taos.im.protocol.instructions import *
from taos.im.protocol import MarketSimulationStateUpdate, FinanceAgentResponse

import numpy as np
import collections

class SmartOrderBookAgent(FinanceSimulationAgent):
    """
    A high-performance market making agent combining Inventory Risk (Avellaneda-Stoikov) 
    and Order Flow Imbalance (Cartea-Jaimungal) strategies.
    """
    def initialize(self):
        # Configuration parameters
        self.quantity = getattr(self.config, 'quantity', 1.0)
        self.gamma = getattr(self.config, 'risk_aversion', 0.1)  # Inventory risk aversion
        self.kappa = getattr(self.config, 'imbalance_sensitivity', 0.05) # Alpha sensitivity
        self.window_size = getattr(self.config, 'window_size', 20)
        self.base_spread_bps = getattr(self.config, 'base_spread_bps', 10) # 10 basis points
        self.max_pos = getattr(self.config, 'max_pos', 100.0)
        
        # State tracking
        self.mid_price_history = collections.deque(maxlen=self.window_size)
        self.volatility = 0.0
        
    def get_weighted_imbalance(self, book: Book, depth: int = 5) -> float:
        """
        Calculates volume-weighted imbalance for the top `depth` levels.
        Formula: (BidVol - AskVol) / (BidVol + AskVol)
        Returns a value between -1 (Pure Ask pressure) and 1 (Pure Bid pressure).
        """
        total_bid_vol = 0.0
        total_ask_vol = 0.0
        
        # Sum volume for top levels
        for i in range(min(depth, len(book.bids))):
            total_bid_vol += book.bids[i].quantity
            
        for i in range(min(depth, len(book.asks))):
            total_ask_vol += book.asks[i].quantity
            
        if total_bid_vol + total_ask_vol == 0:
            return 0.0
            
        return (total_bid_vol - total_ask_vol) / (total_bid_vol + total_ask_vol)

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        response = FinanceAgentResponse(agent_id=self.uid)
        
        for book_id, book in state.books.items():
            if not book.bids or not book.asks:
                continue
                
            best_bid = book.bids[0].price
            best_ask = book.asks[0].price
            mid_price = (best_bid + best_ask) / 2.0
            
            # Update volatility
            self.mid_price_history.append(mid_price)
            if len(self.mid_price_history) > 5:
                # Calculate simple realized volatility
                prices = np.array(self.mid_price_history)
                returns = np.diff(prices) / prices[:-1]
                self.volatility = np.std(returns) if len(returns) > 0 else 0.0
            
            # --- STRATEGY LOGIC ---
            
            # 1. Get current inventory (from account for this book)
            # Find the account corresponding to this book
            my_account = None
            if self.uid in state.accounts:
                for acc_book_id, acc in state.accounts[self.uid].items():
                    if acc_book_id == book_id:
                        my_account = acc
                        break
            
            # Estimate inventory (net position). 
            # Note: Simulator might track base/quote. 
            # Assuming 'base_balance' change from initial or just using what we have.
            # Simplified: Use the 'v' (traded volume) or just track net trades if state doesn't give net position easily.
            # However, looking at Account model, we have base_balance (bb) and quote_balance (qb).
            # We can calculate inventory deviation from a "target" or "initial" if known. 
            # For now, we assume we want to stay neutral relative to current holdings if we treat them as 0,
            # or we need to know our initial balance. 
            # Let's assume we want to hold constant BASE. 
            
            # Simplification: Inventory parameter 'q' is normalized position -1 to 1 or actual quantity.
            # Since we don't have easy "initial balance" in this scope without tracking, 
            # we'll approximate q based on recent fills or just set to 0 (neutral) 
            # and rely on immediate inventory logic if we could track it.
            # Ideally we track self.position[book_id].
            
            # For this implementation, I will omit strict inventory tracking variable 'q' 
            # and focus on Alpha (Imbalance) + Volatility, unless I can persist state.
            # I can persist state in `self`.
            if not hasattr(self, 'inventory'):
                self.inventory = {}
            
            current_inv = self.inventory.get(book_id, 0.0)

            # 2. Calculate Reservation Price (Inventory Adjust)
            # r = s - q * gamma * sigma^2
            # Scaling sigma^2 might be too small per step, so we use a heuristic gamma.
            reservation_price = mid_price - (current_inv * self.gamma * self.volatility * mid_price)

            # 3. Calculate Alpha (Order Book Imbalance)
            imbalance = self.get_weighted_imbalance(book, depth=5)
            
            # 4. Final Target Price
            # Shift reservation price based on alpha
            target_price = reservation_price + (imbalance * self.kappa * mid_price)
            
            # 5. Calculate Spread
            # Base spread + volatility component
            half_spread = (mid_price * (self.base_spread_bps / 10000.0)) + \
                          (self.volatility * mid_price * 2) # Widen by 2 sigma
            
            bid_price = target_price - half_spread
            ask_price = target_price + half_spread
            
            # 6. Sanity Checks & Rounding
            # Ensure we don't cross the actual book aggressively unless alpha is huge
            # (Passive market making preference)
            bid_price = min(bid_price, best_ask - (mid_price * 0.0001)) # Don't cross ask
            ask_price = max(ask_price, best_bid + (mid_price * 0.0001)) # Don't cross bid
            
            # Rounding (assuming 2 decimals for price from config, but safe to default standard)
            # Ideally use state.config info if available, but models don't show config passed in respond.
            # We'll rely on simulator to accept floats or round to typical 2-4 decimals.
            bid_price = round(bid_price, 2)
            ask_price = round(ask_price, 2)
            
            # 7. Generate Orders
            # We place GTT orders (Good Till Time) so we don't have to manually cancel every time
            # This is "Order Cancellation" optimization.
            # Default expiry: 10 seconds (in nanoseconds)
            expiry = getattr(self.config, 'expiry_period', 10_000_000_000)
            
            # Buy Order
            if current_inv < self.max_pos:
                response.limit_order(
                    book_id=book_id,
                    direction=OrderDirection.BUY,
                    quantity=self.quantity,
                    price=bid_price,
                    timeInForce=TimeInForce.GTT,
                    expiryPeriod=expiry
                )
                
            # Sell Order
            if current_inv > -self.max_pos:
                response.limit_order(
                    book_id=book_id,
                    direction=OrderDirection.SELL,
                    quantity=self.quantity,
                    price=ask_price,
                    timeInForce=TimeInForce.GTT,
                    expiryPeriod=expiry
                )
        
        # Update Inventory Logic (Approximation)
        # In a real agent, we'd process 'events' in the book to see if OUR orders filled.
        # state.accounts contains our executed trades? No, state.books has events.
        # We need to check if any of the trades in the book involved our agent_id.
        for book_id, book in state.books.items():
            if book.events:
                for event in book.events:
                    if isinstance(event, TradeInfo):
                        # Check if we were Maker or Taker
                        change = 0
                        if event.maker_agent_id == self.uid:
                            # We were maker. 
                            # If we were maker and side was BUY (bid), we BOUGHT.
                            # Maker side is the resting order side.
                            # If taker is SELL, maker is BUY.
                            # TradeInfo side is "Direction in which the trade was initiated" (Taker side)
                            # If Trade side is SELL, Taker sold, Maker bought.
                            if event.side == OrderDirection.SELL: 
                                change = event.quantity # Maker Bought
                            else:
                                change = -event.quantity # Maker Sold
                        elif event.taker_agent_id == self.uid:
                            # We were taker
                            if event.side == OrderDirection.BUY:
                                change = event.quantity # Taker Bought
                            else:
                                change = -event.quantity # Taker Sold
                        
                        if change != 0:
                            self.inventory[book_id] = self.inventory.get(book_id, 0.0) + change

        return response

if __name__ == "__main__":
    launch(SmartOrderBookAgent)
