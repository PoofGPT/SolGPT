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
    version="0.2.3",
    description="SolGPT—fetch balances, price any token via Jupiter, and find mint addresses",
)

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
if not HELIUS_API_KEY:
    raise RuntimeError("Please set the HELIUS_API_KEY environment variable")

JUPITER_QUOTE_API = "https://quote-api.jup.ag/v1/quote"
HELIUS_BALANCE_URL = "https://api.helius.xyz/v0/addresses/{addr}/balances"
HELIUS_TOKEN_METADATA_URL = "https://api.helius.xyz/v0/tokens"
TOKEN_LIST_URL = "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL_MINT = "So11111111111111111111111111111111111111112"


# ─── Cache SPL token list entries ─────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_full_token_list() -> List[Dict]:
    resp = requests.get(TOKEN_LIST_URL, timeout=10)
    resp.raise_for_status()
    return resp.json().get("tokens", [])


# ─── Resolve symbol to mint via cached list ────────────────────────────────────
def resolve_symbol_to_mint(raw_symbol: str) -> Optional[str]:
    sym = raw_symbol.lstrip("$ ").upper()
    for entry in get_full_token_list():
        if entry.get("symbol", "").upper() == sym:
            return entry.get("address")
    return None


# ─── Pydantic models ───────────────────────────────────────────────────────────
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

class TokenInfo(BaseModel):
    symbol: str
    name: str
    address: str


# ─── Root endpoint ─────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"message": "SolGPT API: /wallet/{address}, /price/{token_or_mint}, /find/{query}"}


# ─── GET /wallet/{address} ─────────────────────────────────────────────────────
@app.get("/wallet/{address}", response_model=WalletResponse)
def get_wallet_balance(address: str):
    if len(address) < 32 or len(address) > 44:
        raise HTTPException(status_code=400, detail="Invalid Solana address format")
    url = HELIUS_BALANCE_URL.format(addr=address) + f"?api-key={HELIUS_API_KEY}"
    try:
        r = requests.get(url, timeout=10); r.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Helius balance error: {e}")
    data = r.json()
    sol_balance = data.get("lamports", 0) / 1e9
    tokens = []
    for t in data.get("tokens", []):
        mint = t.get("mint",""); raw_amt = t.get("amount","0"); dec = t.get("decimals",0)
        try: amt = int(raw_amt) / (10**dec) if dec>=0 else int(raw_amt)
        except: amt = 0
        tokens.append(TokenBalance(mint=mint, amount=str(amt), decimals=dec))
    return WalletResponse(address=address, sol_balance=sol_balance, tokens=tokens)


# ─── GET /price/{identifier} ───────────────────────────────────────────────────
@app.get(
    "/price/{identifier}",
    response_model=PriceResponse,
    summary="Get token price",
    description="Get USD price of a token by mint or symbol using Jupiter",
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
            resolved = resolve_symbol_to_mint(raw)
            if not resolved:
                raise HTTPException(status_code=400, detail="Invalid mint or symbol")
            mint = resolved
    if mint == USDC_MINT:
        return PriceResponse(address=mint, price_usd=1.0, source="static")
    params = {"addresses[]": mint, "api-key": HELIUS_API_KEY}
    try:
        tm_resp = requests.get(HELIUS_TOKEN_METADATA_URL, params=params, timeout=5)
        tm_resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Helius metadata error: {e}")
    tm_data = tm_resp.json()
    if not isinstance(tm_data, list) or not tm_data:
        raise HTTPException(status_code=404, detail="Token metadata not found")
    dec = tm_data[0].get("decimals")
    if dec is None:
        raise HTTPException(status_code=404, detail="Decimals unavailable")
    amount_raw = 10 ** dec
    quote_params = {"inputMint": mint, "outputMint": USDC_MINT, "amount": amount_raw, "slippageBps": 50}
    try:
        jq = requests.get(JUPITER_QUOTE_API, params=quote_params, timeout=5); jq.raise_for_status()
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
    return PriceResponse(address=mint, price_usd=usd_price, source="jupiter")


# ─── GET /find/{query} ───────────────────────────────────────────────────────────
@app.get(
    "/find/{query}",
    response_model=List[TokenInfo],
    summary="Find tokens",
    description="Search SPL token list by symbol or name to retrieve mint addresses",
)
def find_tokens(query: str):
    q = query.strip().lower()
    results = []
    for entry in get_full_token_list():
        sym = entry.get("symbol", "").lower()
        name = entry.get("name", "").lower()
        if q in sym or q in name:
            results.append(TokenInfo(
                symbol=entry.get("symbol", ""),
                name=entry.get("name", ""),
                address=entry.get("address", "")
            ))
    if not results:
        raise HTTPException(status_code=404, detail="No tokens match query")
    return results


# ─── GET /swap ──────────────────────────────────────────────────────────────────
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
    est = round(amount * 1000, 6)
    return {"inputMint": input_mint, "outputMint": output_mint, "amount": amount,
            "estimatedOutput": est, "slippageBps": 50,
            "route": ["SOL", "USDC", output_mint], "platform": "Jupiter (mocked)"}


# ─── Override OpenAPI schema ────────────────────────────────────────────────────
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    schema["servers"] = [{"url": "https://solgpt-production.up.railway.app", "description": "Production"}]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
