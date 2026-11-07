"""
Investigation tools for Orbit.
- Search Out Transaction: find matching outgoing txs by USD value.
"""

import requests
import time
import hashlib
from typing import List, Dict, Optional, Tuple
from core.config import get_env

ETHERSCAN_API = "https://api.etherscan.io/v2/api"
ETHERSCAN_KEY = get_env("ETHERSCAN_API_KEY", "")
CHAIN_ID = 1
TRONSCAN_KEY = get_env("TRONSCAN_API_KEY", "")

# Etherscan V2 supported chains — chainid: (name, native_token)
EVM_CHAINS = {
    1:     ("ethereum", "ETH"),
    56:    ("bsc", "BNB"),
    137:   ("polygon", "MATIC"),
    42161: ("arbitrum", "ETH"),
    10:    ("optimism", "ETH"),
    8453:  ("base", "ETH"),
    43114: ("avalanche", "AVAX"),
    250:   ("fantom", "FTM"),
    59144: ("linea", "ETH"),
    324:   ("zksync", "ETH"),
    1101:  ("polygon-zkevm", "ETH"),
    5000:  ("mantle", "MNT"),
    534352:("scroll", "ETH"),
    81457: ("blast", "ETH"),
    100:   ("gnosis", "xDAI"),
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
})

_last_call = 0.0


