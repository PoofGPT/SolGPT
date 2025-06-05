# main.py

import os
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.openapi.utils import get_openapi
from functools import lru_cache

app = FastAPI(
    title="SolGPT API",
    version="0.2.3",
    description="Fetch wallet balances and USD prices for any SPL token",
)

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
if not HELIUS_API_KEY:
    raise RuntimeError("Please set HELIUS_API_KEY in your environment")

JUPITER_QUOTE_API = "https://quote-api.jup.ag/v1/quote"
HELIUS_BALANCE_URL = "https://api.helius.xyz/v0/addresses/{addr}/balances"
HELIUS_TOKEN_METADATA_URL = "https://api.helius.xyz/v0/tokens"
TOKEN_LIST_URL = "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL_MINT = "So11111111111111111111111111111111111111112"


@lru_cache(maxsize=1)
def get_symbol_map():
    """
    Returns a dict { SYMBOL (uppercase) : mintAddress } from the SPL token list.
    """
    r = requests.get(TOKEN_LIST_URL, timeout=10)
    r.raise_for_status()
    tokens = r.json().get("tokens", [])
    return {
        entry["symbol"].upper(): entry["address"]
        for entry in tokens
        if entry.get("symbol") and entry.get("address")
    }


@app.get("/")
def root():
    return {
        "message": "SolGPT API: /wallet/{address}, /price/{token_or_mint}, /swap"
    }


@app.get("/wallet/{address}")
def get_wallet_balance(address: str):
    """
    Return SOL and SPL token balances for a given wallet.
    """
    if len(address) < 32 or len(address) > 44:
        raise HTTPException(status_code=400, detail="Invalid Solana address format")

    url = HELIUS_BALANCE_URL.format(addr=address) + f"?api-key={HELIUS_API_KEY}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Helius error: {e}")

    data = r.json()
    sol_balance = data.get("lamports", 0) / 1e9

    tokens = []
    for t in data.get("tokens", []):
        mint = t.get("mint", "")
        raw_amt = t.get("amount", "0")
        dec = t.get("decimals", 0)
        try:
            amt = int(raw_amt) / (10 ** dec) if dec >= 0 else int(raw_amt)
        except:
            amt = 0
        tokens.append({"mint": mint, "amount": str(amt), "decimals": dec})

    return {"address": address, "sol_balance": sol_balance, "tokens": tokens}


@app.get(
    "/price/{identifier}",
    summary="Get token price",
    description="Fetch USD price of a token by mint or symbol using Jupiter",
)
def get_token_price(identifier: str):
    raw = identifier.strip().lstrip("$").strip()
    if 32 <= len(raw) <= 44 and raw.isalnum():
        mint = raw
    else:
        sym = raw.upper()
        if sym == "SOL":
            mint = WSOL_MINT
        else:
            mint = get_symbol_map().get(sym)
            if not mint:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{identifier}' is not a valid mint or known symbol"
                )

    if mint == USDC_MINT:
        return {"mint": mint, "price": 1.0, "source": "static"}

    # fetch decimals via Helius
    params = {"addresses[]": mint, "api-key": HELIUS_API_KEY}
    try:
        tm = requests.get(HELIUS_TOKEN_METADATA_URL, params=params, timeout=5)
        tm.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Helius metadata error: {e}")

    tm_data = tm.json()
    if not isinstance(tm_data, list) or not tm_data:
        raise HTTPException(status_code=404, detail="Token metadata not found")
    dec = tm_data[0].get("decimals")
    if dec is None:
        raise HTTPException(status_code=404, detail="Decimals unavailable")

    amount_raw = 10 ** dec
    quote_params = {
        "inputMint": mint,
        "outputMint": USDC_MINT,
        "amount": amount_raw,
        "slippageBps": 50
    }
    try:
        jq = requests.get(JUPITER_QUOTE_API, params=quote_params, timeout=5)
        jq.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Jupiter error: {e}")

    data = jq.json().get("data", [])
    if not data:
        raise HTTPException(status_code=404, detail="No liquidity route found")

    out_amt = data[0].get("outAmount")
    if not out_amt:
        raise HTTPException(status_code=404, detail="No output amount returned")

    try:
        usd_price = int(out_amt) / 10**6
    except:
        raise HTTPException(status_code=500, detail="Invalid outAmount format")

    return {"mint": mint, "price": usd_price, "source": "jupiter"}


@app.get("/swap")
def simulate_swap(
    inputMint: str = Query(...),
    outputMint: str = Query(...),
    amount: float = Query(...)
):
    if len(inputMint) < 32 or len(inputMint) > 44 or len(outputMint) < 32 or len(outputMint) > 44:
        raise HTTPException(status_code=400, detail="Invalid mint format")
    estimated = round(amount * 1000, 6)
    return {
        "inputMint": inputMint,
        "outputMint": outputMint,
        "amount": amount,
        "estimatedOutput": estimated,
        "slippageBps": 50,
        "route": ["SOL", "USDC", outputMint],
        "platform": "Jupiter (mocked)"
    }


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema["servers"] = [
        {
            "url": "https://solgpt-production.up.railway.app",
            "description": "Production"
        }
    ]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
