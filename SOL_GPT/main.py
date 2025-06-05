# main.py

import os
import requests
import httpx
import logging
from fastapi import FastAPI, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from functools import lru_cache
from typing import List, Dict, Optional

app = FastAPI(
    title="Ultimate Solana API",
    version="3.0",
    description="Complete Solana toolkit: Prices, Wallets & Swaps",
)

# Enable CORS so the docs UI can fetch
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======= CONSTANTS =======
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL_MINT = "So11111111111111111111111111111111111111112"
JUPITER_QUOTE_API = "https://quote-api.jup.ag/v1/quote"
HELIUS_BALANCE_URL = "https://api.helius.xyz/v0/addresses/{address}/balances"
HELIUS_TOKEN_METADATA_URL = "https://api.helius.xyz/v0/tokens"
TOKEN_LIST_URLS = [
    "https://cdn.jsdelivr.net/gh/solana-labs/token-list@main/src/tokens/solana.tokenlist.json",
    "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
]
COINGECKO_LIST_URL = "https://api.coingecko.com/api/v3/coins/list?include_platform=true"
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"

# ======= SECURITY =======
API_KEY_NAME = "X-API-KEY"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def validate_api_key(api_key: str = Security(api_key_header)):
    if api_key != os.getenv("INTERNAL_API_KEY"):
        raise HTTPException(status_code=403, detail="Invalid API Key")

# ======= UTILITIES =======
@lru_cache(maxsize=1)
def load_token_list() -> List[Dict]:
    """Download and cache SPL token list from fallback URLs."""
    for url in TOKEN_LIST_URLS:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            return r.json().get("tokens", [])
        except Exception as e:
            logging.warning(f"Failed to load token list from {url}: {e}")
    return []

@lru_cache(maxsize=1)
def load_coingecko_list() -> List[Dict]:
    """Download and cache CoinGecko coin list with platform details."""
    r = requests.get(COINGECKO_LIST_URL, timeout=10)
    r.raise_for_status()
    return r.json()

@lru_cache(maxsize=1)
def build_coingecko_mappings():
    """
    Build two lookup maps:
      - mint_to_cgid: { <solana_mint> : <coingecko_id> }
      - symbol_to_cgid: { <symbol_upper> : <coingecko_id> }
    """
    coin_list = load_coingecko_list()
    mint_to_cgid: Dict[str, str] = {}
    symbol_to_cgid: Dict[str, str] = {}
    for entry in coin_list:
        cg_id = entry.get("id")
        sym = entry.get("symbol", "").upper()
        symbol_to_cgid[sym] = cg_id
        platforms = entry.get("platforms", {})
        sol_mint = platforms.get("solana")
        if sol_mint:
            mint_to_cgid[sol_mint] = cg_id
    return mint_to_cgid, symbol_to_cgid

def resolve_identifier(identifier: str) -> str:
    """
    Convert symbol or mint to a valid Solana mint address.
    Raises HTTPException if not found.
    """
    id_clean = identifier.strip().upper()
    if id_clean in ["SOL", "WSOL"]:
        return WSOL_MINT
    if id_clean == "USDC":
        return USDC_MINT

    # If looks like a mint
    if 32 <= len(id_clean) <= 44 and id_clean.isalnum():
        return id_clean

    # Search SPL token list by symbol
    for token in load_token_list():
        if token.get("symbol", "").upper() == id_clean:
            return token.get("address")
    raise HTTPException(status_code=404, detail=f"Token '{identifier}' not found")

# ======= ENDPOINTS =======
@app.get("/")
async def root():
    return {
        "endpoints": {
            "price": "/price/{token_symbol_or_mint}",
            "wallet": "/wallet/{address}",
            "swap": "/swap?inputMint=...&outputMint=...&amount=..."
        },
        "example": "/price/SOL or /wallet/YourWalletAddress"
    }

