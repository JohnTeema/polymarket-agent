"""
EIP-712 wallet signing + CLOB REST trade execution for Polymarket.
Works on Polygon (USDC) and Base.
Modes: --dry-run (test, no on-chain txn), --live (real money)
"""
import os
import sys
import json
import hashlib
import argparse
import logging
from pathlib import Path
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

sys.path.insert(0, str(Path(__file__).parent.parent))
from engine.ledger import open_position
from engine.polymarket_client import _get, format_market, _parse_double_json
from engine.strategies import load_config, save_config

Config = load_config()

POLYMARKET_CLOB = "https://clob.polymarket.com"
POLYMARKET_CHAIN = {
    "polygon": 137,
    "base": 8453,
}

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def _get_wallet() -> tuple[str | None, str | None, str]:
    cfg = load_config()
    pk = cfg.get("wallet_private_key", "") or os.environ.get("WALLET_PRIVATE_KEY", "")
    addr = cfg.get("wallet_address", "") or os.environ.get("WALLET_ADDRESS", "")
    chain_name = cfg.get("chain", "polygon").lower()
    chain_id = POLYMARKET_CHAIN.get(chain_name, 137)
    return pk, addr, chain_id


def _derive_address(pk: str) -> str:
    acct = Account.from_key(pk)
    return acct.address.lower()


def _get_auth_headers(pk: str, addr: str) -> dict | None:
    """Create EIP-712 authentication headers for Polymarket CLOB API."""
    EIP712_DOMAIN = {
        "name": "Polymarket CLOB",
        "version": "1",
        "chainId": 137,  # Polygon — may need 8453 for Base
    }

    msg = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "Auth": [
                {"name": "address", "type": "address"},
                {"name": "timestamp", "type": "uint64"},
            ],
        },
        "domain": EIP712_DOMAIN,
        "primaryType": "Auth",
        "message": {
            "address": addr,
            "timestamp": int(datetime.timestamp(datetime.utcnow())),
        },
    }

    try:
        signed = Account.sign_typed_data(pk, EIP712_DOMAIN, msg["message"], msg["types"])
        return {
            "POLY_ADDRESS": addr,
            "POLY_SIGNATURE": signed.signature.to_hex(with_prefix=False),
        }
    except Exception as e:
        logger.error(f"Failed to sign auth: {e}")
        return None


def _balance(addr: str, pk: str, chain_id: int) -> float:
    """Read wallet USDC balance from Polygon/Base."""
    cfg = load_config()
    rpc = cfg.get("rpc_url", "")
    if not rpc and chain_id == 137:
        rpc = "https://polygon-rpc.com"
    elif not rpc:
        rpc = f"https://mainnet.base.org"
    try:
        w3 = Web3(Web3.HTTPProvider(rpc))
        token_addr = cfg.get("usdc_address", "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")  # USDC on Polygon
        
        # Minimal ERC-20 ABI for balanceOf
        erc20_abi = '[{"type":"function","name":"balanceOf","inputs":[{"type":"address","name":"account","internalType":"address"}],"outputs":[{"type":"uint256","name":"","internalType":"uint256"}],"stateMutability":"view"}]'
        contract = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=json.loads(erc20_abi))
        bal_wei = contract.functions.balanceOf(Web3.to_checksum_address(addr)).call()
        return w3.from_wei(bal_wei, "mwei")  # USDC has 6 decimals (mwei = 10^-6)
    except Exception as e:
        logger.error(f"Balance check failed: {e}")
        return 0.0


def _sign_order(pk: str, addr: str, side: str, price: float, size: float,
                token_id: str) -> str | None:
    """Sign a single-mint CLOB order (EIP-712)."""
    chain_id = _get_wallet()[2]
    EIP712_DOMAIN = {
        "name": "Polymarket CLOB",
        "version": "1",
        "chainId": chain_id,
    }

    price_encoded = str(int(price * 1000000))  # Micro-prices
    amount = str(int(size))
    nonce = int(Web3.keccak(text=json.dumps({"token_id": token_id, "side": side}))[:20].hex(), 16) % 1_000_000

    msg_dict = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "Order": [
                {"name": "token_id", "type": "bytes32"},
                {"name": "maker", "type": "address"},
                {"name": "price", "type": "int256"},
                {"name": "amount", "type": "uint256"},
                {"name": "side", "type": "uint8"},
                {"name": "nonce", "type": "uint64"},
            ],
        },
        "domain": EIP712_DOMAIN,
        "primaryType": "Order",
        "message": {
            "token_id": token_id,
            "maker": addr,
            "price": price_encoded,
            "amount": amount,
            "side": 0 if side == "buy" else 1,
            "nonce": nonce,
        },
    }

    try:
        signed = Account.sign_typed_data(pk, EIP712_DOMAIN, msg_dict["message"], msg_dict["types"])
        return signed.signature.to_hex(with_prefix=False)
    except Exception as e:
        logger.error(f"Order signing failed: {e}")
        return None


