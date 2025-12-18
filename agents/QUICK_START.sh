#!/bin/bash
# Quick Start Script for OrderBookMarketMaker
# This script helps you deploy the agent with pre-configured settings

set -e

echo "════════════════════════════════════════════════════════════════════"
echo "  OrderBookMarketMaker - Quick Start Deployment"
echo "════════════════════════════════════════════════════════════════════"
echo ""

# Color codes for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if we're in the right directory
if [ ! -f "agents/OrderBookMarketMaker.py" ]; then
    print_error "Please run this script from the sn-79 root directory"
    exit 1
fi

print_info "Script started successfully"
echo ""

# Menu selection
echo "Select deployment environment:"
echo "  1) Local Testing (Proxy Simulator)"
echo "  2) Testnet (netuid 366)"
echo "  3) Mainnet Conservative (netuid 79)"
echo "  4) Mainnet Balanced (netuid 79)"
echo "  5) Mainnet Aggressive (netuid 79)"
echo ""

read -p "Enter choice [1-5]: " choice

case $choice in
    1)
        print_info "Deploying to Local Testing Environment"
        echo ""
        read -p "Enter port (default 8888): " port
        port=${port:-8888}
        read -p "Enter agent_id (default 0): " agent_id
        agent_id=${agent_id:-0}
        
        print_info "Starting local deployment..."
        python3 agents/OrderBookMarketMaker.py \
            --port $port \
            --agent_id $agent_id \
            --params \
                base_order_size=1.0 \
                max_order_size=3.0 \
                min_spread_fraction=0.30 \
                max_spread_fraction=0.70 \
                max_inventory_fraction=0.30 \
                inventory_skew_strength=2.0 \
                imbalance_lookback=5 \
                imbalance_depth=5 \
                imbalance_threshold=0.15 \
                trade_imbalance_threshold=0.30 \
                toxic_flow_penalty=2.0 \
                order_expiry=60000000000
        ;;
    
    2)
        print_info "Deploying to Testnet (netuid 366)"
        echo ""
        print_warning "Make sure you have testnet TAO in your wallet!"
        echo ""
        read -p "Enter wallet name: " wallet_name
        read -p "Enter hotkey name: " hotkey_name
        
        if [ -z "$wallet_name" ] || [ -z "$hotkey_name" ]; then
            print_error "Wallet name and hotkey name are required"
            exit 1
        fi
        
        print_info "Starting testnet deployment..."
        python3 agents/OrderBookMarketMaker.py \
            --netuid 366 \
            --subtensor.chain_endpoint test \
            --wallet.name $wallet_name \
            --wallet.hotkey $hotkey_name \
            --agent.name OrderBookMarketMaker \
            --agent.params \
                base_order_size=0.5 \
                max_order_size=2.0 \
                min_spread_fraction=0.35 \
                max_spread_fraction=0.75 \
                max_inventory_fraction=0.25 \
                inventory_skew_strength=2.0 \
                imbalance_lookback=5 \
                imbalance_depth=5 \
                imbalance_threshold=0.15 \
                trade_imbalance_threshold=0.30 \
                toxic_flow_penalty=2.0 \
                order_expiry=60000000000
        ;;
    
    3)
        print_info "Deploying to Mainnet (Conservative)"
        echo ""
        print_warning "This will use REAL TAO. Make sure you're ready!"
        read -p "Continue? (yes/no): " confirm
        if [ "$confirm" != "yes" ]; then
            print_info "Deployment cancelled"
            exit 0
        fi
        echo ""
        read -p "Enter wallet name: " wallet_name
        read -p "Enter hotkey name: " hotkey_name
        
        if [ -z "$wallet_name" ] || [ -z "$hotkey_name" ]; then
            print_error "Wallet name and hotkey name are required"
            exit 1
        fi
        
        print_info "Starting mainnet deployment (Conservative)..."
        python3 agents/OrderBookMarketMaker.py \
            --netuid 79 \
            --subtensor.chain_endpoint finney \
            --wallet.name $wallet_name \
            --wallet.hotkey $hotkey_name \
            --agent.name OrderBookMarketMaker \
            --agent.params \
                base_order_size=0.5 \
                max_order_size=2.0 \
                min_spread_fraction=0.35 \
                max_spread_fraction=0.75 \
                max_inventory_fraction=0.25 \
                inventory_skew_strength=2.0 \
                imbalance_lookback=5 \
                imbalance_depth=5 \
                imbalance_threshold=0.15 \
                trade_imbalance_threshold=0.30 \
                toxic_flow_penalty=2.0 \
                order_expiry=60000000000
        ;;
    
    4)
        print_info "Deploying to Mainnet (Balanced)"
        echo ""
        print_warning "This will use REAL TAO. Make sure you're ready!"
        read -p "Continue? (yes/no): " confirm
        if [ "$confirm" != "yes" ]; then
            print_info "Deployment cancelled"
            exit 0
        fi
        echo ""
        read -p "Enter wallet name: " wallet_name
        read -p "Enter hotkey name: " hotkey_name
        
        if [ -z "$wallet_name" ] || [ -z "$hotkey_name" ]; then
            print_error "Wallet name and hotkey name are required"
            exit 1
        fi
        
        print_info "Starting mainnet deployment (Balanced)..."
        python3 agents/OrderBookMarketMaker.py \
            --netuid 79 \
            --subtensor.chain_endpoint finney \
            --wallet.name $wallet_name \
            --wallet.hotkey $hotkey_name \
            --agent.name OrderBookMarketMaker \
            --agent.params \
                base_order_size=1.0 \
                max_order_size=3.0 \
                min_spread_fraction=0.30 \
                max_spread_fraction=0.70 \
                max_inventory_fraction=0.30 \
                inventory_skew_strength=2.5 \
                imbalance_lookback=5 \
                imbalance_depth=5 \
                imbalance_threshold=0.12 \
                trade_imbalance_threshold=0.30 \
                toxic_flow_penalty=2.5 \
                order_expiry=45000000000
        ;;
    
    5)
        print_info "Deploying to Mainnet (Aggressive)"
        echo ""
        print_warning "This is HIGH RISK configuration. Only use if experienced!"
        print_warning "This will use REAL TAO. Make sure you're ready!"
        read -p "Continue? (yes/no): " confirm
        if [ "$confirm" != "yes" ]; then
            print_info "Deployment cancelled"
            exit 0
        fi
        echo ""
        read -p "Enter wallet name: " wallet_name
        read -p "Enter hotkey name: " hotkey_name
        
        if [ -z "$wallet_name" ] || [ -z "$hotkey_name" ]; then
            print_error "Wallet name and hotkey name are required"
            exit 1
        fi
        
        print_info "Starting mainnet deployment (Aggressive)..."
        python3 agents/OrderBookMarketMaker.py \
            --netuid 79 \
            --subtensor.chain_endpoint finney \
            --wallet.name $wallet_name \
            --wallet.hotkey $hotkey_name \
            --agent.name OrderBookMarketMaker \
            --agent.params \
                base_order_size=1.5 \
                max_order_size=4.0 \
                min_spread_fraction=0.25 \
                max_spread_fraction=0.65 \
                max_inventory_fraction=0.35 \
                inventory_skew_strength=3.0 \
                imbalance_lookback=5 \
                imbalance_depth=5 \
                imbalance_threshold=0.10 \
                trade_imbalance_threshold=0.35 \
                toxic_flow_penalty=3.0 \
                order_expiry=30000000000
        ;;
    
    *)
        print_error "Invalid choice"
        exit 1
        ;;
esac

print_info "Deployment initiated!"
echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "  Monitor your agent's performance carefully!"
echo "  Review the tuning guide for optimization tips."
echo "════════════════════════════════════════════════════════════════════"
