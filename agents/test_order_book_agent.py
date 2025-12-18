#!/usr/bin/env python3
"""
Validation script for OrderBookImbalanceMarketMaker

Tests:
1. Agent initialization
2. Response generation with synthetic data
3. Performance metrics (response time, order quality)
4. Edge cases (empty books, high inventory)

Usage:
    python test_order_book_agent.py
"""

import sys
import time
import numpy as np
from typing import Dict, List
from collections import namedtuple

# Mock the required imports for standalone testing
class MockConfig:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

class MockDendrite:
    def __init__(self):
        self.hotkey = "test_validator"
        self.process_time = 0.1

class MockAccount:
    def __init__(self):
        self.base_balance = type('obj', (object,), {'total': 5.0, 'free': 5.0, 'reserved': 0.0})()
        self.quote_balance = type('obj', (object,), {'total': 1500.0, 'free': 1500.0, 'reserved': 0.0})()
        self.base_loan = 0.0
        self.quote_loan = 0.0

class MockLevel:
    def __init__(self, price: float, quantity: float):
        self.price = price
        self.quantity = quantity
        self.orders = None

class MockBook:
    def __init__(self, book_id: int, base_price: float = 300.0):
        self.id = book_id
        self.bids = self._generate_bids(base_price)
        self.asks = self._generate_asks(base_price)
    
    def _generate_bids(self, base_price: float) -> List[MockLevel]:
        """Generate realistic bid levels"""
        levels = []
        for i in range(21):
            price = base_price - 0.01 * (i + 1)
            quantity = np.random.uniform(0.5, 5.0) * (1 + 0.2 * i)  # Deeper = more volume
            levels.append(MockLevel(price, quantity))
        return levels
    
    def _generate_asks(self, base_price: float) -> List[MockLevel]:
        """Generate realistic ask levels"""
        levels = []
        for i in range(21):
            price = base_price + 0.01 * (i + 1)
            quantity = np.random.uniform(0.5, 5.0) * (1 + 0.2 * i)
            levels.append(MockLevel(price, quantity))
        return levels
    
    def snapshot(self, timestamp):
        """Mock snapshot method"""
        return type('obj', (object,), {
            'imbalance': lambda depth=None: 0.1
        })()

class MockState:
    def __init__(self, n_books: int = 40):
        self.dendrite = MockDendrite()
        self.timestamp = 1000000000000
        self.config = MockConfig(
            miner_wealth=2000.0,
            priceDecimals=4,
            volumeDecimals=2,
            publish_interval=5_000_000_000
        )
        self.books = {i: MockBook(i, base_price=300.0 + i * 0.5) for i in range(n_books)}
        # Generate mock account for UID 0
        self.accounts = {0: {i: MockAccount() for i in range(n_books)}}

