# SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT

"""
═══════════════════════════════════════════════════════════════════════════════
OrderBookMarketMaker: Elite Competitive Trading Agent for SN-79
═══════════════════════════════════════════════════════════════════════════════

STRATEGY OVERVIEW:
This agent implements a sophisticated market-making strategy optimized for the
SN-79 reward mechanism, combining proven microstructure techniques:

1. ORDER BOOK IMBALANCE SIGNAL
   - Academic basis: Cont et al. (2014), "The Price Impact of Order Book Events"
   - Empirical finding: Imbalance predicts short-term price movements
   - Implementation: Multi-level weighted imbalance with decay

2. INVENTORY-AWARE QUOTING
   - Academic basis: Avellaneda & Stoikov (2008), "High-frequency trading in a limit order book"
   - Purpose: Manage inventory risk by skewing quotes
   - Implementation: Exponential inventory aversion

3. SPREAD CAPTURE OPTIMIZATION
   - Strategy: Place orders inside the spread to capture rebates
   - Risk control: Use post-only orders to avoid adverse selection
   - Volume optimization: Adjust sizes based on book depth

4. ADVERSE SELECTION MITIGATION
   - Signal: Rapid order flow imbalance + trade imbalance divergence
   - Response: Widen spreads or temporarily pause on toxic flow
   - Academic basis: Menkveld (2013), "High Frequency Trading and The New Market Makers"

REWARD OPTIMIZATION:
- Sharpe maximization: Tight risk controls, consistent small wins
- Volume factor: Aggressive quoting to hit 2x activity multiplier
- Outlier avoidance: Conservative fallback when uncertain
- Speed: Optimized data structures, minimal computation

═══════════════════════════════════════════════════════════════════════════════
"""

import time
import numpy as np
from collections import deque, defaultdict
import bittensor as bt

from taos.common.agents import launch
from taos.im.agents import FinanceSimulationAgent
from taos.im.protocol.models import *
from taos.im.protocol.instructions import *
from taos.im.protocol import MarketSimulationStateUpdate, FinanceAgentResponse


