# Orbit

> **Status: work in progress.** Orbit is an active side project. I'll keep improving it when I have time, so expect rough edges and changes. Issues, ideas and pull requests are welcome.
> Also there were more tools, but I lost bunch of them. I will add todo in next few days.

**A local blockchain investigation workstation for tracing fund flows on Ethereum and Tron.**

Orbit runs on your own machine. You enter a subject address, and it draws the money flow as a graph you expand step by step: who sent funds in, where they went out, and which known entities (exchanges, services) are involved. Every lookup is recorded in an evidence log so results can be reproduced later.

## Features

- **Interactive trace graph:** start from one address, expand any transaction into a new node, and keep inflows (left) and outflows (right) in clear columns
- **Entity labels:** public Etherscan name tags (for example exchange hot wallets) are shown on nodes
- **Search Out Transaction:** given an incoming transfer, find outgoing transfers from the receiver (and optionally related wallets of the same entity) that match its **USD value** within a time window, across Ethereum, other EVM chains and Tron (TRX + TRC-20)
- **Activity indicators:** nodes active in the last 30 days or 6 months are marked
- **Cases:** attach a trace to a case and save findings to `cases/<case_id>/`
- **Evidence log:** each query is appended to `evidence_manifest.jsonl` with a timestamp, the source and a SHA-256 hash of the response
- **Groups, hiding, search and export:** cluster related nodes, hide noise, search by address or entity, and export or import the graph as JSON

## Getting started

Requires **Python 3.10+**.

```bash
pip install -r requirements.txt
cp .env.example .env      # then add your API keys
python server.py
```

Open **http://localhost:8888**. The server only listens on this machine.

### Configuration (`.env`)

| Variable | Required | Purpose |
|---|---|---|
| `ETHERSCAN_API_KEY` | Recommended | Ethereum/EVM transaction history (free key at etherscan.io/apis) |
| `TRONSCAN_API_KEY` | Recommended | Tron lookups with higher rate limits |
| `ETH_RPC_URL` | No | Ethereum JSON-RPC for balances (default `https://ethereum.publicnode.com`) |
| `CRYPTOCOMPARE_API_KEY` | No | Optional historical prices; Binance and Coinbase public data are used without it |
| `ORBIT_LOG_LEVEL`, `ORBIT_LOG_FILE` | No | Logging (default `INFO`, `orbit.log`) |

API keys are only read by the Python backend and are never sent to the browser.

## Project structure

| Path | Purpose |
|---|---|
| `server.py` | Local HTTP server: serves the UI and the `/api/*` endpoints |
| `index.html` | The whole UI (graph, panels, tools), using vis-network |
| `core/transactions.py` | Balances and transaction history (RPC, Etherscan, TronScan) |
| `core/investigation.py` | Search Out Transaction: USD-value matching across chains |
| `core/cases.py` | Case folders and saved findings |
| `core/evidence.py` | Evidence log with response hashes |

## Known limitations

- **Automated network scan** is not included yet: the background scanner module (`orbiteth`) isn't part of this repository, so the option shows as unavailable.
- Entity labels come from scraping Etherscan page titles and can break if the page changes.
- Free API tiers are rate limited, so large traces take a while.

## Responsible use

Orbit only reads public blockchain data and public labels. Treat its results as leads: verify findings against primary sources before acting on them, and use it in line with the laws that apply to you.

## License

[MIT](LICENSE)
