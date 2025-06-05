# main.py

import os
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel
from typing import List, Optional, Dict
from functools import lru_cache

app = FastAPI(
    title="SolGPT API",
    version="0.2.1",
    description="SolGPT—fetch SOL/SPL balances via Helius and price any token via Jupiter",
)

# ────────────────────────────────────────────────────────────────────────────
# 1) Helius & Jupiter endpoints
# ────────────────────────────────────────────────────────────────────────────
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
if not HELIUS_API_KEY:
    raise RuntimeError("Please set the HELIUS_API_KEY environment variable")

JUPITER_QUOTE_API = "https://quote-api.jup.ag/v1/quote"
HELIUS_BALANCE_URL = "https://api.helius.xyz/v0/addresses/{addr}/balances"
HELIUS_TOKEN_METADATA_URL = "https://api.helius.xyz/v0/tokens"


# ────────────────────────────────────────────────────────────────────────────
# 2) SPL Token List (symbol → mint)
# ────────────────────────────────────────────────────────────────────────────
TOKEN_LIST_URL = (
    "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
)

@lru_cache(maxsize=1)
def get_token_list_map() -> Dict[str, str]:
    """
    Download & cache SPL Token List → { SYMBOL (upper): mintAddress }
    """
    resp = requests.get(TOKEN_LIST_URL, timeout=10)
    resp.raise_for_status()
    tokens = resp.json().get("tokens", [])
    mapping: Dict[str, str] = {}
    for entry in tokens:
        sym = entry.get("symbol", "").upper()
        mint = entry.get("address", "")
        if sym and mint:
            mapping[sym] = mint
    return mapping

def resolve_symbol_to_mint(raw_symbol: str) -> Optional[str]:
    """
    Strip leading '$', uppercase, look up in SPL Token List → mint.
    """
    sym = raw_symbol.lstrip("$ ").upper()
    return get_token_list_map().get(sym)


# ────────────────────────────────────────────────────────────────────────────
# 3) Pydantic models
# ────────────────────────────────────────────────────────────────────────────
class TokenBalance(BaseModel):
    mint: str
    amount: str
    decimals: int

class WalletResponse(BaseModel):
    address: str
    sol_balance: float
    tokens: List[TokenBalance]

class PriceResponse(BaseModel):
    address: str
    price_usd: float
    source: str  # "jupiter" or "static"


# ────────────────────────────────────────────────────────────────────────────
# 4) Root endpoint
# ────────────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "message": "SolGPT API: /wallet/{address}, /price/{token_or_mint}, /swap"
    }


# ────────────────────────────────────────────────────────────────────────────
# 5) GET /wallet/{address} → SOL + SPL balances via Helius
# ────────────────────────────────────────────────────────────────────────────
@app.get("/wallet/{address}", response_model=WalletResponse)
def get_wallet_balance(address: str):
    if len(address) < 32 or len(address) > 44:
        raise HTTPException(status_code=400, detail="Invalid Solana address format")

    url = HELIUS_BALANCE_URL.format(addr=address) + f"?api-key={HELIUS_API_KEY}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Helius balance error: {e}")

    data = r.json()
    sol_lamports = data.get("lamports", 0)
    sol_balance = sol_lamports / 1e9

    tokens: List[TokenBalance] = []
    for t in data.get("tokens", []):
        mint = t.get("mint", "")
        raw_amt = t.get("amount", "0")
        decimals = t.get("decimals", 0)
        try:
            human_amt = int(raw_amt) / (10 ** decimals) if decimals >= 0 else int(raw_amt)
        except Exception:
            human_amt = 0
        tokens.append(TokenBalance(mint=mint, amount=str(human_amt), decimals=decimals))

    return WalletResponse(address=address, sol_balance=sol_balance, tokens=tokens)


# ────────────────────────────────────────────────────────────────────────────
# 6) GET /price/{identifier} → price via Jupiter (fetch decimals from Helius)
# ────────────────────────────────────────────────────────────────────────────
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC (6 decimals)
WSOL_MINT = "So11111111111111111111111111111111111111112"  # Wrapped SOL