@app.get("/price/{identifier}")
async def get_token_price(identifier: str):
    """
    Get USD price of any SPL token by mint address or symbol.
    Tries Jupiter first; if no route or error, falls back to CoinGecko.
    """
    mint = resolve_identifier(identifier)

    # Static for USDC
    if mint == USDC_MINT:
        return {"mint": mint, "price": 1.0, "source": "static"}

    # Step 1: get decimals via Helius
    helius_key = os.getenv("HELIUS_API_KEY")
    if not helius_key:
        raise HTTPException(status_code=501, detail="Helius API key not configured")
    try:
        resp = requests.get(
            HELIUS_TOKEN_METADATA_URL,
            params={"addresses[]": mint, "api-key": helius_key},
            timeout=5
        )
        resp.raise_for_status()
        tm_data = resp.json()
        if not isinstance(tm_data, list) or not tm_data:
            raise HTTPException(status_code=404, detail="Token metadata not found")
        decimals = tm_data[0].get("decimals")
        if decimals is None:
            raise HTTPException(status_code=404, detail="Decimals unavailable")
    except requests.RequestException as e:
        logging.warning(f"Helius metadata failed: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch token metadata")

    # Step 2: Try Jupiter quote for 1 token → USDC
    try:
        async with httpx.AsyncClient() as client:
            params = {
                "inputMint": mint,
                "outputMint": USDC_MINT,
                "amount": 10**decimals,
                "slippageBps": 50
            }
            r = await client.get(JUPITER_QUOTE_API, params=params, timeout=5)
            r.raise_for_status()
            data = r.json().get("data", [])
            if data:
                out_amount = data[0].get("outAmount")
                if out_amount:
                    price_usd = int(out_amount) / 10**6
                    return {"mint": mint, "price": price_usd, "source": "jupiter"}
    except Exception as e:
        logging.warning(f"Jupiter failed: {e}")

    # Step 3: Fallback to CoinGecko
    mint_to_cgid, symbol_to_cgid = build_coingecko_mappings()
    cg_id = mint_to_cgid.get(mint)
    if not cg_id:
        # also try symbol mapping if identifier was a symbol
        sym = identifier.strip().upper().lstrip("$")
        cg_id = symbol_to_cgid.get(sym)
    if not cg_id:
        raise HTTPException(status_code=404, detail="Token not found on CoinGecko")

    try:
        cg_resp = requests.get(
            COINGECKO_PRICE_URL,
            params={"ids": cg_id, "vs_currencies": "usd"},
            timeout=5
        )
        cg_resp.raise_for_status()
        price_data = cg_resp.json()
        usd_price = price_data.get(cg_id, {}).get("usd")
        if usd_price is None:
            raise HTTPException(status_code=404, detail="Price not available on CoinGecko")
        return {"mint": mint, "price": usd_price, "source": "coingecko"}
    except requests.RequestException as e:
        logging.warning(f"CoinGecko failed: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch price from CoinGecko")

@app.get("/wallet/{address}")
async def get_wallet_balances(address: str):
    """
    Get SOL & SPL token balances for a wallet via Helius.
    """
    if len(address) < 32 or len(address) > 44:
        raise HTTPException(status_code=400, detail="Invalid Solana address")

    helius_key = os.getenv("HELIUS_API_KEY")
    if not helius_key:
        raise HTTPException(status_code=501, detail="Helius API key not configured")

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                HELIUS_BALANCE_URL.format(address=address),
                params={"api-key": helius_key},
                timeout=10
            )
            r.raise_for_status()
            data = r.json()
            sol_balance = data.get("lamports", 0) / 10**9
            tokens = []
            for t in data.get("tokens", []):
                amt = int(t["amount"]) / 10**t["decimals"]
                tokens.append({"mint": t["mint"], "amount": str(amt), "decimals": t["decimals"]})
            return {"address": address, "sol_balance": sol_balance, "tokens": tokens}
    except Exception as e:
        logging.warning(f"Helius balance failed: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch wallet balances")

@app.get("/swap")
async def simulate_swap(
    inputMint: str = Query(..., description="Input token mint or symbol"),
    outputMint: str = Query(..., description="Output token mint or symbol"),
    amount: float = Query(..., description="Amount to swap"),
    slippageBps: int = Query(50, description="Slippage in basis points")
):
    """
    Get real swap quote from Jupiter. Accepts mint or symbol for input/output.
    """
    try:
        in_mint = resolve_identifier(inputMint)
        out_mint = resolve_identifier(outputMint)
    except HTTPException as e:
        raise e

    try:
        async with httpx.AsyncClient() as client:
            params = {
                "inputMint": in_mint,
                "outputMint": out_mint,
                "amount": int(amount * 10**6),  # assume 6 decimals
                "slippageBps": slippageBps
            }
            r = await client.get(JUPITER_QUOTE_API, params=params, timeout=10)
            r.raise_for_status()
            data = r.json().get("data", [])
            if not data:
                raise HTTPException(status_code=404, detail="No liquidity route found")
            best = data[0]
            return {
                "inputMint": in_mint,
                "outputMint": out_mint,
                "inAmount": str(amount),
                "outAmount": str(int(best["outAmount"]) / 10**6),
                "slippageBps": slippageBps,
                "route": best.get("route", []),
                "priceImpact": best.get("priceImpactPct"),
                "platformFee": best.get("platformFee")
            }
    except HTTPException as e:
        raise e
    except Exception as e:
        logging.warning(f"Swap failed: {e}")
        raise HTTPException(status_code=502, detail="Swap error")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "services": ["jupiter", "helius", "coingecko"]}

# ======= OVERRIDE OPENAPI SCHEMA =======
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
        {"url": "https://striking-illumination.up.railway.app", "description": "Production"}
    ]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
