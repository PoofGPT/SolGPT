# main.py

import os
import requests
from fastapi import FastAPI, HTTPException, Query
from functools import lru_cache

app = FastAPI(
    title="SimpleSolPrice",
    description="Fetch SOL/SPL balances and USD token prices via Helius",
)

# ────────────────────────────────────────────────────────────────────────────
# 1) Load Helius API key from environment
# ────────────────────────────────────────────────────────────────────────────
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
if not HELIUS_API_KEY:
    raise RuntimeError("Please set HELIUS_API_KEY in your environment")


# ────────────────────────────────────────────────────────────────────────────
# 2) Cache the on-chain SPL Token List (symbol → mint)
# ────────────────────────────────────────────────────────────────────────────
TOKEN_LIST_URL = (
    "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
)

@lru_cache(maxsize=1)
def get_symbol_map():
    """
    Returns a dict { SYMBOL (uppercase) : mintAddress }
    """
    r = requests.get(TOKEN_LIST_URL, timeout=10)
    r.raise_for_status()
    tokens = r.json().get("tokens", [])
    return { entry["symbol"].upper(): entry["address"] for entry in tokens if entry.get("symbol") }


# ────────────────────────────────────────────────────────────────────────────
# 3) Root endpoint
# ────────────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "message": "SimpleSolPrice API: /wallet/{address}, /price/{mint_or_symbol}, /swap?inputMint=...&outputMint=...&amount=..."
    }


# ────────────────────────────────────────────────────────────────────────────
# 4) GET /wallet/{address} → fetch SOL + SPL balances via Helius
# ────────────────────────────────────────────────────────────────────────────
@app.get("/wallet/{address}")
def get_wallet_balance(address: str):
    """
    Returns SOL and SPL token balances for a given Solana wallet via Helius.
    """
    if len(address) < 32 or len(address) > 44:
        raise HTTPException(status_code=400, detail="Invalid Solana address format")

    helius_url = (
        f"https://api.helius.xyz/v0/addresses/{address}/balances?api-key={HELIUS_API_KEY}"
    )
    try:
        resp = requests.get(helius_url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Helius error: {e}")

    data = resp.json()
    # Helius returns "lamports" and "tokens" list
    sol_balance = data.get("lamports", 0) / 1e9

    tokens = []
    for t in data.get("tokens", []):
        mint = t.get("mint", "")
        raw_amt = t.get("amount", "0")
        decimals = t.get("decimals", 0)
        try:
            human_amt = int(raw_amt) / (10 ** decimals) if decimals >= 0 else int(raw_amt)
        except Exception:
            human_amt = 0
        tokens.append({
            "mint": mint,
            "amount": str(human_amt),
            "decimals": decimals
        })

    return {
        "address": address,
        "sol_balance": sol_balance,
        "tokens": tokens
    }


# ────────────────────────────────────────────────────────────────────────────
# 5) GET /price/{identifier} → price via Helius
# ────────────────────────────────────────────────────────────────────────────
@app.get("/price/{identifier}")
def get_price(identifier: str):
    """
    Returns JSON {"mint": ..., "price": ...}.
    Accepts either:
      • A mint address (32–44 Base58 chars)
      • A token symbol (with or without leading '$'), e.g. /price/BONK or /price/$BONK
    """
    raw = identifier.strip()
    if raw.startswith("$"):
        raw = raw[1:]
    symbol = raw.upper()

    # Determine if 'symbol' is actually a mint
    if 32 <= len(symbol) <= 44 and symbol.isalnum():
        mint = symbol
    else:
        mint = get_symbol_map().get(symbol)
        if not mint:
            raise HTTPException(
                status_code=400,
                detail=f"'{identifier}' is neither a valid mint nor a known SPL symbol."
            )

    helius_url = (
        f"https://api.helius.xyz/v0/token/price"
        f"?addresses[]={mint}"
        f"&api-key={HELIUS_API_KEY}"
    )
    try:
        resp = requests.get(helius_url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Helius error: {e}")

    data = resp.json()
    if not isinstance(data, list) or len(data) == 0 or data[0].get("price") is None:
        raise HTTPException(status_code=404, detail=f"No price found for mint {mint}")

    return {"mint": mint, "price": data[0]["price"]}


# ────────────────────────────────────────────────────────────────────────────
# 6) GET /swap → mock swap simulation
# ────────────────────────────────────────────────────────────────────────────
@app.get("/swap")
def simulate_swap(
    inputMint: str = Query(...),
    outputMint: str = Query(...),
    amount: float = Query(...)
):
    """
    Mock swap simulation. Returns a dummy route and estimated output.
    Usage: /swap?inputMint=<mint>&outputMint=<mint>&amount=<value>
    """
    if len(inputMint) < 32 or len(inputMint) > 44 or len(outputMint) < 32 or len(outputMint) > 44:
        raise HTTPException(status_code=400, detail="Invalid mint format")

    estimated_output = round(amount * 1000, 6)
    return {
        "inputMint": inputMint,
        "outputMint": outputMint,
        "amount": amount,
        "estimatedOutput": estimated_output,
        "slippageBps": 50,
        "route": ["SOL", "USDC", outputMint],
        "platform": "Jupiter (mocked)"
    }
