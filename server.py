"""
Orbit server — serves the unified UI + proxies Etherscan labels + runs auto scans.
Usage: python server.py → open http://localhost:8888
"""

from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import io
import logging
import os
from pathlib import Path
import sys
import time
import urllib.request
import urllib.parse
import json
import re
import threading

from core.config import load_env
from core.cases import ensure_case, list_cases, load_case, save_finding
from core.evidence import append_evidence

load_env()

# Windows consoles often use a legacy code page; make print() calls in the
# core modules (which contain symbols like "⚠") unable to crash a request.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")

PORT = 8888


def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("orbit")
    if logger.handlers:
        return logger
    level_name = os.getenv("ORBIT_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Console: Windows terminals may use non-UTF8 codepages ("charmap" errors).
    # Wrap stdout/stderr with UTF-8 + safe error handling.
    console_stream = sys.stderr
    try:
        if hasattr(console_stream, "buffer"):
            console_stream = io.TextIOWrapper(
                console_stream.buffer, encoding="utf-8", errors="backslashreplace"
            )
    except Exception:
        console_stream = sys.stderr

    console_handler = logging.StreamHandler(console_stream)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File log (UTF-8), enabled by default.
    log_file = os.getenv("ORBIT_LOG_FILE", "orbit.log")
    base_dir = Path(__file__).resolve().parent
    log_path = Path(log_file)
    if not log_path.is_absolute():
        log_path = base_dir / log_path
    # Expose for diagnostics.
    setattr(logger, "_orbit_log_path", str(log_path))
    try:
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception:
        # If file can't be created (permissions, etc.), keep console logging.
        pass

    logger.propagate = False
    return logger


logger = _setup_logger()
logger.info(
    "Logger initialized (pid=%s cwd=%s log=%s)",
    os.getpid(),
    os.getcwd(),
    getattr(logger, "_orbit_log_path", "<unknown>"),
)


def _install_excepthooks() -> None:
    # Log uncaught exceptions (main thread + background threads).
    def _sys_excepthook(exc_type, exc, tb):
        logger.error("Uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = _sys_excepthook

    if hasattr(threading, "excepthook"):
        def _threading_excepthook(args):
            logger.error(
                "Uncaught thread exception in %s",
                getattr(args.thread, "name", "<thread>"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = _threading_excepthook


_install_excepthooks()


class OrbitHandler(SimpleHTTPRequestHandler):
    server_version = "OrbitHTTP/1.0"

    def end_headers(self):
        # If you don't see this header in the browser, you're not hitting this server.py.
        self.send_header("X-Orbit-Server", f"pid={os.getpid()};ts={int(time.time())}")
        # Local tool: always serve the current files (no stale cached UI after updates).
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        logger.info("HTTP GET %s", getattr(self, "path", ""))
        try:
            if self.path == '/' or self.path == '':
                self.path = '/index.html'
            if self.path.startswith('/api/label'):
                self.handle_label()
                return
            if self.path.startswith('/api/address-balance'):
                self.handle_address_balance()
                return
            if self.path.startswith('/api/address-transactions'):
                self.handle_address_transactions()
                return
            if self.path.startswith('/api/status'):
                self.handle_status()
                return
            if self.path.startswith('/api/cases'):
                self.handle_cases_list()
                return
            if self.path.startswith('/api/case'):
                self.handle_case_get()
                return
            super().do_GET()
        except Exception:
            logger.exception("Unhandled error in GET %s", getattr(self, "path", ""))
            self.send_error(500)

    def do_POST(self):
        logger.info("HTTP POST %s", getattr(self, "path", ""))
        try:
            if self.path == '/api/autoscan':
                self.handle_autoscan()
                return
            if self.path == '/api/search-out-tx':
                self.handle_search_out_tx()
                return
            if self.path == '/api/cases':
                self.handle_case_create()
                return
            if self.path == '/api/case/save-finding':
                self.handle_case_save_finding()
                return
            self.send_error(404)
        except Exception:
            logger.exception("Unhandled error in POST %s", getattr(self, "path", ""))
            self.send_error(500)

    def handle_label(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        address = params.get('address', [''])[0]
        if not address:
            self.send_json({"entity": None, "wallet": None})
            return
        try:
            url = f"https://etherscan.io/address/{address}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            m = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
            if not m:
                self.send_json({"entity": None, "wallet": None})
                return
            title = m.group(1).strip()
            parts = [p.strip() for p in title.split("|")]
            if len(parts) >= 3:
                entity = parts[0]
                if ":" in entity:
                    name, wtype = entity.split(":", 1)
                    payload = {"entity": name.strip(), "wallet": wtype.strip()}
                    append_evidence(
                        action="label_lookup",
                        source="etherscan.io",
                        query={"address": address},
                        response=payload,
                        case_id=None,
                    )
                    self.send_json(payload)
                else:
                    payload = {"entity": entity, "wallet": None}
                    append_evidence(
                        action="label_lookup",
                        source="etherscan.io",
                        query={"address": address},
                        response=payload,
                        case_id=None,
                    )
                    self.send_json(payload)
            else:
                self.send_json({"entity": None, "wallet": None})
        except Exception as e:
            logger.exception("Label error")
            self.send_json({"entity": None, "wallet": None})

    def handle_autoscan(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        address = body.get('address', '')
        depth = body.get('depth', 2)
        if not address:
            self.send_json({"error": "No address"})
            return
        # The network scanner lives in a separate `orbiteth` module that is not
        # part of this repository yet; report that instead of failing silently.
        try:
            import orbiteth  # noqa: F401
        except ImportError:
            self.send_json({"error": "Auto-scan is not available: the orbiteth scanner module is not included yet."})
            return
        # Run scan in background, return immediately
        def run_scan():
            try:
                from orbiteth import scan_eth_address_network, save_network_data
                result = scan_eth_address_network(
                    start_address=address, max_depth=depth,
                    tx_page_size=20, max_pages=3, use_arkham=True
                )
                save_network_data(result, "eth_network.json")
                logger.info(
                    "Auto-scan complete: %s nodes",
                    result.get("metadata", {}).get("total_nodes"),
                )
            except Exception as e:
                logger.exception("Auto-scan error")
        t = threading.Thread(target=run_scan, daemon=True)
        t.start()
        self.send_json({"status": "scanning", "message": "Scan started in background"})

    def handle_address_balance(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        address = params.get("address", [""])[0].strip()
        chain = params.get("chain", ["auto"])[0].strip().lower()
        if not address:
            self.send_json({"error": "No address"})
            return
        try:
            from core.transactions import get_address_balance, get_eth_address_balance
            if chain == "tron" or (chain == "auto" and address.startswith("T")):
                bal = get_address_balance("tron", address)
                payload = {
                    "balance": (None if bal.get("balance") is None else int(bal.get("balance", 0))),
                    "transactionCount": (None if bal.get("transactionCount") is None else int(bal.get("transactionCount", 0))),
                    "chain": "tron",
                }
                append_evidence(action="address_balance", source="tronscan", query={"address": address, "chain": "tron"}, response=payload)
                self.send_json(payload)
                return
            bal = get_eth_address_balance(address)
            payload = {
                "balance": (None if bal.get("balance") is None else int(bal.get("balance", 0))),
                "transactionCount": (None if bal.get("transactionCount") is None else int(bal.get("transactionCount", 0))),
                "chain": "ethereum",
            }
            append_evidence(action="address_balance", source="eth_rpc", query={"address": address, "chain": "ethereum"}, response=payload)
            self.send_json(payload)
        except Exception as e:
            logger.exception("Address balance error")
            self.send_json({"error": str(e)})

    def handle_address_transactions(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        address = params.get("address", [""])[0].strip()
        chain = params.get("chain", ["auto"])[0].strip().lower()
        page = int(params.get("page", ["0"])[0])
        size = int(params.get("size", ["50"])[0])
        if not address:
            self.send_json({"error": "No address", "transactions": []})
            return
        try:
            from core.transactions import get_eth_address_transactions, get_tron_address_transactions
            if chain == "tron" or (chain == "auto" and address.startswith("T")):
                txs = get_tron_address_transactions(address, limit=size, offset=page * size)
                payload = {"transactions": txs, "chain": "tron"}
                append_evidence(action="address_transactions", source="tronscan", query={"address": address, "chain": "tron", "page": page, "size": size}, response={"count": len(txs)})
                self.send_json(payload)
                return
            txs = get_eth_address_transactions(address, page=page, size=size)
            payload = {"transactions": txs, "chain": "ethereum"}
            append_evidence(action="address_transactions", source="etherscan_api", query={"address": address, "chain": "ethereum", "page": page, "size": size}, response={"count": len(txs)})
            self.send_json(payload)
        except Exception as e:
            logger.exception("Address tx error")
            self.send_json({"error": str(e), "transactions": []})

    def handle_search_out_tx(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        try:
            from core.investigation import search_out_transaction

            # Pass known node data for entity matching
            known_nodes = {}
            # Try to load from current network data
            try:
                with open("eth_network.json") as f:
                    net = json.load(f)
                for n in net.get("nodes", []):
                    known_nodes[n["id"].lower()] = {
                        "entity": n.get("entity"),
                        "wallet": n.get("wallet_type"),
                    }
            except:
                pass

            result = search_out_transaction(
                receiver=body.get("receiver", ""),
                value_wei=body.get("value_wei", 0),
                tx_time=body.get("tx_time", 0),
                start_time=body.get("start_time", 0),
                end_time=body.get("end_time", 0),
                deviation=body.get("deviation", 0.001),
                check_related=body.get("check_related", True),
                receiver_entity=body.get("receiver_entity"),
                known_nodes=known_nodes,
                extra_wallets=body.get("extra_wallets", []),
            )
            append_evidence(
                action="search_out_tx",
                source="investigation_core",
                query={
                    "receiver": body.get("receiver", ""),
                    "start_time": body.get("start_time", 0),
                    "end_time": body.get("end_time", 0),
                    "deviation": body.get("deviation", 0.001),
                },
                response={"matches": len(result.get("matches", [])), "chains_checked": result.get("chains_checked", [])},
                case_id=body.get("case_id"),
            )
            self.send_json(result)

        except Exception as e:
            import traceback
            logger.exception("Search error")
            self.send_json({"error": str(e), "matches": []})

    def handle_status(self):
        """Which data sources are configured. Never returns key values."""
        rpc = os.getenv("ETH_RPC_URL", "https://ethereum.publicnode.com")
        manifest = Path(__file__).resolve().parent / "evidence_manifest.jsonl"
        try:
            with open(manifest, encoding="utf-8") as f:
                entries = sum(1 for line in f if line.strip())
        except OSError:
            entries = 0
        self.send_json({
            "etherscan_key": bool(os.getenv("ETHERSCAN_API_KEY")),
            "tronscan_key": bool(os.getenv("TRONSCAN_API_KEY")),
            "price_key": bool(os.getenv("CRYPTOCOMPARE_API_KEY")),
            "rpc_host": urllib.parse.urlparse(rpc).hostname or rpc,
            "evidence_entries": entries,
        })

    def handle_cases_list(self):
        try:
            self.send_json({"cases": list_cases()})
        except Exception as e:
            logger.exception("Cases list error")
            self.send_json({"error": str(e), "cases": []})

    def handle_case_get(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        case_id = params.get("case_id", [""])[0].strip()
        if not case_id:
            self.send_json({"error": "No case_id"})
            return
        data = load_case(case_id)
        if not data:
            self.send_json({"error": "Case not found"})
            return
        self.send_json({"case": data})

    def handle_case_create(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        case_id = body.get("case_id", "")
        title = body.get("title") or case_id
        if not case_id:
            self.send_json({"error": "No case_id"})
            return
        case = ensure_case(case_id, title=title)
        self.send_json({"case": case})

    def handle_case_save_finding(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        case_id = body.get("case_id", "")
        finding = body.get("finding", {})
        if not case_id:
            self.send_json({"error": "No case_id"})
            return
        data = save_finding(case_id, finding)
        append_evidence(
            action="save_finding",
            source="orbit_ui",
            query={"case_id": case_id},
            response={"finding_count": data.get("finding_count", 0)},
            case_id=case_id,
        )
        self.send_json({"ok": True, "case": data})

    def send_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if '/api/' in str(args[0]):
            logger.info("%s", args[0])


if __name__ == "__main__":
    logger.info("Orbit server running at http://localhost:%s", PORT)
    # Threaded so one slow upstream lookup (labels, Search Out) doesn't block the UI.
    # Bound to localhost only: the API proxies your keys and is meant for this machine.
    server = ThreadingHTTPServer(("127.0.0.1", PORT), OrbitHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down…")
        server.server_close()