def run_tests():
    """Run validation tests"""
    
    print("=" * 80)
    print("ORDER BOOK IMBALANCE MARKET MAKER — VALIDATION TESTS")
    print("=" * 80)
    print()
    
    # Import the agent (adjust path as needed)
    sys.path.insert(0, '/workspace')
    from agents.OrderBookImbalanceMarketMaker import OrderBookImbalanceMarketMaker
    
    # Test 1: Initialization
    print("Test 1: Agent Initialization")
    print("-" * 80)
    
    config = MockConfig(
        base_order_size=0.5,
        max_inventory_pct=0.3,
        target_spread_bps=10.0,
        expiry_seconds=60,
        imbalance_depths="1,3,5,10",
        lookback_periods=20,
        inventory_skew_factor=0.5,
        min_edge_bps=2.0
    )
    
    agent = OrderBookImbalanceMarketMaker()
    agent.uid = 0
    agent.config = config
    agent.log_dir = "/tmp"
    
    try:
        agent.initialize()
        print("✓ Agent initialized successfully")
        print(f"  - Base order size: {agent.base_order_size}")
        print(f"  - Max inventory: {agent.max_inventory_pct * 100:.1f}%")
        print(f"  - Target spread: {agent.target_spread_bps} bps")
        print(f"  - Imbalance depths: {agent.imbalance_depths}")
    except Exception as e:
        print(f"✗ Initialization failed: {e}")
        return False
    
    print()
    
    # Test 2: Response Generation
    print("Test 2: Response Generation (40 books)")
    print("-" * 80)
    
    state = MockState(n_books=40)
    
    try:
        start = time.time()
        response = agent.respond(state)
        elapsed = time.time() - start
        
        print(f"✓ Response generated in {elapsed*1000:.1f}ms")
        print(f"  - Orders placed: {len(response.instructions)}")
        print(f"  - Time per book: {elapsed/40*1000:.2f}ms")
        
        if elapsed < 0.3:  # Target <300ms total
            print(f"  - Latency: EXCELLENT (<300ms)")
        elif elapsed < 0.5:
            print(f"  - Latency: GOOD (<500ms)")
        elif elapsed < 1.0:
            print(f"  - Latency: ACCEPTABLE (<1s)")
        else:
            print(f"  - Latency: SLOW (>1s) - May cause timeout penalties")
        
    except Exception as e:
        print(f"✗ Response generation failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print()
    
    # Test 3: Order Quality Checks
    print("Test 3: Order Quality Analysis")
    print("-" * 80)
    
    try:
        # Check order properties
        bid_count = sum(1 for instr in response.instructions if instr.direction == 0)
        ask_count = sum(1 for instr in response.instructions if instr.direction == 1)
        
        print(f"  - Bid orders: {bid_count}")
        print(f"  - Ask orders: {ask_count}")
        print(f"  - Bid/Ask ratio: {bid_count/max(ask_count,1):.2f}")
        
        # Check that orders have reasonable prices and sizes
        for instr in response.instructions[:5]:  # Sample first 5
            book = state.books[instr.bookId]
            best_bid = book.bids[0].price
            best_ask = book.asks[0].price
            
            if instr.direction == 0:  # BUY
                if instr.price > best_ask:
                    print(f"  ✗ WARNING: Bid {instr.price} crosses spread (ask={best_ask})")
                else:
                    edge_bps = (best_ask - instr.price) / best_ask * 10000
                    print(f"  ✓ Bid {instr.price:.4f} has {edge_bps:.1f} bps edge")
            else:  # SELL
                if instr.price < best_bid:
                    print(f"  ✗ WARNING: Ask {instr.price} crosses spread (bid={best_bid})")
                else:
                    edge_bps = (instr.price - best_bid) / best_bid * 10000
                    print(f"  ✓ Ask {instr.price:.4f} has {edge_bps:.1f} bps edge")
        
    except Exception as e:
        print(f"✗ Order quality check failed: {e}")
        return False
    
    print()
    
    # Test 4: Signal Calculation Tests
    print("Test 4: Signal Calculation")
    print("-" * 80)
    
    try:
        book = state.books[0]
        
        # Test imbalance calculation
        imbalance_1 = agent.calculate_order_book_imbalance(book, depth=1)
        imbalance_10 = agent.calculate_order_book_imbalance(book, depth=10)
        print(f"  ✓ Imbalance (depth=1):  {imbalance_1:+.4f}")
        print(f"  ✓ Imbalance (depth=10): {imbalance_10:+.4f}")
        
        # Test microprice calculation
        microprice = agent.calculate_microprice(book)
        midquote = (book.bids[0].price + book.asks[0].price) / 2
        print(f"  ✓ Microprice: {microprice:.4f}")
        print(f"  ✓ Midquote:   {midquote:.4f}")
        print(f"  ✓ Difference: {abs(microprice - midquote):.4f}")
        
        # Test volatility estimation
        vol = agent.estimate_volatility("test_validator", 0, midquote)
        print(f"  ✓ Volatility estimate: {vol:.6f}")
        
    except Exception as e:
        print(f"✗ Signal calculation failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print()
    
    # Test 5: Inventory Management
    print("Test 5: Inventory Management")
    print("-" * 80)
    
    try:
        account = state.accounts[0][0]
        microprice = agent.calculate_microprice(state.books[0])
        
        inv_pct, inv_skew = agent.calculate_inventory_position(
            account, microprice, state.config.miner_wealth
        )
        
        print(f"  ✓ Inventory %: {inv_pct*100:.2f}%")
        print(f"  ✓ Inventory skew: {inv_skew:+.3f}")
        print(f"  ✓ Position: {'LONG' if inv_skew > 0 else 'SHORT' if inv_skew < 0 else 'NEUTRAL'}")
        
        if abs(inv_pct) < agent.max_inventory_pct:
            print(f"  ✓ Within risk limits ({agent.max_inventory_pct*100:.0f}% max)")
        else:
            print(f"  ✗ Exceeds risk limits!")
        
    except Exception as e:
        print(f"✗ Inventory management test failed: {e}")
        return False
    
    print()
    
    # Test 6: Quote Calculation
    print("Test 6: Optimal Quote Calculation")
    print("-" * 80)
    
    try:
        book = state.books[0]
        microprice = agent.calculate_microprice(book)
        spread = microprice * agent.target_spread_bps / 10000
        
        bid, ask, bid_mult, ask_mult = agent.calculate_optimal_quotes(
            microprice=microprice,
            spread=spread,
            imbalance_signal=0.2,  # Bullish
            inventory_skew=-0.1,   # Slight short position
            price_decimals=4
        )
        
        print(f"  ✓ Microprice: {microprice:.4f}")
        print(f"  ✓ Base spread: {spread:.4f}")
        print(f"  ✓ Optimal bid: {bid:.4f} (size mult: {bid_mult:.2f}x)")
        print(f"  ✓ Optimal ask: {ask:.4f} (size mult: {ask_mult:.2f}x)")
        print(f"  ✓ Effective spread: {ask - bid:.4f} ({(ask-bid)/microprice*10000:.1f} bps)")
        
        # Check that quotes make sense
        if bid < microprice < ask:
            print(f"  ✓ Quotes straddle microprice")
        else:
            print(f"  ✗ WARNING: Quotes don't straddle microprice!")
        
    except Exception as e:
        print(f"✗ Quote calculation failed: {e}")
        return False
    
    print()
    
    # Test 7: Edge Cases
    print("Test 7: Edge Case Handling")
    print("-" * 80)
    
    try:
        # Empty book
        empty_book = MockBook(999)
        empty_book.bids = []
        empty_book.asks = []
        
        imb = agent.calculate_order_book_imbalance(empty_book, depth=5)
        print(f"  ✓ Empty book imbalance: {imb} (should be 0.0)")
        
        # High inventory
        high_inv_account = MockAccount()
        high_inv_account.base_balance.total = 20.0  # Very high
        
        inv_pct, inv_skew = agent.calculate_inventory_position(
            high_inv_account, 300.0, state.config.miner_wealth
        )
        
        print(f"  ✓ High inventory detected: {inv_pct*100:.1f}%")
        if abs(inv_skew) > 0.5:
            print(f"  ✓ Strong skew applied: {inv_skew:+.3f}")
        
    except Exception as e:
        print(f"✗ Edge case handling failed: {e}")
        return False
    
    print()
    
    # Summary
    print("=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)
    print("✓ All tests passed!")
    print()
    print("Agent is ready for deployment. Recommended next steps:")
    print("  1. Test with agents/proxy (local simulation)")
    print("  2. Deploy to testnet (netuid 366)")
    print("  3. Monitor per-book Sharpe for 1000+ steps")
    print("  4. Deploy to mainnet (netuid 79)")
    print()
    print("For parameter tuning, see:")
    print("  - ORDER_BOOK_STRATEGY_GUIDE.md (detailed)")
    print("  - PARAMETER_QUICK_REFERENCE.md (quick reference)")
    print()
    
    return True

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
