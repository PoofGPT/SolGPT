# main.py

import os
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel
from typing import List, Optional, Dict
from functools import lru_cache
from solana.rpc.api import Client

app = FastAPI(
    title="SolGPT API",
    version="0.2.0",
    description="SolGPT—fetch SOL/SPL balances and price any token via Jupiter aggregator",
)

# ────────────────────────────────────────────────────────────────────────────
# 1) RPC client for Solana (to fetch decimals via getTokenSupply)
# ────────────────────────────────────────────────────────────────────────────
SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"
solana_client = Client(SOLANA_RPC_URL)

# ────────────────────────────────────────────────────────────────────────────
# 2) SPL Token List URL (symbol → mint)
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
    Strip leading '$', uppercase, then check SPL Token List → mint address.
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
    source: str  # "jupiter" or "fallback"


# ────────────────────────────────────────────────────────────────────────────
# 4) Root endpoint
# ────────────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "message": "SolGPT API: /wallet/{address}, /price/{token_or_mint}"
    }


# ────────────────────────────────────────────────────────────────────────────
# 5) GET /wallet/{address} → fetch SOL + SPL balances via JSON-RPC
# ────────────────────────────────────────────────────────────────────────────
@app.get("/wallet/{address}", response_model=WalletResponse)
def get_wallet_balance(address: str):
    if len(address) < 32 or len(address) > 44:
        raise HTTPException(status_code=400, detail="Invalid Solana address format")

    # 5a) SOL balance
    sol_resp = solana_client.get_balance(address)
    if sol_resp.get("error"):
        raise HTTPException(status_code=502, detail="Error fetching SOL balance via RPC")
    lamports = sol_resp["result"]["value"]
    sol_balance = lamports / 1e9

    # 5b) SPL token accounts
    tokens_resp = solana_client.get_token_accounts_by_owner(
        address,
        {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
        encoding="jsonParsed",
    )
    if tokens_resp.get("error"):
        raise HTTPException(status_code=502, detail="Error fetching token accounts via RPC")

    tokens: List[TokenBalance] = []
    for acct in tokens_resp["result"]["value"]:
        info = acct["account"]["data"]["parsed"]["info"]
        mint = info["mint"]
        raw_amt = int(info["tokenAmount"]["amount"])
        decimals = info["tokenAmount"]["decimals"]
        human_amt = raw_amt / (10 ** decimals) if decimals >= 0 else raw_amt
        tokens.append(TokenBalance(mint=mint, amount=str(human_amt), decimals=decimals))

    return WalletResponse(address=address, sol_balance=sol_balance, tokens=tokens)


# ────────────────────────────────────────────────────────────────────────────
# 6) GET /price/{identifier} → use Jupiter aggregator to get price in USDC
# ────────────────────────────────────────────────────────────────────────────
JUPITER_QUOTE_API = "https://quote-api.jup.ag/v1/quote"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC on Solana (6 decimals)
WSOL_MINT = "So11111111111111111111111111111111111111112"  # Wrapped SOL

@app.get("/price/{identifier}", response_model=PriceResponse)
def get_token_price(identifier: str):
    """
    Accepts either:
      - Token symbol (e.g., "BONK" or "$BONK")
      - Or mint address (32–44 char Base58)
    Steps:
      1) Normalize: strip '$', whitespace.
      2) If length 32–44 alphanumeric → treat as mint.
         Else try resolve_symbol_to_mint() → mint.
         Special‐case "SOL" → WSOL mint.
      3) Fetch token decimals via getTokenSupply.
      4) If mint == USDC_MINT → return 1.0 USD.
      5) Call Jupiter quote API: amount = 10^decimals, outputMint = USDC_MINT.
      6) If Jupiter returns route → price = outAmount / 10^6.
      7) Else raise 404.
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
                    detail=f"'{identifier}' not a valid mint or known SYMBOL"
                )
            mint = resolved

    # 2) If user asked price for USDC itself, it's always 1.0
    if mint == USDC_MINT:
        return PriceResponse(address=mint, price_usd=1.0, source="static")

    # 3) Fetch decimals via getTokenSupply
    supply_resp = solana_client.get_token_supply(mint)
    if supply_resp.get("error"):
        raise HTTPException(status_code=502, detail="RPC error fetching token supply")
    value = supply_resp["result"]["value"]
    decimals = value.get("decimals")
    if decimals is None:
        raise HTTPException(status_code=404, detail="Cannot determine token decimals")

    # 4) Build Jupiter quote request for 1 token unit
    amount_raw = 10 ** decimals
    params = {
        "inputMint": mint,
        "outputMint": USDC_MINT,
        "amount": amount_raw,
        "slippageBps": 50  # 0.50% slippage allowance
    }

    try:
        r = requests.get(JUPITER_QUOTE_API, params=params, timeout=5)
        r.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Jupiter API error: {e}")

    data = r.json().get("data", [])
    if not data:
        raise HTTPException(status_code=404, detail="No liquidity route found for this token")

    # 5) Take the best route (first element)
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
# 7) Override OpenAPI schema with only production server
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