def _tron_hex_to_base58(addr_hex: str) -> Optional[str]:
    if not addr_hex:
        return None
    h = addr_hex.lower().strip()
    if h.startswith("0x"):
        h = h[2:]
    if len(h) != 42 or not h.startswith("41"):
        return None
    try:
        raw = bytes.fromhex(h)
    except ValueError:
        return None
    chk = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(raw + chk, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = alphabet[r] + out
    return out or None


def _normalize_tron_address(address: str) -> str:
    a = (address or "").strip()
    if a.startswith("T"):
        return a
    return _tron_hex_to_base58(a) or a


def _throttle():
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < 0.35:
        time.sleep(0.35 - elapsed)
    _last_call = time.time()


def _es_get(params: Dict, chain_id: int = 1) -> Optional[Dict]:
    _throttle()
    params["chainid"] = chain_id
    if ETHERSCAN_KEY:
        params["apikey"] = ETHERSCAN_KEY
    try:
        r = SESSION.get(ETHERSCAN_API, params=params, timeout=10)
        return r.json()
    except Exception as e:
        print(f"  Etherscan error ({chain_id}): {e}")
        return None


def _check_chain_activity(address: str, chain_id: int) -> bool:
    """Quick check if an address has any txs on a chain. Single API call."""
    data = _es_get({
        "module": "account", "action": "txlist", "address": address,
        "startblock": 0, "endblock": 99999999,
        "page": 1, "offset": 1, "sort": "desc",
    }, chain_id=chain_id)
    if data and data.get("status") == "1" and data.get("result"):
        return len(data["result"]) > 0
    return False


_price_cache = {}  # cache_key -> usd price


CRYPTOCOMPARE_KEY = get_env("CRYPTOCOMPARE_API_KEY", "")


def _price_cryptocompare(symbol: str, timestamp: int) -> Optional[float]:
    """Hourly close from CryptoCompare. Their API now requires a key."""
    if not CRYPTOCOMPARE_KEY:
        return None
    try:
        r = SESSION.get(
            "https://min-api.cryptocompare.com/data/v2/histohour",
            params={"fsym": symbol, "tsym": "USD", "limit": 1, "toTs": timestamp, "api_key": CRYPTOCOMPARE_KEY},
            timeout=10,
        )
        prices = r.json().get("Data", {}).get("Data", []) if r.status_code == 200 else []
        return (prices[-1].get("close") or None) if prices else None
    except Exception:
        return None


def _price_binance(symbol: str, timestamp: int) -> Optional[float]:
    """Close of the 1h Binance candle (SYMBOL/USDT) that contains the timestamp. No key needed."""
    try:
        hour_ms = (timestamp // 3600) * 3600 * 1000
        r = SESSION.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": f"{symbol}USDT", "interval": "1h", "startTime": hour_ms, "limit": 1},
            timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        return float(rows[0][4]) if rows else None
    except Exception:
        return None


def _price_coinbase(symbol: str, timestamp: int) -> Optional[float]:
    """Close of the 1h Coinbase candle (SYMBOL-USD) that contains the timestamp. No key needed."""
    try:
        start = (timestamp // 3600) * 3600
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        r = SESSION.get(
            f"https://api.exchange.coinbase.com/products/{symbol}-USD/candles",
            params={"granularity": 3600, "start": iso(start), "end": iso(start + 3600)},
            timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        # Rows are [time, low, high, open, close, volume], newest first.
        rows = [row for row in rows if isinstance(row, list) and row[0] == start] or rows
        return float(rows[0][4]) if rows and isinstance(rows[0], list) else None
    except Exception:
        return None


def _usd_price(symbol: str, timestamp: int) -> Optional[float]:
    """USD price of a symbol at a timestamp (hourly). Tries CryptoCompare (if a key
    is configured), then Binance, then Coinbase. Results are cached per hour."""
    symbol = symbol.upper()
    cache_key = f"{symbol}_{(timestamp // 3600) * 3600}"
    if cache_key in _price_cache:
        return _price_cache[cache_key]
    for source in (_price_cryptocompare, _price_binance, _price_coinbase):
        price = source(symbol, timestamp)
        if price:
            _price_cache[cache_key] = price
            return price
    return None


def get_eth_price_at_time(timestamp: int) -> Optional[float]:
    """Get ETH/USD price at a timestamp (hourly)."""
    price = _usd_price("ETH", timestamp)
    if price:
        print(f"  ETH price at {time.strftime('%Y-%m-%d %H:%M', time.gmtime(timestamp))}: ${price:.2f}")
    return price


def _get_token_price(symbol: str, timestamp: int) -> Optional[float]:
    """Get USD price for a token at a given timestamp."""
    symbol = symbol.upper()
    stablecoins = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "GUSD", "FRAX"}
    if symbol in stablecoins:
        return 1.0
    return _usd_price(symbol, timestamp)


def get_address_label(address: str) -> Optional[str]:
    """Get entity label from Etherscan page title."""
    import re
    try:
        url = f"https://etherscan.io/address/{address}"
        req = requests.Request("GET", url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        prepared = SESSION.prepare_request(req)
        resp = SESSION.send(prepared, timeout=10)
        m = re.search(r"<title>(.*?)</title>", resp.text, re.DOTALL)
        if not m:
            return None
        title = m.group(1).strip()
        parts = [p.strip() for p in title.split("|")]
        if len(parts) >= 3:
            entity = parts[0]
            if ":" in entity:
                return entity.split(":")[0].strip()
            return entity
        return None
    except:
        return None


def find_related_wallets(address: str, entity_name: Optional[str] = None, known_nodes: Optional[Dict] = None) -> List[str]:
    """
    Find wallets related to the same entity using Etherscan's search.
    E.g. "FixedFloat" → FixedFloat 1, FixedFloat 2, etc.
    """
    wallets = [address.lower()]

    if not entity_name:
        return wallets

    # Normalize: "FixedFloat 1" → "FixedFloat", "Binance 14" → "Binance"
    base_entity = entity_name.split()[0] if entity_name else None
    if not base_entity or len(base_entity) < 3:
        return wallets

    # Method 1: Etherscan search handler
    try:
        _throttle()
        r = SESSION.get(
            f"https://etherscan.io/searchHandler?term={base_entity}&filterby=0",
            timeout=10,
        )
        if r.status_code == 200:
            results = r.json()
            for item in results:
                title = item.get("title", "")
                addr = item.get("address", "").lower()
                # Only include if same base entity
                if addr and title.lower().startswith(base_entity.lower()) and addr not in wallets:
                    wallets.append(addr)
            if len(wallets) > 1:
                print(f"  Found {len(wallets)} {base_entity} wallets via Etherscan search")
    except Exception as e:
        print(f"  Etherscan search error: {e}")

    # Method 2: check known nodes for same base entity
    if known_nodes:
        for addr, nd in known_nodes.items():
            ent = nd.get("entity") or ""
            if ent.split()[0].lower() == base_entity.lower() and addr.lower() not in wallets:
                wallets.append(addr.lower())

    return wallets


def _timestamp_to_block(timestamp: int, closest: str = "before", chain_id: int = 1) -> Optional[int]:
    """Convert a unix timestamp to the nearest block number via Etherscan."""
    data = _es_get({
        "module": "block",
        "action": "getblocknobytime",
        "timestamp": timestamp,
        "closest": closest,
    }, chain_id=chain_id)
    if data and data.get("status") == "1":
        try:
            return int(data["result"])
        except (ValueError, KeyError):
            pass
    return None


def fetch_outgoing_txs(
    address: str,
    start_time: int,
    end_time: int,
    chain_id: int = 1,
    start_block: int = 0,
) -> List[Dict]:
    """Fetch outgoing ETH + ERC-20 token transfers from an address in a time window on any EVM chain."""
    chain_name, native_token = EVM_CHAINS.get(chain_id, ("unknown", "ETH"))

    if start_block == 0:
        sb = _timestamp_to_block(start_time, "before", chain_id)
        if sb:
            start_block = sb

    end_block = _timestamp_to_block(end_time, "after", chain_id)
    if not end_block:
        end_block = 99999999

    all_txs = []

    # 1. Native transfers
    for page in range(1, 20):
        data = _es_get({
            "module": "account", "action": "txlist", "address": address,
            "startblock": start_block, "endblock": end_block,
            "page": page, "offset": 100, "sort": "asc",
        }, chain_id=chain_id)
        if not data or data.get("status") != "1":
            break
        results = data.get("result", [])
        if not results:
            break
        done = False
        for tx in results:
            ts = int(tx.get("timeStamp", 0))
            if ts > end_time:
                done = True; break
            if ts < start_time:
                continue
            if tx.get("from", "").lower() == address.lower() and int(tx.get("value", 0)) > 0:
                all_txs.append({
                    "hash": tx["hash"], "from": tx["from"].lower(),
                    "to": (tx.get("to") or "").lower(),
                    "value": int(tx["value"]),
                    "time": ts, "block": int(tx.get("blockNumber", 0)),
                    "success": tx.get("txreceipt_status") == "1",
                    "token": native_token, "decimals": 18, "chain": chain_name,
                })
        if done or len(results) < 100:
            break

    # 2. Token transfers (ERC-20)
    for page in range(1, 20):
        data = _es_get({
            "module": "account", "action": "tokentx", "address": address,
            "startblock": start_block, "endblock": end_block,
            "page": page, "offset": 100, "sort": "asc",
        }, chain_id=chain_id)
        if not data or data.get("status") != "1":
            break
        results = data.get("result", [])
        if not results:
            break
        done = False
        for tx in results:
            ts = int(tx.get("timeStamp", 0))
            if ts > end_time:
                done = True; break
            if ts < start_time:
                continue
            if tx.get("from", "").lower() == address.lower():
                all_txs.append({
                    "hash": tx["hash"], "from": tx["from"].lower(),
                    "to": (tx.get("to") or "").lower(),
                    "value": int(tx.get("value", 0)),
                    "time": ts, "block": int(tx.get("blockNumber", 0)),
                    "success": True,
                    "token": tx.get("tokenSymbol", "?"),
                    "decimals": int(tx.get("tokenDecimal", 18)),
                    "chain": chain_name,
                })
        if done or len(results) < 100:
            break

    return all_txs


def fetch_tron_outgoing_txs(
    address: str,
    start_time: int,
    end_time: int,
) -> List[Dict]:
    """Fetch outgoing TRC-20 + TRX transfers from a Tron address."""
    def _tron_norm(addr: str) -> str:
        """Normalize Tron address into base58 (T...) when possible."""
        if not addr:
            return ""
        a = addr.strip()
        if a.startswith("T"):
            return a
        b58 = _normalize_tron_address(a)
        return b58 or a

    all_txs = []
    start_ms = start_time * 1000
    end_ms = end_time * 1000
    tron_headers = {"TRON-PRO-API-KEY": TRONSCAN_KEY} if TRONSCAN_KEY else {}
    address_norm = _tron_norm(address)

    _txinfo_cache: Dict[str, Dict] = {}

    def _tronscan_tx_info(tx_hash: str) -> Optional[Dict]:
        if not tx_hash:
            return None
        if tx_hash in _txinfo_cache:
            return _txinfo_cache[tx_hash]
        try:
            # Keep this small; search windows can require many lookups.
            time.sleep(0.06)
            r = SESSION.get(
                "https://apilist.tronscanapi.com/api/transaction-info",
                params={"hash": tx_hash},
                headers=tron_headers,
                timeout=15,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            if isinstance(data, dict):
                _txinfo_cache[tx_hash] = data
            return data
        except Exception:
            return None

    # Preferred for TRC-20: TronScan transfer index (reliable for token transfers).
    # NOTE: querying by `fromAddress` returns transfers sent by this wallet.
    try:
        base_url = "https://apilist.tronscanapi.com/api/token_trc20/transfers"
        start = 0
        # Newest-first pagination so we can stop once we pass start_time.
        rate_limit_hits = 0
        while start <= 5000:  # hard safety cap
            params = {
                "fromAddress": address_norm,
                "start_timestamp": start_ms,
                "end_timestamp": end_ms,
                "start": start,
                "limit": 20,
                "confirm": "true",
            }
            # Handle rate limiting with bounded retries.
            r = None
            for attempt in range(5):
                # TronScan can be aggressive with rate limits; keep a conservative cadence.
                time.sleep(0.6 + attempt * 0.4)
                r = SESSION.get(base_url, params=params, headers=tron_headers, timeout=15)
                if r.status_code != 429:
                    break
            if r and r.status_code == 429:
                rate_limit_hits += 1
                if rate_limit_hits >= 10:
                    break
                time.sleep(3.0)
                continue
            if not r or r.status_code != 200:
                break
            payload = r.json() or {}
            rows = payload.get("token_transfers") or payload.get("data") or []
            if not rows:
                break
            for row in rows:
                try:
                    frm = _tron_norm(row.get("from_address") or "")
                    if frm != address_norm:
                        continue
                    to_addr = _tron_norm(row.get("to_address") or "")
                    token_info = row.get("tokenInfo") or {}
                    symbol = token_info.get("tokenAbbr") or token_info.get("symbol") or "?"
                    decimals = int(token_info.get("tokenDecimal") or 6)
                    value_int = int(row.get("quant") or 0)
                    ts_ms = int(row.get("block_ts") or 0)
                    txid = row.get("transaction_id") or ""
                    if not txid:
                        continue
                    all_txs.append({
                        "hash": txid,
                        "from": frm,
                        "to": to_addr,
                        "value": value_int,
                        "time": ts_ms // 1000,
                        "block": int(row.get("block") or 0),
                        "success": True,
                        "token": symbol,
                        "decimals": decimals,
                        "chain": "tron",
                    })
                except Exception:
                    continue
            # If the oldest row is already before the window, we can stop.
            try:
                oldest_ts = int(rows[-1].get("block_ts") or 0)
                if oldest_ts and oldest_ts < start_ms:
                    break
            except Exception:
                pass
            if len(rows) < 20:
                break
            start += 20
    except Exception as e:
        print(f"  TronScan TRC20 transfers error: {e}")

    # Preferred: TronScan transaction index (more reliable than TronGrid account endpoints)
    try:
        # NOTE: TronScan uses ms timestamps; pagination via start/limit.
        base_url = "https://apilist.tronscanapi.com/api/transaction"
        # Pull a few pages within window; keep it bounded.
        for start in (0, 50, 100, 150):
            params = {
                "sort": "timestamp",
                "count": "true",
                "limit": 50,
                "start": start,
                "start_timestamp": start_ms,
                "end_timestamp": end_ms,
                "address": address_norm,
            }
            time.sleep(0.25)
            r = SESSION.get(base_url, params=params, headers=tron_headers, timeout=15)
            if r.status_code != 200:
                break
            payload = r.json() or {}
            data = payload.get("data") or []
            if not data:
                break
            # Avoid unbounded per-tx lookups for TRC-20 decoding (but allow enough
            # headroom for busy services like FixedFloat).
            trc20_info_lookups = 0
            for tx in data:
                try:
                    owner = _tron_norm(tx.get("ownerAddress") or tx.get("owner_address") or "")
                    if owner != address_norm:
                        continue
                    ts_ms = int(tx.get("timestamp") or 0)
                    tx_hash = tx.get("hash") or tx.get("txID") or tx.get("txid") or ""
                    contract_type = tx.get("contractType")

                    # TRC-20 transfers: list endpoint often only shows contract address as toAddress,
                    # so decode with transaction-info to get real token transfer to/from/amount.
                    if contract_type == 31 and tx_hash and trc20_info_lookups < 220:
                        trc20_info_lookups += 1
                        info = _tronscan_tx_info(tx_hash) or {}
                        trc20 = info.get("trc20TransferInfo") or {}
                        # Sometimes transfer fields are nested or named differently
                        token_symbol = trc20.get("symbol") or trc20.get("tokenInfo", {}).get("symbol") or "?"
                        decimals = int(trc20.get("decimals") or 6)
                        to_addr = _tron_norm(trc20.get("to_address") or trc20.get("toAddress") or "")
                        frm = _tron_norm(trc20.get("from_address") or trc20.get("fromAddress") or owner)
                        amount_str = trc20.get("amount_str") or trc20.get("amount") or trc20.get("quant")
                        if frm != address_norm or not to_addr or amount_str is None:
                            continue
                        all_txs.append({
                            "hash": tx_hash,
                            "from": frm,
                            "to": to_addr,
                            "value": int(amount_str),
                            "time": int(info.get("timestamp") or ts_ms) // 1000,
                            "block": int(info.get("block") or tx.get("block") or 0),
                            "success": True,
                            "token": token_symbol,
                            "decimals": decimals,
                            "chain": "tron",
                        })
                        continue

                    # Default decode (covers TRX transfers and other simple types)
                    to_addr = _tron_norm(tx.get("toAddress") or tx.get("to_address") or "")
                    token_info = tx.get("tokenInfo") or {}
                    token_abbr = (
                        token_info.get("tokenAbbr")
                        or token_info.get("tokenSymbol")
                        or token_info.get("symbol")
                        or "TRX"
                    )
                    decimals = int(token_info.get("tokenDecimal") or token_info.get("decimals") or (6 if token_abbr.lower() == "trx" else 6))
                    amount = tx.get("amount")
                    if amount is None:
                        amount = (tx.get("contractData") or {}).get("amount")
                    if amount is None:
                        continue
                    all_txs.append({
                        "hash": tx_hash,
                        "from": owner,
                        "to": to_addr,
                        "value": int(amount),
                        "time": ts_ms // 1000,
                        "block": int(tx.get("block") or 0),
                        "success": True,
                        "token": token_abbr,
                        "decimals": decimals,
                        "chain": "tron",
                    })
                except Exception:
                    continue
            # Stop early if fewer than page size
            if len(data) < 50:
                break
        if all_txs:
            return all_txs
    except Exception as e:
        print(f"  TronScan tx list error: {e}")

    # TRC-20 token transfers via TronGrid
    try:
        url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
        params = {
            "min_timestamp": start_ms, "max_timestamp": end_ms,
            "limit": 200, "order_by": "block_timestamp,asc",
        }
        time.sleep(0.3)
        r = SESSION.get(url, params=params, headers=tron_headers, timeout=15)
        if r.status_code == 200:
            data = r.json().get("data", [])
            for tx in data:
                try:
                    frm = _tron_norm(tx.get("from", ""))
                    if frm != address_norm:
                        continue
                    tok = tx.get("token_info", {}) or {}
                    decimals = int(tok.get("decimals", 6) or 6)
                    raw_val = tx.get("value", 0)
                    # TronGrid may return value as string; keep smallest-unit integer
                    value_int = int(raw_val)
                    all_txs.append({
                        "hash": tx.get("transaction_id", ""),
                        "from": frm,
                        "to": _tron_norm(tx.get("to", "")),
                        "value": value_int,
                        "time": tx.get("block_timestamp", 0) // 1000,
                        "block": 0,
                        "success": True,
                        "token": tok.get("symbol", "?"),
                        "decimals": decimals,
                        "chain": "tron",
                    })
                except Exception:
                    # Skip malformed rows rather than dropping the whole response
                    continue
            print(f"    TRC20: {len(all_txs)} outgoing txs")
    except Exception as e:
        print(f"  Tron TRC20 error: {e}")

    # Native TRX transfers
    try:
        url = f"https://api.trongrid.io/v1/accounts/{address}/transactions"
        params = {
            "min_timestamp": start_ms, "max_timestamp": end_ms,
            "limit": 200, "order_by": "block_timestamp,asc",
            "only_confirmed": "true",
        }
        time.sleep(0.3)
        r = SESSION.get(url, params=params, timeout=15)
        if r.status_code == 200:
            data = r.json().get("data", [])
            for tx in data:
                contract = tx.get("raw_data", {}).get("contract", [{}])[0]
                ctype = contract.get("type", "")
                if ctype != "TransferContract":
                    continue
                val = contract.get("parameter", {}).get("value", {})
                owner = val.get("owner_address", "")
                owner_b58 = _tron_norm(owner)
                if owner_b58 and owner_b58 != address_norm:
                    continue
                amount = val.get("amount", 0)
                all_txs.append({
                    "hash": tx.get("txID", ""),
                    "from": address_norm,
                    "to": _tron_norm(val.get("to_address", "")),
                    "value": amount,
                    "time": tx.get("block_timestamp", 0) // 1000,
                    "block": 0,
                    "success": True,
                    "token": "TRX", "decimals": 6, "chain": "tron",
                })
    except Exception as e:
        print(f"  Tron TRX error: {e}")

    return all_txs


def search_out_transaction(
    receiver: str,
    value_wei: int,
    tx_time: int,
    start_time: int,
    end_time: int,
    deviation: float = 0.001,
    check_related: bool = True,
    receiver_entity: Optional[str] = None,
    known_nodes: Optional[Dict] = None,
    extra_wallets: Optional[List[str]] = None,
) -> Dict:
    """
    Search for outgoing transactions from the receiver (or related wallets)
    that match the source transaction's USD value within deviation%.
    Checks: ETH transfers, ERC-20 tokens, Tron TRC-20, Tron TRX.
    """
    # Get ETH price at transaction time for source value
    eth_price = get_eth_price_at_time(tx_time)
    if not eth_price:
        return {"error": "Could not fetch ETH price", "matches": []}

    source_eth = value_wei / 1e18
    source_usd = source_eth * eth_price
    match_low = source_usd * (1 - deviation)
    match_high = source_usd * (1 + deviation)

    receiver = _normalize_tron_address((receiver or "").strip())
    receiver_l = receiver.lower()

    # If UI didn't provide an entity label, try to infer it from known nodes
    if not receiver_entity and known_nodes and receiver_l in known_nodes:
        receiver_entity = known_nodes[receiver_l].get("entity")

    # Build list of EVM wallets to check
    wallets_to_check = [receiver.lower()]
    if check_related and receiver_entity:
        wallets_to_check = find_related_wallets(receiver, receiver_entity, known_nodes)

    # Tron wallets
    tron_wallets: List[str] = []
    # Always include receiver itself if it's a Tron address
    if receiver.startswith("T") and len(receiver) >= 30:
        tron_wallets.append(receiver)
    # If receiver is an entity-labeled EVM wallet, try to discover the entity's Tron wallets too
    if check_related and receiver_entity:
        for w in _find_tron_wallets(receiver_entity):
            if w not in tron_wallets:
                tron_wallets.append(w)

    # Add extra wallets from user input
    if extra_wallets:
        for w in extra_wallets:
            w = _normalize_tron_address(w.strip())
            if w.startswith("T") and len(w) > 30:
                # Tron address
                if w not in tron_wallets:
                    tron_wallets.append(w)
            elif w.startswith("0x"):
                # EVM address
                if w.lower() not in wallets_to_check:
                    wallets_to_check.append(w.lower())

    matches = []
    total_txs_checked = 0
    chains_checked = []

    # First, find which EVM chains have activity for the primary wallet
    active_chains = []
    primary = wallets_to_check[0] if wallets_to_check else receiver.lower()
    for chain_id, (chain_name, _) in EVM_CHAINS.items():
        print(f"  Probing {chain_name}…", end="")
        if _check_chain_activity(primary, chain_id):
            active_chains.append(chain_id)
            print(f" ✓ active")
        else:
            print(f" — skip")

    # Check active EVM chains — only scan all wallets if primary has activity
    for chain_id in active_chains:
        chain_name = EVM_CHAINS[chain_id][0]
        # First check primary wallet
        print(f"  [{chain_name}] Scanning primary {primary[:16]}…")
        txs = fetch_outgoing_txs(primary, start_time, end_time, chain_id=chain_id)
        if txs:
            total_txs_checked += len(txs)
            if chain_name not in chains_checked:
                chains_checked.append(chain_name)
            _match_txs(txs, source_usd, match_low, match_high, matches, known_nodes, source_time=tx_time)
        # Only scan remaining wallets on this chain if primary had txs
        if txs or chain_id == 1:  # always scan all on ethereum
            for wallet in wallets_to_check[1:]:
                print(f"  [{chain_name}] Scanning {wallet[:16]}…")
                wtxs = fetch_outgoing_txs(wallet, start_time, end_time, chain_id=chain_id)
                if wtxs:
                    total_txs_checked += len(wtxs)
                    if chain_name not in chains_checked:
                        chains_checked.append(chain_name)
                    _match_txs(wtxs, source_usd, match_low, match_high, matches, known_nodes, source_time=tx_time)

    # Check Tron wallets
    for wallet in tron_wallets:
        print(f"  [tron] Checking {wallet[:16]}…")
        txs = fetch_tron_outgoing_txs(wallet, start_time, end_time)
        if txs:
            total_txs_checked += len(txs)
            if "tron" not in chains_checked:
                chains_checked.append("tron")
            _match_txs(txs, source_usd, match_low, match_high, matches, known_nodes, source_time=tx_time)

    matches.sort(key=lambda m: m["deviation"])
    unique_matches = []
    seen = set()
    for m in matches:
        sig = (m.get("hash"), m.get("from"), m.get("to"), m.get("token"), m.get("chain"))
        if sig in seen:
            continue
        seen.add(sig)
        unique_matches.append(m)

    return {
        "source_usd": source_usd,
        "eth_price": eth_price,
        "match_range": [match_low, match_high],
        "wallets_checked": len(wallets_to_check) + len(tron_wallets),
        "txs_checked": total_txs_checked,
        "chains_checked": chains_checked if chains_checked else ["ethereum"],
        "matches": unique_matches,
    }


def _match_txs(txs, source_usd, match_low, match_high, matches, known_nodes, source_time: Optional[int] = None):
    """Check a list of txs for USD value matches and append to matches list."""
    for tx in txs:
        token = tx.get("token", "ETH")
        decimals = tx.get("decimals", 18)
        chain = tx.get("chain", "ethereum")
        raw_value = tx["value"]
        human_value = raw_value / (10 ** decimals)

        # Get token price
        price = _get_token_price(token, tx["time"])
        if not price:
            price = get_eth_price_at_time(tx["time"]) if token == "ETH" else None
        if not price:
            continue

        tx_usd = human_value * price
        if tx_usd <= 0:
            continue

        if match_low <= tx_usd <= match_high:
            dev = abs(tx_usd - source_usd) / source_usd if source_usd > 0 else 0
            time_dev_s = None
            if source_time:
                try:
                    time_dev_s = int(tx["time"]) - int(source_time)
                except Exception:
                    time_dev_s = None
            to_label = None
            from_label = None
            if known_nodes:
                if tx["to"] in known_nodes:
                    to_label = known_nodes[tx["to"]].get("entity")
                if tx["from"] in known_nodes:
                    from_label = known_nodes[tx["from"]].get("entity")

            matches.append({
                "hash": tx["hash"],
                "from": tx["from"],
                "to": tx["to"],
                "value": raw_value,
                "human_value": human_value,
                "usd_value": tx_usd,
                "time": tx["time"],
                "time_deviation_s": time_dev_s,
                "block": tx.get("block", 0),
                "deviation": dev,
                "from_label": from_label,
                "to_label": to_label,
                "token": token,
                "chain": chain,
                "token_price": price,
            })


def _find_tron_wallets(entity_name: str) -> List[str]:
    """Find Tron addresses for an entity using TronScan search/v2 API."""
    base = entity_name.split()[0] if entity_name else ""
    if not base or len(base) < 3:
        return []

    wallets = []
    try:
        time.sleep(0.3)
        headers = {"TRON-PRO-API-KEY": TRONSCAN_KEY} if TRONSCAN_KEY else {}
        r = SESSION.get(
            f"https://apilist.tronscanapi.com/api/search/v2?term={base}&type=&start=0&limit=20",
            headers=headers, timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            for item in data.get("address", []):
                addr = item.get("address", "")
                tag = item.get("address_tag", "")
                if addr.startswith("T") and tag and base.lower() in tag.lower():
                    wallets.append(addr)
            if wallets:
                print(f"  Found {len(wallets)} Tron wallet(s) for {base} via TronScan")
    except Exception as e:
        print(f"  TronScan search error: {e}")

    return wallets