@app.get("/price/{identifier}", response_model=PriceResponse)
def get_token_price(identifier: str):
    """
    Accepts:
      • Token symbol (e.g. "BONK", "$BONK", "USDC")
      • OR mint address (32–44 char Base58).
    Steps:
      1) Strip '$', identify mint (direct or via SPL list; special‐case "SOL" → WSOL).
      2) If mint == USDC_MINT, return price_usd = 1.0.
      3) Fetch token decimals via Helius /tokens endpoint.
      4) Query Jupiter: amount = 10**decimals, outputMint = USDC_MINT.
      5) Calculate price_usd = outAmount / 10**6.
    """
    raw = identifier.strip()
    if raw.startswith("$"):
        raw = raw.lstrip("$").strip()

    # 1) Determine mint
    if 32 <= len(raw) <= 44 and raw.isalnum():
        mint = raw
    else:
        sym = raw.upper()
        if sym == "SOL":
            mint = WSOL_MINT
        else:
            resolved = resolve_symbol_to_mint(raw)
            if not resolved:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{identifier}' is not a valid mint or known symbol"
                )
            mint = resolved

    # 2) If USDC, fixed price
    if mint == USDC_MINT:
        return PriceResponse(address=mint, price_usd=1.0, source="static")

    # 3) Fetch token metadata (to get decimals) via Helius
    params = {"addresses[]": mint, "api-key": HELIUS_API_KEY}
    try:
        tm_resp = requests.get(HELIUS_TOKEN_METADATA_URL, params=params, timeout=5)
        tm_resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Helius token metadata error: {e}")

    tm_data = tm_resp.json()
    if not isinstance(tm_data, list) or not tm_data:
        raise HTTPException(status_code=404, detail="Token metadata not found")
    decimals = tm_data[0].get("decimals")
    if decimals is None:
        raise HTTPException(status_code=404, detail="Decimals not available for this token")

    # 4) Build Jupiter quote for 1 full token (10**decimals)
    amount_raw = 10 ** decimals
    params = {
        "inputMint": mint,
        "outputMint": USDC_MINT,
        "amount": amount_raw,
        "slippageBps": 50
    }
    try:
        jq = requests.get(JUPITER_QUOTE_API, params=params, timeout=5)
        jq.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Jupiter API error: {e}")

    data = jq.json().get("data", [])
    if not data:
        raise HTTPException(status_code=404, detail="No liquidity route found for this token")

    best = data[0]
    out_amount_str = best.get("outAmount")
    if not out_amount_str:
        raise HTTPException(status_code=404, detail="No output amount returned")

    try:
        out_amount = int(out_amount_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid outAmount format")

    # USDC has 6 decimals
    price_usd = out_amount / 10**6
    return PriceResponse(address=mint, price_usd=price_usd, source="jupiter")


# ────────────────────────────────────────────────────────────────────────────
# 7) GET /swap → mock swap simulation (inputMint, outputMint, amount)
# ────────────────────────────────────────────────────────────────────────────
@app.get("/swap")
def simulate_swap(
    input_mint: str = Query(..., alias="inputMint"),
    output_mint: str = Query(..., alias="outputMint"),
    amount: float = Query(...),
):
    if len(input_mint) < 32 or len(input_mint) > 44:
        raise HTTPException(status_code=400, detail="Invalid inputMint format")
    if len(output_mint) < 32 or len(output_mint) > 44:
        raise HTTPException(status_code=400, detail="Invalid outputMint format")

    estimated_output = round(amount * 1000, 6)
    return {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "estimatedOutput": estimated_output,
        "slippageBps": 50,
        "route": ["SOL", "USDC", output_mint],
        "platform": "Jupiter (mocked)",
    }


# ────────────────────────────────────────────────────────────────────────────
# 8) Override OpenAPI schema with only production server
# ────────────────────────────────────────────────────────────────────────────
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
            "description": "Production (Railway)"
        }
    ]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
