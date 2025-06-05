# main.py

import os
import requests
import httpx
import logging
from fastapi import FastAPI, HTTPException, Query
from fastapi.openapi.utils import get_openapi
from fastapi.middleware.cors import CORSMiddleware
from functools import lru_cache
from typing import List, Dict

app = FastAPI(
    title="Ultimate Solana Price API",
    version="1.0.1",
    description="Fetch USD prices for any Solana SPL token (obscure or popular) via Jupiter & CoinGecko",
)

# ─────────────────────────────────────────────────────────────────────────────
# Enable CORS so docs/examples can load properly
# ─────────────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Constants: common token mints + API endpoints
# ─────────────────────────────────────────────────────────────────────────────
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL_MINT = "So11111111111111111111111111111111111111112"

JUPITER_QUOTE_API       = "https://quote-api.jup.ag/v1/quote"
HELIUS_BALANCE_URL      = "https://api.helius.xyz/v0/addresses/{address}/balances"
HELIUS_TOKEN_METADATA   = "https://api.helius.xyz/v0/tokens"

# On-chain SPL token list (symbol→mint)
TOKEN_LIST_URLS = [
    "https://cdn.jsdelivr.net/gh/solana-labs/token-list@main/src/tokens/solana.tokenlist.json",
    "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
]

# CoinGecko endpoints → no API key needed
COINGECKO_LIST_URL  = "https://api.coingecko.com/api/v3/coins/list?include_platform=true"
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"


# ─────────────────────────────────────────────────────────────────────────────
# Utility: load SPL token list (symbol→address)
# ─────────────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def load_spl_token_list() -> List[Dict]:
    """
    Download and cache the SPL token list from GitHub (two fallback URLs).
    Each entry has fields: symbol, address (mint), name, etc.
    """
    for url in TOKEN_LIST_URLS:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            return r.json().get("tokens", [])
        except Exception as e:
            logging.warning(f"Failed to load SPL token list from {url}: {e}")
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Utility: load CoinGecko's entire coin list (with platforms)
# ─────────────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def load_coingecko_list() -> List[Dict]:
    """
    Download and cache CoinGecko's coin list including platform addresses.
    Each entry includes: id, symbol, name, platforms (e.g. {"solana":<mint>})
    """
    r = requests.get(COINGECKO_LIST_URL, timeout=10)
    r.raise_for_status()
    return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# Build lookup tables: mint→CoinGeckoID, symbol→CoinGeckoID
# ─────────────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def build_coingecko_mappings():
    coin_list = load_coingecko_list()
    mint_to_cgid: Dict[str, str]   = {}
    symbol_to_cgid: Dict[str, str] = {}
    for entry in coin_list:
        cg_id = entry.get("id")              # e.g. "bonk", "rizzmas", etc.
        sym   = entry.get("symbol", "").upper()
        symbol_to_cgid[sym] = cg_id

        platforms = entry.get("platforms", {})
        sol_mint = platforms.get("solana")    # If tracked on Solana, holds its mint address
        if sol_mint:
            mint_to_cgid[sol_mint] = cg_id

    return mint_to_cgid, symbol_to_cgid


# ─────────────────────────────────────────────────────────────────────────────
# Resolve “symbol” (or raw mint) → SPL mint address
# ─────────────────────────────────────────────────────────────────────────────
def resolve_symbol_or_mint(symbol: str) -> str:
    """
    1) Uppercase + strip any leading '$'.
    2) If it looks like a mint (32–44 length & alphanumeric), return as-is.
    3) If it’s “SOL” or “WSOL”, return WSOL_MINT.
    4) If it’s “USDC”, return USDC_MINT.
    5) Otherwise, search SPL token list for symbol match → return its mint.
    6) If still not found, raise 404.
    """
    raw = symbol.strip().upper().lstrip("$").strip()

    if raw in ("SOL", "WSOL"):
        return WSOL_MINT
    if raw == "USDC":
        return USDC_MINT

    # If it resembles a mint address (32–44 chars, alphanumeric)
    if 32 <= len(raw) <= 44 and raw.isalnum():
        return raw

    # Otherwise, search SPL token list by symbol
    for token in load_spl_token_list():
        if token.get("symbol", "").upper() == raw:
            return token.get("address")

    raise HTTPException(status_code=404, detail=f"Token '{symbol}' not found")


