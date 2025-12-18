# SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT

"""
AdaptiveMarketMakerAgent - High-Performance Order Book Trading Agent

This agent implements an optimized multi-strategy approach combining:
1. Adaptive market making with inventory control (Avellaneda-Stoikov inspired)
2. Order book imbalance signals for directional skew
3. Mean reversion bounds for risk management
4. Volume-aware activity management for reward optimization

Design optimized for sn-79 reward function:
- Maximizes Sharpe ratio through low-variance returns
- Generates consistent volume for activity multiplier
- Maintains performance across all books (avoids outliers)
- Fast execution to minimize latency penalties
"""

import time
import numpy as np
import bittensor as bt
from collections import defaultdict
from typing import Dict, Tuple, Optional

from taos.common.agents import launch
from taos.im.agents import FinanceSimulationAgent
from taos.im.protocol.models import *
from taos.im.protocol.instructions import *
from taos.im.protocol import MarketSimulationStateUpdate, FinanceAgentResponse


class AdaptiveMarketMakerAgent(FinanceSimulationAgent):
    """
    Advanced order book trading agent with adaptive market making and multi-strategy blending.
    
    Core Philosophy:
    - Market making provides base volume and Sharpe
    - Inventory control prevents volatility spikes
    - Imbalance signals improve directional accuracy
    - Risk management prevents outlier book performance
    """
    
    def initialize(self):
        """Initialize agent parameters and state tracking."""
        
        # ===== Market Making Parameters =====
        # Base spread from midquote (in price units, will be adjusted per book)
        self.base_spread_bps = float(getattr(self.config, 'base_spread_bps', 10.0))  # 10 bps default
        
        # Order size configuration
        self.base_order_size = float(getattr(self.config, 'base_order_size', 0.5))
        self.min_order_size = float(getattr(self.config, 'min_order_size', 0.1))
        self.max_order_size = float(getattr(self.config, 'max_order_size', 2.0))
        
        # Order expiry (in simulation nanoseconds)
        self.base_expiry = int(getattr(self.config, 'base_expiry', 30_000_000_000))  # 30s default
        self.min_expiry = int(getattr(self.config, 'min_expiry', 10_000_000_000))     # 10s
        self.max_expiry = int(getattr(self.config, 'max_expiry', 60_000_000_000))     # 60s
        
        # ===== Inventory Control Parameters =====
        # Maximum inventory deviation (in BASE units) before aggressive rebalancing
        self.max_inventory = float(getattr(self.config, 'max_inventory', 5.0))
        # Target inventory (usually 0 for neutral)
        self.target_inventory = float(getattr(self.config, 'target_inventory', 0.0))
        # Inventory skew intensity (how much to adjust quotes per unit inventory)
        self.inventory_skew_factor = float(getattr(self.config, 'inventory_skew_factor', 0.5))
        
        # ===== Imbalance Trading Parameters =====
        # Number of levels to include in imbalance calculation
        self.imbalance_depth = int(getattr(self.config, 'imbalance_depth', 5))
        # Threshold for strong imbalance signal (0-1 scale)
        self.imbalance_threshold = float(getattr(self.config, 'imbalance_threshold', 0.3))
        # Multiplier for imbalance-based directional sizing
        self.imbalance_sizing_factor = float(getattr(self.config, 'imbalance_sizing_factor', 0.3))
        
        # ===== Risk Management Parameters =====
        # Volatility lookback for risk scaling (number of observations)
        self.volatility_lookback = int(getattr(self.config, 'volatility_lookback', 10))
        # Risk aversion parameter (higher = more conservative)
        self.risk_aversion = float(getattr(self.config, 'risk_aversion', 0.5))
        # Maximum spread multiplier in high volatility
        self.max_spread_multiplier = float(getattr(self.config, 'max_spread_multiplier', 3.0))
        
        # ===== Volume Management Parameters =====
        # Target activity factor (1.0 = cap, 1.5 = typical good target)
        self.target_activity_factor = float(getattr(self.config, 'target_activity_factor', 1.5))
        # Volume safety margin (trade up to this fraction of cap per book)
        self.volume_safety_margin = float(getattr(self.config, 'volume_safety_margin', 0.95))
        
        # ===== State Tracking =====
        # Per-validator, per-book tracking
        self.inventory: Dict[str, Dict[int, float]] = defaultdict(lambda: defaultdict(float))
        self.midquote_history: Dict[str, Dict[int, list]] = defaultdict(lambda: defaultdict(list))
        self.pnl_history: Dict[str, Dict[int, list]] = defaultdict(lambda: defaultdict(list))
        self.last_midquote: Dict[str, Dict[int, float]] = defaultdict(lambda: defaultdict(float))
        self.open_orders: Dict[str, Dict[int, list]] = defaultdict(lambda: defaultdict(list))
        self.volume_traded: Dict[str, Dict[int, float]] = defaultdict(lambda: defaultdict(float))
        
        bt.logging.info(f"""
╔══════════════════════════════════════════════════════════════════╗
║          Adaptive Market Maker Agent - Configuration             ║
╠══════════════════════════════════════════════════════════════════╣
║ MARKET MAKING                                                    ║
║   Base Spread:              {self.base_spread_bps:>8.1f} bps                     ║
║   Order Size:               {self.base_order_size:>8.2f} (min: {self.min_order_size:.2f}, max: {self.max_order_size:.2f}) ║
║   Order Expiry:             {self.base_expiry/1e9:>8.1f}s (min: {self.min_expiry/1e9:.0f}s, max: {self.max_expiry/1e9:.0f}s)  ║
║                                                                  ║
║ INVENTORY CONTROL                                                ║
║   Max Inventory:            {self.max_inventory:>8.2f}                           ║
║   Target Inventory:         {self.target_inventory:>8.2f}                           ║
║   Skew Factor:              {self.inventory_skew_factor:>8.2f}                           ║
║                                                                  ║
║ IMBALANCE TRADING                                                ║
║   Imbalance Depth:          {self.imbalance_depth:>8d} levels                      ║
║   Imbalance Threshold:      {self.imbalance_threshold:>8.2f}                           ║
║   Sizing Factor:            {self.imbalance_sizing_factor:>8.2f}                           ║
║                                                                  ║
║ RISK MANAGEMENT                                                  ║
║   Volatility Lookback:      {self.volatility_lookback:>8d} periods                     ║
║   Risk Aversion:            {self.risk_aversion:>8.2f}                           ║
║   Max Spread Multiplier:    {self.max_spread_multiplier:>8.2f}x                          ║
║                                                                  ║
║ VOLUME MANAGEMENT                                                ║
║   Target Activity Factor:   {self.target_activity_factor:>8.2f}                           ║
║   Volume Safety Margin:     {self.volume_safety_margin:>8.1%}                          ║
╚══════════════════════════════════════════════════════════════════╝
        """)
    
    def calculate_volatility(self, validator: str, book_id: int) -> float:
        """
        Calculate realized volatility from midquote history.
        
        Returns volatility as standard deviation of log returns.
        """
        history = self.midquote_history[validator][book_id]
        if len(history) < 2:
            return 0.01  # Default low volatility
        
        # Calculate log returns
        prices = np.array(history[-self.volatility_lookback:])
        if len(prices) < 2:
            return 0.01
        
        returns = np.diff(np.log(prices))
        volatility = np.std(returns) if len(returns) > 0 else 0.01
        
        # Ensure minimum volatility
        return max(volatility, 0.001)
    
    def calculate_imbalance(self, book: Book, depth: int = None) -> float:
        """
        Calculate order book imbalance from depth.
        
        Imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume)
        
        Returns value in [-1, 1]:
        - Positive: more buying pressure
        - Negative: more selling pressure
        """
        depth = depth or self.imbalance_depth
        
        # Sum volumes up to specified depth
        bid_volume = sum(level.quantity for level in book.bids[:depth])
        ask_volume = sum(level.quantity for level in book.asks[:depth])
        
        total_volume = bid_volume + ask_volume
        if total_volume == 0:
            return 0.0
        
        imbalance = (bid_volume - ask_volume) / total_volume
        return np.clip(imbalance, -1.0, 1.0)
    
    def calculate_inventory(self, account: Account, validator: str, book_id: int) -> float:
        """
        Calculate net inventory position.
        
        Inventory = total_base - initial_base
        Positive = long, Negative = short
        """
        # Use current total base balance as inventory
        # (In sn-79, all agents start with same allocation, so deviation = inventory)
        current_base = account.base_balance.total
        
        # Store for tracking
        self.inventory[validator][book_id] = current_base
        
        return current_base
    
    def update_state_tracking(self, state: MarketSimulationStateUpdate, book_id: int, 
                             midquote: float, inventory: float):
        """Update historical tracking for features and PnL."""
        validator = state.dendrite.hotkey
        
        # Update midquote history
        self.midquote_history[validator][book_id].append(midquote)
        # Keep only recent history
        if len(self.midquote_history[validator][book_id]) > self.volatility_lookback * 2:
            self.midquote_history[validator][book_id] = \
                self.midquote_history[validator][book_id][-self.volatility_lookback * 2:]
        
        # Calculate PnL change (mark-to-market)
        if self.last_midquote[validator][book_id] > 0:
            # PnL from price change on inventory
            price_change = midquote - self.last_midquote[validator][book_id]
            pnl_change = inventory * price_change
            self.pnl_history[validator][book_id].append(pnl_change)
            
            # Keep only recent history
            if len(self.pnl_history[validator][book_id]) > self.volatility_lookback * 2:
                self.pnl_history[validator][book_id] = \
                    self.pnl_history[validator][book_id][-self.volatility_lookback * 2:]
        
        self.last_midquote[validator][book_id] = midquote
    
    def calculate_optimal_quotes(self, midquote: float, volatility: float, inventory: float,
                                 imbalance: float, config: MarketSimulationConfig) -> Tuple[float, float, float, float]:
        """
        Calculate optimal bid/ask quotes and sizes using Avellaneda-Stoikov + enhancements.
        
        Returns: (bid_price, ask_price, bid_size, ask_size)
        """
        # Base spread scaled by volatility and risk aversion
        base_spread = midquote * (self.base_spread_bps / 10000.0)
        volatility_multiplier = 1.0 + (volatility / 0.01) * self.risk_aversion
        volatility_multiplier = min(volatility_multiplier, self.max_spread_multiplier)
        spread = base_spread * volatility_multiplier
        
        # Inventory skew: widen quotes on side with excess inventory
        # If long (positive inventory), widen bids, tighten asks to encourage selling
        inventory_skew = self.inventory_skew_factor * (inventory / max(self.max_inventory, 0.1))
        inventory_skew = np.clip(inventory_skew, -0.5, 0.5)  # Limit skew impact
        
        # Imbalance adjustment: tighten quotes on imbalance side
        # Positive imbalance = buying pressure = tighten asks (be aggressive seller)
        imbalance_adjustment = 0.0
        if abs(imbalance) > self.imbalance_threshold:
            imbalance_adjustment = imbalance * spread * 0.3  # 30% max adjustment
        
        # Calculate bid and ask
        bid_spread = spread * (1.0 + inventory_skew) - imbalance_adjustment
        ask_spread = spread * (1.0 - inventory_skew) + imbalance_adjustment
        
        # Ensure minimum spread
        min_tick = 10 ** (-config.priceDecimals)
        bid_spread = max(bid_spread, min_tick)
        ask_spread = max(ask_spread, min_tick)
        
        bid_price = round(midquote - bid_spread, config.priceDecimals)
        ask_price = round(midquote + ask_spread, config.priceDecimals)
        
        # Size calculation: scale by imbalance and inventory
        base_size = self.base_order_size
        
        # Reduce size if inventory is large (risk management)
        inventory_size_factor = 1.0 - min(abs(inventory) / self.max_inventory, 0.7)
        
        # Increase size on favorable imbalance side
        bid_size = base_size * inventory_size_factor
        ask_size = base_size * inventory_size_factor
        
        if abs(imbalance) > self.imbalance_threshold:
            if imbalance > 0:  # Buying pressure, increase bid size
                bid_size *= (1.0 + self.imbalance_sizing_factor)
            else:  # Selling pressure, increase ask size
                ask_size *= (1.0 + self.imbalance_sizing_factor)
        
        # Clip to limits
        bid_size = np.clip(bid_size, self.min_order_size, self.max_order_size)
        ask_size = np.clip(ask_size, self.min_order_size, self.max_order_size)
        
        # Round to volume decimals
        bid_size = round(bid_size, config.volumeDecimals)
        ask_size = round(ask_size, config.volumeDecimals)
        
        return bid_price, ask_price, bid_size, ask_size
    
    def calculate_adaptive_expiry(self, volatility: float) -> int:
        """
        Calculate adaptive order expiry based on volatility.
        
        Higher volatility → shorter expiry (to avoid adverse selection)
        Lower volatility → longer expiry (to maximize fill rate)
        """
        # Normalize volatility (typical range 0.001 to 0.05)
        vol_normalized = np.clip(volatility / 0.01, 0.1, 5.0)
        
        # Inverse relationship: high vol = short expiry
        expiry_factor = 1.0 / vol_normalized
        expiry = int(self.base_expiry * expiry_factor)
        
        # Clip to bounds
        expiry = np.clip(expiry, self.min_expiry, self.max_expiry)
        
        return expiry
    
    def check_volume_limit(self, validator: str, book_id: int, 
                          config: MarketSimulationConfig, 
                          simulation_config: MarketSimulationConfig) -> bool:
        """
        Check if we're approaching volume cap for this book.
        
        Returns True if we can still trade, False if we should pause.
        """
        # Volume cap calculation
        # From sn-79 code: cap = capital_turnover_cap * miner_wealth
        # Default capital_turnover_cap = 20.0
        # We use a safety margin to stay below cap
        
        # Note: Full volume tracking would require event history
        # For this agent, we use a simplified estimate
        # In production, you'd track from TradeEvent notices
        
        # Simplified approach: don't limit (could enhance with trade tracking)
        return True
    
    def place_market_making_orders(self, response: FinanceAgentResponse, book_id: int,
                                   bid_price: float, ask_price: float, 
                                   bid_size: float, ask_size: float,
                                   expiry: int, config: MarketSimulationConfig):
        """Place the core market making limit orders."""
        
        # Bid order (buy side)
        response.limit_order(
            book_id=book_id,
            direction=OrderDirection.BUY,
            quantity=bid_size,
            price=bid_price,
            timeInForce=TimeInForce.GTT,
            expiryPeriod=expiry,
            postOnly=True,  # Ensures we're providing liquidity
            stp=STP.CANCEL_OLDEST  # Avoid self-trading
        )
        
        # Ask order (sell side)
        response.limit_order(
            book_id=book_id,
            direction=OrderDirection.SELL,
            quantity=ask_size,
            price=ask_price,
            timeInForce=TimeInForce.GTT,
            expiryPeriod=expiry,
            postOnly=True,
            stp=STP.CANCEL_OLDEST
        )
    
    def place_inventory_rebalancing_order(self, response: FinanceAgentResponse, book_id: int,
                                         inventory: float, midquote: float, 
                                         config: MarketSimulationConfig):
        """
        Place aggressive order to rebalance extreme inventory.
        
        Only triggered when inventory exceeds max_inventory threshold.
        """
        if abs(inventory) < self.max_inventory:
            return  # No rebalancing needed
        
        # Determine direction and size
        if inventory > self.max_inventory:
            # Too long, need to sell
            direction = OrderDirection.SELL
            quantity = round(inventory - self.target_inventory, config.volumeDecimals)
            # Place aggressive limit order just inside best ask
            price = round(midquote * 0.999, config.priceDecimals)  # 0.1% below mid
        else:
            # Too short, need to buy
            direction = OrderDirection.BUY
            quantity = round(-inventory + self.target_inventory, config.volumeDecimals)
            # Place aggressive limit order just inside best bid
            price = round(midquote * 1.001, config.priceDecimals)  # 0.1% above mid
        
        quantity = min(quantity, self.max_order_size)
        quantity = max(quantity, self.min_order_size)
        
        bt.logging.warning(
            f"BOOK {book_id}: Extreme inventory {inventory:.2f}, placing rebalancing order "
            f"{direction.name} {quantity:.2f}@{price:.2f}"
        )
        
        response.limit_order(
            book_id=book_id,
            direction=direction,
            quantity=quantity,
            price=price,
            timeInForce=TimeInForce.IOC,  # Immediate or Cancel
            stp=STP.CANCEL_OLDEST
        )
    
    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        """
        Main trading logic: processes market state and generates orders.
        
        Strategy flow:
        1. Extract market data (bids, asks, depth)
        2. Calculate features (volatility, imbalance, inventory)
        3. Compute optimal quotes using Avellaneda-Stoikov + enhancements
        4. Place market making orders
        5. Place rebalancing orders if needed
        """
        start_time = time.time()
        response = FinanceAgentResponse(agent_id=self.uid)
        validator = state.dendrite.hotkey
        
        # Check if we have account info
        if not state.accounts or self.uid not in state.accounts:
            bt.logging.warning(f"No account information available for UID {self.uid}")
            return response
        
        accounts = state.accounts[self.uid]
        
        # Process each book
        for book_id, book in state.books.items():
            try:
                # Skip if no market data
                if not book.bids or not book.asks:
                    continue
                
                # Extract market data
                best_bid = book.bids[0].price
                best_ask = book.asks[0].price
                midquote = (best_bid + best_ask) / 2.0
                
                # Get account for this book
                if book_id not in accounts:
                    continue
                account = accounts[book_id]
                
                # Calculate features
                inventory = self.calculate_inventory(account, validator, book_id)
                volatility = self.calculate_volatility(validator, book_id)
                imbalance = self.calculate_imbalance(book, self.imbalance_depth)
                
                # Update tracking
                self.update_state_tracking(state, book_id, midquote, inventory)
                
                # Check volume limits
                if not self.check_volume_limit(validator, book_id, state.config, 
                                              self.simulation_config):
                    bt.logging.info(f"BOOK {book_id}: Near volume cap, reducing activity")
                    continue
                
                # Calculate optimal quotes
                bid_price, ask_price, bid_size, ask_size = self.calculate_optimal_quotes(
                    midquote, volatility, inventory, imbalance, state.config
                )
                
                # Calculate adaptive expiry
                expiry = self.calculate_adaptive_expiry(volatility)
                
                # Log strategy decision
                bt.logging.info(
                    f"BOOK {book_id}: mid={midquote:.2f} vol={volatility:.4f} "
                    f"imbal={imbalance:+.3f} inv={inventory:+.2f} | "
                    f"quotes: {bid_price:.2f}({bid_size:.2f}) / {ask_price:.2f}({ask_size:.2f}) "
                    f"expiry={expiry/1e9:.0f}s"
                )
                
                # Place market making orders
                self.place_market_making_orders(
                    response, book_id, bid_price, ask_price, bid_size, ask_size, 
                    expiry, state.config
                )
                
                # Place inventory rebalancing order if needed
                self.place_inventory_rebalancing_order(
                    response, book_id, inventory, midquote, state.config
                )
                
            except Exception as e:
                bt.logging.error(f"BOOK {book_id}: Error processing - {e}")
                continue
        
        elapsed = time.time() - start_time
        bt.logging.info(
            f"Response generated with {len(response.instructions)} instructions "
            f"in {elapsed:.3f}s"
        )
        
        return response
    
    def onTrade(self, event: TradeEvent) -> None:
        """
        Handle trade notifications to track volume and performance.
        
        This is called automatically when our orders are filled.
        """
        # Track volume for activity monitoring
        validator = event.dendrite.hotkey if hasattr(event, 'dendrite') else 'unknown'
        book_id = event.bookId
        
        if book_id is not None:
            trade_volume = event.quantity * event.price
            self.volume_traded[validator][book_id] += trade_volume
            
            bt.logging.info(
                f"TRADE: Book {book_id} - {'BUY' if event.side == 0 else 'SELL'} "
                f"{event.quantity:.2f}@{event.price:.2f} (volume: {trade_volume:.2f})"
            )


if __name__ == "__main__":
    """
    Launch the agent for standalone or miner execution.
    
    Example local test command:
    python AdaptiveMarketMakerAgent.py --port 8888 --agent_id 0 \\
        --params base_spread_bps=10.0 base_order_size=0.5 max_inventory=5.0 \\
                 imbalance_depth=5 risk_aversion=0.5 target_activity_factor=1.5
    
    Example miner deployment:
    ./run_miner.sh -n AdaptiveMarketMakerAgent \\
        -m "base_spread_bps=8.0 base_order_size=1.0 max_inventory=8.0 \\
            imbalance_depth=7 risk_aversion=0.4"
    """
    launch(AdaptiveMarketMakerAgent)
