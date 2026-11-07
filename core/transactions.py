"""
Blockchain data fetching.
- BTC: blockchain.info Haskoin API (direct HTTP)
- ETH: web3.py for balance, Etherscan API for tx history
- Labels: Etherscan page title scraping (free, no API key needed)
"""

import re
import requests
import time
from typing import List, Dict, Optional
from web3 import Web3
from core.config import get_env

# ----------------------------
# Config
# ----------------------------
BTC_BASE = "https://api.blockchain.info/haskoin-store/btc"

# ETH RPC
# Default to a public endpoint that does not require API keys.
# (cloudflare-eth.com no longer serves these requests.)
# Users can override with ETH_RPC_URL in .env.
ETH_RPC = get_env("ETH_RPC_URL", "https://ethereum.publicnode.com")

# Etherscan V2 API — free tier: 5 calls/sec. Get a key at etherscan.io/apis
ETHERSCAN_API = "https://api.etherscan.io/v2/api"
ETHERSCAN_KEY = get_env("ETHERSCAN_API_KEY", "")  # optional, works without but stricter limits
ETHERSCAN_CHAIN_ID = 1  # 1 = Ethereum mainnet
TRONSCAN_KEY = get_env("TRONSCAN_API_KEY", "")

w3 = Web3(Web3.HTTPProvider(ETH_RPC))

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
})

# Minimum delay between Etherscan calls (free tier = 5/sec)
_last_etherscan_call = 0.0
_ETHERSCAN_DELAY = 0.22  # ~4.5 calls/sec to stay safe


def _etherscan_throttle():
    global _last_etherscan_call
    now = time.time()
    elapsed = now - _last_etherscan_call
    if elapsed < _ETHERSCAN_DELAY:
        time.sleep(_ETHERSCAN_DELAY - elapsed)
    _last_etherscan_call = time.time()