# ─────────────────────────────────────────────────────────────────────────────
# Root endpoint
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "message": "Solana Price API",
        "endpoints": {
            "price": "/price/{symbol_or_mint}",
            "wallet": "/wallet/{address}",
            "swap": "/swap?inputMint=...&outputMint=...&amount=..."
        },
        "example": "/price/BORK  or  /wallet/4Nd1m…abc"
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1) GET /price/{symbol_or_mint}
#    → Returns USD price for any SPL token (popular or obscure)
#    Logic:
#      • Resolve symbol_or_mint → mint
#      • If mint == USDC_MINT → return 1.0
#      • Fetch decimals via Helius metadata
#      • Try Jupiter quote for 1.0 token → USDC (on-chain)
#      • If Jupiter fails, fallback to CoinGecko (off-chain)
# ─────────────────────────────────────────────────────────────────────────────
@app.get(
    "/price/{symbol_or_mint}",
    summary="Get SPL token price",
    description="Fetch USD price of any Solana SPL token by mint or symbol (Jupiter → CoinGecko fallback).",
)
async def get_token_price(symbol_or_mint: str):
    # 1) Resolve to mint address
    try:
        mint = resolve_symbol_or_mint(symbol_or_mint)
    except HTTPException as e:
        raise e

    # 2) If user requested USDC, it’s always $1.00
    if mint == USDC_MINT:
        return {"mint": mint, "price": 1.0, "source": "static"}

    # 3) Fetch token decimals via Helius
    helius_key = os.getenv("HELIUS_API_KEY")
    if not helius_key:
        raise HTTPException(status_code=501, detail="Helius API key not configured")

    try:
        resp = requests.get(
            HELIUS_TOKEN_METADATA,
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
        logging.warning(f"Helius metadata error: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch token metadata")

    # 4) Try Jupiter: quote 1.0 token (10**decimals) → USDC
    try:
        async with httpx.AsyncClient() as client:
            params = {
                "inputMint": mint,
                "outputMint": USDC_MINT,
                "amount": 10 ** decimals,
                "slippageBps": 50
            }
            jresp = await client.get(JUPITER_QUOTE_API, params=params, timeout=5)
            jresp.raise_for_status()
            jdata = jresp.json().get("data", [])
            if jdata:
                out_amount = jdata[0].get("outAmount")
                if out_amount:
                    price_usd = int(out_amount) / 10**6
                    return {"mint": mint, "price": price_usd, "source": "jupiter"}
    except Exception as e:
        logging.warning(f"Jupiter quote failed: {e}")

    # 5) Fallback → CoinGecko. Build mappings if not already.
    mint_to_cgid, symbol_to_cgid = build_coingecko_mappings()
    cg_id = mint_to_cgid.get(mint)

    # If we still don’t have a CG ID, maybe input was a symbol:
    if not cg_id:
        sym = symbol_or_mint.strip().upper().lstrip("$")
        cg_id = symbol_to_cgid.get(sym)

    if not cg_id:
        raise HTTPException(status_code=404, detail="Token not found on CoinGecko")

    # 6) Fetch USD price from CoinGecko
    try:
        price_resp = requests.get(
            COINGECKO_PRICE_URL,
            params={"ids": cg_id, "vs_currencies": "usd"},
            timeout=5
        )
        price_resp.raise_for_status()
        price_data = price_resp.json()
        usd_price = price_data.get(cg_id, {}).get("usd")
        if usd_price is None:
            raise HTTPException(status_code=404, detail="Price not available on CoinGecko")
        return {"mint": mint, "price": usd_price, "source": "coingecko"}
    except requests.RequestException as e:
        logging.warning(f"CoinGecko error: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch price from CoinGecko")


# ─────────────────────────────────────────────────────────────────────────────
# 2) GET /wallet/{address}
#    → Returns SOL & SPL token balances via Helius
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/wallet/{address}")
async def get_wallet_balances(address: str):
    if len(address) < 32 or len(address) > 44:
        raise HTTPException(status_code=400, detail="Invalid Solana address")

    helius_key = os.getenv("HELIUS_API_KEY")
    if not helius_key:
        raise HTTPException(status_code=501, detail="Helius API key not configured")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                HELIUS_BALANCE_URL.format(address=address),
                params={"api-key": helius_key},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()

            sol_balance = data.get("lamports", 0) / 10**9
            tokens = []
            for t in data.get("tokens", []):
                amt = int(t["amount"]) / (10**t["decimals"])
                tokens.append({
                    "mint": t["mint"],
                    "amount": str(amt),
                    "decimals": t["decimals"]
                })

            return {
                "address": address,
                "sol_balance": sol_balance,
                "tokens": tokens
            }
    except Exception as e:
        logging.warning(f"Helius wallet error: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch wallet balances")


# ─────────────────────────────────────────────────────────────────────────────
# 3) GET /swap
#    → Returns a real Jupiter swap quote. Accepts mint or symbol for input/output.
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/swap")
async def simulate_swap(
    inputMint: str = Query(..., description="Input token mint or symbol"),
    outputMint: str = Query(..., description="Output token mint or symbol"),
    amount: float = Query(..., description="Amount to swap"),
    slippageBps: int = Query(50, description="Slippage in basis points")
):
    try:
        in_mint  = resolve_symbol_or_mint(inputMint)
        out_mint = resolve_symbol_or_mint(outputMint)
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
        logging.warning(f"Swap error: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch swap quote")


# ─────────────────────────────────────────────────────────────────────────────
# 4) Health check
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health_check():
    return {"status": "healthy", "services": ["jupiter", "helius", "coingecko"]}


# ─────────────────────────────────────────────────────────────────────────────
# Override OpenAPI schema to point at your Railway domain
# ─────────────────────────────────────────────────────────────────────────────
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