def execute_trade(market: dict, outcome: str, size_usdc: float,
                  dry_run: bool = False) -> dict:
    """
    Execute a live trade on Polymarket CLOB.
    Returns result dict with tx_hash or dry_run flag.
    """
    pk, addr, chain_id = _get_wallet()
    fm = format_market(market)
    token_id = fm["yes_token"] if outcome == "yes" else fm["no_token"]
    entry_price = float(fm["yes_price"]) if outcome == "yes" else float(fm["no_price"])

    cfg = load_config()
    result = {
        "ok": False,
        "mode": "dry-run" if dry_run else "live",
        "market": fm["question"],
        "outcome": outcome,
        "size": size_usdc,
        "price": entry_price,
        "shares": size_usdc / entry_price,
    }

    if dry_run:
        logger.info(f"[DRY RUN] Would execute: {outcome.upper()} ${size_usdc} @ {entry_price*100:.1f}%")
        result["ok"] = True
        result["note"] = "Dry run — no real transaction"
        return result

    if not pk:
        result["error"] = "No wallet_private_key in config"
        logger.error(result["error"])
        return result

    # Sign order
    sig = _sign_order(pk, addr, "buy", entry_price, size_usdc / entry_price, token_id)
    if not sig:
        result["error"] = "Failed to sign order"
        return result

    try:
        # Get auth headers
        auth = _get_auth_headers(pk, addr)
        if not auth:
            result["error"] = "Failed to sign auth"
            return result

        payload = {
            "token_id": token_id,
            "price": entry_price,
            "amount": size_usdc / entry_price,
            "side": "BUY",
            "fee_fraction_bps": 0,
            "nonce": 0,
            "signature": sig,
        }
        headers = {**auth, "Content-Type": "application/json"}

        import urllib.request
        req = urllib.request.Request(
            f"{POLYMARKET_CLOB}/order",
            data=json.dumps(payload).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            order_resp = json.loads(resp.read().decode())

        result["ok"] = True
        result["order_id"] = order_resp.get("order_id")
        result["tx_hash"] = order_resp.get("order_id")
        logger.info(f"Order executed: {result}")
        return result

    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        result["error"] = f"HTTP {e.code}: {body[:200]}"
        logger.error(result["error"])
        return result
    except Exception as e:
        result["error"] = str(e)
        logger.error(result["error"])
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polymarket live trade executor")
    parser.add_argument("--dry-run", action="store_true", help="Test without real txn")
    parser.add_argument("--live", action="store_true", help="Actually execute trade")
    parser.add_argument("--market", type=str, help="Condition ID of market to trade")
    parser.add_argument("--outcome", type=str, choices=["yes", "no"], default="yes")
    parser.add_argument("--size", type=float, default=100.0, help="USD size")
    parser.add_argument("--test-connectivity", action="store_true", help="Test wallet connection")
    args = parser.parse_args()

    if args.test_connectivity:
        pk, addr, chain = _get_wallet()
        if not pk:
            print("❌ No wallet_private_key configured")
            sys.exit(1)
        derived_addr = _derive_address(pk)
        bal = _balance(addr or derived_addr, pk, chain)
        print(f"Wallet address: {addr or derived_addr}")
        print(f"Chain: {chain} ({'Polygon' if chain == 137 else 'Base'})")
        print(f"USDC balance: ${bal:.2f}")

        r = execute_trade(
            {"question": "Test market", "yes_token": "0xtest", "no_token": "0xtest"},
            "yes", 0, dry_run=True
        )
        print(f"Executor status: {'✅ OK' if r['ok'] else '❌ FAIL'} | {r}")
        sys.exit(0 if r["ok"] else 1)

    if not args.market:
        print("Usage: python3 live_executor.py --live --market <condition_id> [--outcome yes|no] [--size 100]")
        print("       python3 live_executor.py --test-connectivity")
        print("       python3 live_executor.py --dry-run --market <condition_id>")
        sys.exit(1)

    # Get market from ledger or Gamma
    from engine.ledger import get_all_positions
    existing = get_all_positions(1000)
    market_data = None
    if existing:
        existing_condition_ids = [p.get("condition_id") for p in existing if p.get("condition_id")]
        if args.market in existing_condition_ids:
            pos = next(p for p in existing if p.get("condition_id") == args.market)
            from engine.polymarket_client import get_market
            market_data = get_market(pos.get("market_id", ""))

    if not market_data:
        # try slug lookup
        from engine.polymarket_client import get_market
        market_data = get_market(args.market)

    if not market_data:
        print(f"Market {args.market} not found in ledger or Gamma API")
        sys.exit(1)

    is_live = args.live and load_config().get("mode", "paper") == "live"
    result = execute_trade(market_data, args.outcome, args.size, dry_run=not is_live)
    print(json.dumps(result, indent=2))