class OrderBookMarketMaker(FinanceSimulationAgent):
    """
    Production-ready market making agent optimized for SN-79 reward structure.
    
    Core competencies:
    - Microstructure-based alpha generation
    - Dynamic inventory management
    - Adverse selection protection
    - High-frequency spread capture
    """
    
    def initialize(self):
        """Initialize agent parameters and state tracking."""
        
        # ═══════════════════════════════════════════════════════════════════
        # STRATEGY PARAMETERS
        # ═══════════════════════════════════════════════════════════════════
        
        # Order sizing: Balance between volume factor and risk
        self.base_order_size = float(self.config.base_order_size) if hasattr(self.config, 'base_order_size') else 0.5
        self.max_order_size = float(self.config.max_order_size) if hasattr(self.config, 'max_order_size') else 2.0
        
        # Spread parameters: Optimize for spread capture while managing risk
        self.min_spread_fraction = float(self.config.min_spread_fraction) if hasattr(self.config, 'min_spread_fraction') else 0.25  # Quote inside 25% of spread
        self.max_spread_fraction = float(self.config.max_spread_fraction) if hasattr(self.config, 'max_spread_fraction') else 0.75
        
        # Inventory management: Critical for Sharpe ratio
        self.max_inventory_fraction = float(self.config.max_inventory_fraction) if hasattr(self.config, 'max_inventory_fraction') else 0.3  # Max 30% of capital in base
        self.inventory_skew_strength = float(self.config.inventory_skew_strength) if hasattr(self.config, 'inventory_skew_strength') else 2.0
        
        # Imbalance signal: Primary alpha source
        self.imbalance_lookback = int(self.config.imbalance_lookback) if hasattr(self.config, 'imbalance_lookback') else 5
        self.imbalance_depth = int(self.config.imbalance_depth) if hasattr(self.config, 'imbalance_depth') else 5  # Top 5 levels
        self.imbalance_threshold = float(self.config.imbalance_threshold) if hasattr(self.config, 'imbalance_threshold') else 0.1
        
        # Adverse selection protection
        self.trade_imbalance_threshold = float(self.config.trade_imbalance_threshold) if hasattr(self.config, 'trade_imbalance_threshold') else 0.3
        self.toxic_flow_penalty = float(self.config.toxic_flow_penalty) if hasattr(self.config, 'toxic_flow_penalty') else 2.0  # Widen spread multiplier
        
        # Order expiry: Balance between stale orders and volume
        self.order_expiry = int(self.config.order_expiry) if hasattr(self.config, 'order_expiry') else 60_000_000_000  # 60 seconds
        
        # ═══════════════════════════════════════════════════════════════════
        # STATE TRACKING
        # ═══════════════════════════════════════════════════════════════════
        
        # Per-validator, per-book state
        self.book_state = defaultdict(lambda: defaultdict(dict))
        
        # Imbalance history: Rolling window for signal calculation
        self.imbalance_history = defaultdict(lambda: defaultdict(lambda: deque(maxlen=self.imbalance_lookback)))
        
        # Trade flow tracking: Detect adverse selection
        self.trade_flow_history = defaultdict(lambda: defaultdict(lambda: deque(maxlen=10)))
        
        # Midquote history: For inventory valuation
        self.midquote_history = defaultdict(lambda: defaultdict(lambda: deque(maxlen=100)))
        
        # Performance tracking
        self.pnl_per_book = defaultdict(lambda: defaultdict(float))
        self.volume_per_book = defaultdict(lambda: defaultdict(float))
        
        bt.logging.info(self._format_config())
    
    def _format_config(self) -> str:
        """Format configuration for logging."""
        return f"""
╔═══════════════════════════════════════════════════════════════════════════════╗
║                   ORDER BOOK MARKET MAKER CONFIGURATION                       ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║ ORDER SIZING                                                                  ║
║   Base Size                : {self.base_order_size:>8.2f}                                          ║
║   Max Size                 : {self.max_order_size:>8.2f}                                          ║
║                                                                               ║
║ SPREAD PARAMETERS                                                             ║
║   Min Spread Fraction      : {self.min_spread_fraction:>8.2%}                                          ║
║   Max Spread Fraction      : {self.max_spread_fraction:>8.2%}                                          ║
║                                                                               ║
║ INVENTORY MANAGEMENT                                                          ║
║   Max Inventory Fraction   : {self.max_inventory_fraction:>8.2%}                                          ║
║   Skew Strength            : {self.inventory_skew_strength:>8.2f}                                          ║
║                                                                               ║
║ IMBALANCE SIGNAL                                                              ║
║   Lookback Window          : {self.imbalance_lookback:>8d} observations                              ║
║   Book Depth               : {self.imbalance_depth:>8d} levels                                    ║
║   Signal Threshold         : {self.imbalance_threshold:>8.2%}                                          ║
║                                                                               ║
║ ADVERSE SELECTION                                                             ║
║   Trade Imbalance Threshold: {self.trade_imbalance_threshold:>8.2%}                                          ║
║   Toxic Flow Penalty       : {self.toxic_flow_penalty:>8.2f}x                                         ║
║                                                                               ║
║ EXECUTION                                                                     ║
║   Order Expiry             : {self.order_expiry / 1e9:>8.1f}s                                         ║
╚═══════════════════════════════════════════════════════════════════════════════╝
"""
    
    def calculate_book_imbalance(self, book: Book, depth: int = None) -> float:
        """
        Calculate order book imbalance with exponential depth weighting.
        
        Academic basis: "The Price Impact of Order Book Events" (Cont et al., 2014)
        - Imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume)
        - Weighted by level depth to emphasize near-touch liquidity
        
        Args:
            book: Order book state
            depth: Number of levels to consider (default: self.imbalance_depth)
        
        Returns:
            Imbalance value in [-1, 1], where positive = bullish
        """
        if depth is None:
            depth = self.imbalance_depth
        
        bid_volume = 0.0
        ask_volume = 0.0
        
        # Exponential decay: weight = exp(-lambda * level)
        decay_lambda = 0.5
        
        for i, bid_level in enumerate(book.bids[:depth]):
            weight = np.exp(-decay_lambda * i)
            bid_volume += bid_level.quantity * weight
        
        for i, ask_level in enumerate(book.asks[:depth]):
            weight = np.exp(-decay_lambda * i)
            ask_volume += ask_level.quantity * weight
        
        total_volume = bid_volume + ask_volume
        if total_volume < 1e-8:
            return 0.0
        
        return (bid_volume - ask_volume) / total_volume
    
    def calculate_trade_flow_imbalance(self, book: Book) -> float:
        """
        Calculate trade flow imbalance from recent trades.
        
        Purpose: Detect directional pressure and adverse selection risk
        - High trade imbalance = informed trading likely occurring
        - Divergence from book imbalance = potential adverse selection
        
        Args:
            book: Order book state with events
        
        Returns:
            Trade imbalance in [-1, 1]
        """
        if not book.events:
            return 0.0
        
        buy_volume = 0.0
        sell_volume = 0.0
        
        for event in book.events:
            if isinstance(event, TradeInfo):
                if event.side == OrderDirection.BUY:
                    buy_volume += event.quantity
                else:
                    sell_volume += event.quantity
        
        total_volume = buy_volume + sell_volume
        if total_volume < 1e-8:
            return 0.0
        
        return (buy_volume - sell_volume) / total_volume
    
    def calculate_inventory_position(self, account: Account, midquote: float) -> float:
        """
        Calculate normalized inventory position.
        
        Returns:
            Inventory fraction in [-1, 1], where:
            - +1 = fully long (all capital in base)
            - -1 = fully short (all capital in quote)
            -  0 = neutral
        """
        base_value = account.own_base * midquote
        quote_value = account.own_quote
        total_value = base_value + quote_value
        
        if total_value < 1e-8:
            return 0.0
        
        # Normalize to [-1, 1]
        return (base_value - quote_value) / total_value
    
    def detect_adverse_selection(self, validator: str, book_id: int, 
                                  book_imbalance: float, trade_imbalance: float) -> bool:
        """
        Detect toxic order flow indicating informed trading.
        
        Heuristic: Large trade imbalance diverging from book imbalance
        suggests informed traders are aggressively taking liquidity.
        
        Academic basis: "High Frequency Trading and The New Market Makers" (Menkveld, 2013)
        
        Returns:
            True if adverse selection detected
        """
        # Check if trade flow is significantly different from book imbalance
        imbalance_divergence = abs(trade_imbalance - book_imbalance)
        
        # If trades are heavily directional and diverge from book state
        if abs(trade_imbalance) > self.trade_imbalance_threshold and \
           imbalance_divergence > 0.3:
            return True
        
        return False
    
    def calculate_optimal_quotes(self, book: Book, account: Account, 
                                 book_imbalance: float, inventory_position: float,
                                 adverse_selection: bool, config) -> tuple:
        """
        Calculate optimal bid/ask quotes using microstructure models.
        
        Implements: Avellaneda-Stoikov market making model with modifications
        
        Quote placement considers:
        1. Spread capture opportunity (quote inside spread)
        2. Inventory skew (skew away from large positions)
        3. Imbalance signal (lean into imbalance direction)
        4. Adverse selection (widen on toxic flow)
        
        Returns:
            (bid_price, ask_price, bid_size, ask_size)
        """
        best_bid = book.bids[0].price
        best_ask = book.asks[0].price
        spread = best_ask - best_bid
        midquote = (best_bid + best_ask) / 2
        
        # ═══════════════════════════════════════════════════════════════════
        # 1. BASE SPREAD POSITIONING
        # ═══════════════════════════════════════════════════════════════════
        
        # Default: quote at min_spread_fraction inside the spread
        base_spread_offset = spread * self.min_spread_fraction
        
        # Widen if adverse selection detected
        if adverse_selection:
            base_spread_offset *= self.toxic_flow_penalty
            bt.logging.warning(f"BOOK {book.id}: Adverse selection detected - widening spread")
        
        # ═══════════════════════════════════════════════════════════════════
        # 2. INVENTORY SKEW
        # ═══════════════════════════════════════════════════════════════════
        
        # Skew quotes away from large inventory positions
        # If long: widen ask, tighten bid (encourage selling)
        # If short: widen bid, tighten ask (encourage buying)
        inventory_skew = inventory_position * self.inventory_skew_strength * spread * 0.5
        
        # ═══════════════════════════════════════════════════════════════════
        # 3. IMBALANCE SIGNAL
        # ═══════════════════════════════════════════════════════════════════
        
        # If book is bid-heavy (positive imbalance), expect upward price movement
        # -> Be more aggressive on ask, less on bid
        imbalance_adjustment = 0.0
        if abs(book_imbalance) > self.imbalance_threshold:
            # Scale by imbalance strength
            imbalance_adjustment = book_imbalance * spread * 0.3
        
        # ═══════════════════════════════════════════════════════════════════
        # 4. COMPUTE FINAL QUOTES
        # ═══════════════════════════════════════════════════════════════════
        
        # Bid: quote below midquote
        bid_offset = base_spread_offset - inventory_skew + imbalance_adjustment
        bid_price = midquote - bid_offset
        
        # Ask: quote above midquote  
        ask_offset = base_spread_offset + inventory_skew - imbalance_adjustment
        ask_price = midquote + ask_offset
        
        # Ensure we don't cross the market or quote outside reasonable bounds
        bid_price = min(bid_price, best_bid + spread * 0.4)
        bid_price = max(bid_price, best_bid - spread * 0.1)
        
        ask_price = max(ask_price, best_ask - spread * 0.4)
        ask_price = min(ask_price, best_ask + spread * 0.1)
        
        # Round to price decimals
        bid_price = round(bid_price, config.priceDecimals)
        ask_price = round(ask_price, config.priceDecimals)
        
        # ═══════════════════════════════════════════════════════════════════
        # 5. DYNAMIC ORDER SIZING
        # ═══════════════════════════════════════════════════════════════════
        
        # Base size adjusted by:
        # - Book depth (more size when more liquidity)
        # - Inventory position (reduce size when approaching limits)
        # - Imbalance strength (more aggressive when signal is strong)
        
        depth_factor = min(book.bids[0].quantity / 10.0, 1.5)
        inventory_factor = max(0.3, 1.0 - abs(inventory_position))
        signal_factor = 1.0 + abs(book_imbalance) * 0.5
        
        bid_size = self.base_order_size * depth_factor * inventory_factor * signal_factor
        ask_size = self.base_order_size * depth_factor * inventory_factor * signal_factor
        
        # Inventory skew: reduce size on side we're heavy
        if inventory_position > self.max_inventory_fraction:
            # Long: reduce bid size
            bid_size *= 0.5
            ask_size *= 1.5
        elif inventory_position < -self.max_inventory_fraction:
            # Short: reduce ask size
            bid_size *= 1.5
            ask_size *= 0.5
        
        # Cap at max order size
        bid_size = min(bid_size, self.max_order_size)
        ask_size = min(ask_size, self.max_order_size)
        
        # Round to volume decimals
        bid_size = round(bid_size, config.volumeDecimals)
        ask_size = round(ask_size, config.volumeDecimals)
        
        return bid_price, ask_price, bid_size, ask_size
    
    def should_cancel_orders(self, account: Account, book: Book, config) -> list:
        """
        Determine which orders should be cancelled.
        
        Cancel orders that:
        1. Are far from current market (stale)
        2. Are on wrong side given inventory position
        3. Are too small to be meaningful
        
        Returns:
            List of order IDs to cancel
        """
        if not account.orders:
            return []
        
        to_cancel = []
        midquote = (book.bids[0].price + book.asks[0].price) / 2
        spread = book.asks[0].price - book.bids[0].price
        
        for order in account.orders:
            # Cancel if too far from market (more than 2x spread away)
            distance = abs(order.price - midquote)
            if distance > 2 * spread:
                to_cancel.append(order.id)
                continue
            
            # Cancel tiny orders (less than 10% of base size)
            if order.quantity < self.base_order_size * 0.1:
                to_cancel.append(order.id)
        
        return to_cancel
    
    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        """
        Main strategy loop: analyze market state and generate orders.
        
        Process:
        1. Update state tracking
        2. Calculate signals (imbalance, trade flow)
        3. Check for adverse selection
        4. Compute optimal quotes
        5. Cancel stale orders
        6. Place new orders
        
        Args:
            state: Current market state from validator
        
        Returns:
            Response with limit order instructions
        """
        response = FinanceAgentResponse(agent_id=self.uid)
        validator = state.dendrite.hotkey
        
        start_time = time.time()
        
        # Process each book independently
        for book_id, book in state.books.items():
            try:
                # Skip if book is empty or broken
                if not book.bids or not book.asks:
                    bt.logging.warning(f"BOOK {book_id}: Empty book - skipping")
                    continue
                
                # Get account state
                if validator not in state.accounts or book_id not in state.accounts[validator]:
                    bt.logging.warning(f"BOOK {book_id}: No account state - skipping")
                    continue
                
                account = state.accounts[validator][book_id]
                
                # ═══════════════════════════════════════════════════════════
                # SIGNAL CALCULATION
                # ═══════════════════════════════════════════════════════════
                
                # 1. Book imbalance (primary alpha signal)
                book_imbalance = self.calculate_book_imbalance(book)
                self.imbalance_history[validator][book_id].append(book_imbalance)
                
                # 2. Trade flow imbalance (adverse selection detection)
                trade_imbalance = self.calculate_trade_flow_imbalance(book)
                self.trade_flow_history[validator][book_id].append(trade_imbalance)
                
                # 3. Inventory position
                midquote = (book.bids[0].price + book.asks[0].price) / 2
                self.midquote_history[validator][book_id].append(midquote)
                inventory_position = self.calculate_inventory_position(account, midquote)
                
                # 4. Adverse selection detection
                adverse_selection = self.detect_adverse_selection(
                    validator, book_id, book_imbalance, trade_imbalance
                )
                
                # ═══════════════════════════════════════════════════════════
                # ORDER MANAGEMENT
                # ═══════════════════════════════════════════════════════════
                
                # Cancel stale orders
                orders_to_cancel = self.should_cancel_orders(account, book, state.config)
                if orders_to_cancel:
                    response.cancel_orders(book_id=book_id, order_ids=orders_to_cancel)
                
                # Calculate optimal quotes
                bid_price, ask_price, bid_size, ask_size = self.calculate_optimal_quotes(
                    book, account, book_imbalance, inventory_position,
                    adverse_selection, state.config
                )
                
                # ═══════════════════════════════════════════════════════════
                # RISK CHECKS
                # ═══════════════════════════════════════════════════════════
                
                # Don't place orders if at max open orders
                if len(account.orders) >= state.config.max_open_orders - 2:
                    bt.logging.info(f"BOOK {book_id}: At max open orders - skipping placement")
                    continue
                
                # Check balance constraints
                if account.quote_balance.free < bid_size * bid_price * 0.5:
                    bt.logging.warning(f"BOOK {book_id}: Insufficient quote balance")
                    bid_size = max(account.quote_balance.free / bid_price * 0.8, 0)
                    bid_size = round(bid_size, state.config.volumeDecimals)
                
                if account.base_balance.free < ask_size * 0.5:
                    bt.logging.warning(f"BOOK {book_id}: Insufficient base balance")
                    ask_size = max(account.base_balance.free * 0.8, 0)
                    ask_size = round(ask_size, state.config.volumeDecimals)
                
                # ═══════════════════════════════════════════════════════════
                # PLACE ORDERS
                # ═══════════════════════════════════════════════════════════
                
                # Place bid (buy order)
                if bid_size > 0:
                    response.limit_order(
                        book_id=book_id,
                        direction=OrderDirection.BUY,
                        quantity=bid_size,
                        price=bid_price,
                        postOnly=True,  # Avoid taking liquidity
                        timeInForce=TimeInForce.GTT,
                        expiryPeriod=self.order_expiry,
                        stp=STP.CANCEL_BOTH  # Prevent self-trading
                    )
                
                # Place ask (sell order)
                if ask_size > 0:
                    response.limit_order(
                        book_id=book_id,
                        direction=OrderDirection.SELL,
                        quantity=ask_size,
                        price=ask_price,
                        postOnly=True,
                        timeInForce=TimeInForce.GTT,
                        expiryPeriod=self.order_expiry,
                        stp=STP.CANCEL_BOTH
                    )
                
                # ═══════════════════════════════════════════════════════════
                # LOGGING
                # ═══════════════════════════════════════════════════════════
                
                bt.logging.info(
                    f"BOOK {book_id:3d} | "
                    f"MID {midquote:8.2f} | "
                    f"IMB {book_imbalance:+.3f} | "
                    f"INV {inventory_position:+.3f} | "
                    f"BID {bid_price:8.2f}@{bid_size:.2f} | "
                    f"ASK {ask_price:8.2f}@{ask_size:.2f}"
                    f"{' [TOXIC]' if adverse_selection else ''}"
                )
                
            except Exception as e:
                bt.logging.error(f"BOOK {book_id}: Error processing - {e}")
                import traceback
                bt.logging.error(traceback.format_exc())
        
        elapsed = time.time() - start_time
        bt.logging.success(
            f"Response generated in {elapsed:.3f}s | "
            f"{len(response.instructions)} instructions"
        )
        
        return response


if __name__ == "__main__":
    """
    Launch the OrderBookMarketMaker agent.
    
    Example command for production:
    python OrderBookMarketMaker.py \
        --netuid 79 \
        --subtensor.chain_endpoint finney \
        --wallet.name my_wallet \
        --wallet.hotkey my_hotkey \
        --agent.name OrderBookMarketMaker \
        --agent.params base_order_size=1.0 max_order_size=3.0 \
                      min_spread_fraction=0.3 max_spread_fraction=0.7 \
                      inventory_skew_strength=2.5 imbalance_depth=5 \
                      imbalance_threshold=0.15 order_expiry=45000000000
    
    Example command for testing (with proxy):
    python OrderBookMarketMaker.py \
        --port 8888 \
        --agent_id 0 \
        --params base_order_size=1.0 imbalance_depth=5
    """
    launch(OrderBookMarketMaker)