# ----------------------------
# Retry helper (for HTTP APIs)
# ----------------------------
def _get_json(
    url: str,
    params: Dict = None,
    retries: int = 5,
    backoff: float = 3.0,
) -> Optional[Dict | List]:
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, params=params, timeout=15)
            if resp.status_code == 429:
                wait = backoff * (attempt + 2)
                print(f"  ⚠ Rate limited, waiting {wait:.0f}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            wait = backoff * (attempt + 1)
            print(f"  ⚠ {url} failed ({e}), retry {attempt+1}/{retries} in {wait:.0f}s")
            time.sleep(wait)
    return None


# ----------------------------
# BTC endpoints (blockchain.info)
# ----------------------------
def get_address_balance(crypto: str, address: str) -> Dict:
    if crypto.lower() == "tron":
        return get_tron_address_balance(address)
    url = f"{BTC_BASE}/address/{address}/balance"
    return _get_json(url) or {}


def get_address_transactions(
    crypto: str,
    address: str,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict]:
    url = f"{BTC_BASE}/address/{address}/transactions"
    return _get_json(url, params={"limit": limit, "offset": offset}) or []


def get_transactions_details(crypto: str, txids: List[str]) -> List[Dict]:
    if not txids:
        return []
    url = f"{BTC_BASE}/transactions"
    return _get_json(url, params={"txids": ",".join(txids)}) or []


# ----------------------------
# ETH: balance via web3, txs via Etherscan
# ----------------------------
def get_eth_address_balance(address: str) -> Dict:
    """Balance + tx count via RPC (no rate limit issues)."""
    try:
        addr = Web3.to_checksum_address(address)
        balance = w3.eth.get_balance(addr)
        tx_count = w3.eth.get_transaction_count(addr)
        return {"balance": balance, "transactionCount": tx_count}
    except Exception as e:
        print(f"  ⚠ web3 balance failed for {address}: {e}")
        # Fallback: Etherscan account balance + latest tx page count estimate.
        # This keeps UI functional when public RPC endpoints intermittently fail.
        _etherscan_throttle()
        params = {
            "chainid": ETHERSCAN_CHAIN_ID,
            "module": "account",
            "action": "balance",
            "address": address,
            "tag": "latest",
        }
        if ETHERSCAN_KEY:
            params["apikey"] = ETHERSCAN_KEY
        data = _get_json(ETHERSCAN_API, params=params) or {}
        if data.get("status") == "1":
            balance = int(data.get("result", 0) or 0)
        else:
            return {"balance": None, "transactionCount": None}

        # Sent-transaction count (nonce) through Etherscan's JSON-RPC proxy.
        _etherscan_throttle()
        params = {
            "chainid": ETHERSCAN_CHAIN_ID,
            "module": "proxy",
            "action": "eth_getTransactionCount",
            "address": address,
            "tag": "latest",
        }
        if ETHERSCAN_KEY:
            params["apikey"] = ETHERSCAN_KEY
        nonce = (_get_json(ETHERSCAN_API, params=params) or {}).get("result")
        try:
            tx_count = int(nonce, 16) if isinstance(nonce, str) and nonce.startswith("0x") else None
        except ValueError:
            tx_count = None
        return {"balance": balance, "transactionCount": tx_count}


def get_eth_address_transactions(
    address: str,
    page: int = 0,
    size: int = 20,
) -> List[Dict]:
    """
    Fetch ETH transaction history via Etherscan API.
    Etherscan uses 1-based pages.
    """
    _etherscan_throttle()

    params = {
        "chainid": ETHERSCAN_CHAIN_ID,
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": 0,
        "endblock": 99999999,
        "page": page + 1,  # Etherscan is 1-indexed
        "offset": size,
        "sort": "desc",
    }
    if ETHERSCAN_KEY:
        params["apikey"] = ETHERSCAN_KEY

    data = _get_json(ETHERSCAN_API, params=params)
    if not data or data.get("status") != "1":
        return []

    results = []
    for tx in data.get("result", []):
        results.append({
            "hash": tx["hash"],
            "from": tx.get("from"),
            "to": tx.get("to"),
            "value": int(tx.get("value", 0)),
            "gasUsed": int(tx.get("gasUsed", 0)),
            "gasPrice": int(tx.get("gasPrice", 0)),
            "blockNumber": int(tx.get("blockNumber", 0)),
            "timestamp": int(tx.get("timeStamp", 0)),
            "success": tx.get("txreceipt_status") == "1",
            "type": "EXTERNAL",
        })

    return results


# ----------------------------
# Address labels (Etherscan page scraping)
# ----------------------------
def get_arkham_address_intelligence(address: str) -> Optional[Dict]:
    """
    Get address label by scraping Etherscan's page title.
    Title format: 'Label Name | Address: 0x... | Etherscan'
    Unlabeled:    'Address 0x... | Etherscan'
    """
    try:
        resp = SESSION.get(
            f"https://etherscan.io/address/{address}",
            timeout=10,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return {"entity": None, "wallet": None, "tags": []}

        m = re.search(r"<title>(.*?)</title>", resp.text, re.DOTALL)
        if not m:
            return {"entity": None, "wallet": None, "tags": []}

        title = m.group(1).strip()
        parts = [p.strip() for p in title.split("|")]

        # 3+ parts means there's a label: "Binance 14 | Address: 0x... | Etherscan"
        if len(parts) >= 3:
            entity = parts[0]
            # Try to split "Circle: USDC Token" into entity + wallet type
            if ":" in entity:
                name, wtype = entity.split(":", 1)
                return {"entity": name.strip(), "wallet": wtype.strip(), "tags": []}
            return {"entity": entity, "wallet": None, "tags": []}

        return {"entity": None, "wallet": None, "tags": []}

    except Exception as e:
        print(f"  ⚠ Label fetch failed for {address}: {e}")
        return {"entity": None, "wallet": None, "tags": []}


def close_browser():
    """No-op, kept for backward compatibility."""
    pass


def get_tron_address_balance(address: str) -> Dict:
    """Fetch basic Tron balance and tx count from TronScan."""
    headers = {"TRON-PRO-API-KEY": TRONSCAN_KEY} if TRONSCAN_KEY else {}
    try:
        resp = SESSION.get(
            "https://apilist.tronscanapi.com/api/accountv2",
            params={"address": address},
            headers=headers,
            timeout=15,
        )
        data = resp.json() if resp.status_code == 200 else {}
    except Exception:
        data = {}
    # accountv2 may return amounts in sun.
    if not data:
        return {"balance": None, "transactionCount": None}
    balance = int(data.get("balance", 0) or 0)
    tx_count = int(data.get("transactions_out", 0) or data.get("totalTransactionCount", 0) or 0)
    return {"balance": balance, "transactionCount": tx_count}


def get_tron_address_transactions(address: str, limit: int = 50, offset: int = 0) -> List[Dict]:
    """Fetch outgoing+incoming Tron txs normalized to Orbit shape."""
    params = {
        "sort": "timestamp",
        "count": "true",
        "limit": max(1, min(limit, 200)),
        "start": max(0, offset),
        "address": address,
    }
    headers = {"TRON-PRO-API-KEY": TRONSCAN_KEY} if TRONSCAN_KEY else {}
    try:
        resp = SESSION.get("https://apilist.tronscanapi.com/api/transaction", params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return []
        payload = resp.json() or {}
        out = []
        for tx in payload.get("data", []):
            owner = tx.get("ownerAddress") or tx.get("owner_address") or ""
            to_addr = tx.get("toAddress") or tx.get("to_address") or ""
            token_info = tx.get("tokenInfo") or {}
            token = token_info.get("tokenAbbr") or token_info.get("symbol") or "TRX"
            decimals = int(token_info.get("tokenDecimal") or 6)
            amount = tx.get("amount")
            if amount is None:
                amount = (tx.get("contractData") or {}).get("amount", 0)
            out.append({
                "hash": tx.get("hash") or tx.get("txID") or tx.get("txid") or "",
                "from": owner,
                "to": to_addr,
                "value": int(amount or 0),
                "gasUsed": 0,
                "gasPrice": 0,
                "blockNumber": int(tx.get("block") or 0),
                "timestamp": int(tx.get("timestamp") or 0) // 1000,
                "success": True,
                "type": token,
                "decimals": decimals,
                "chain": "tron",
            })
        return out
    except Exception:
        return []
