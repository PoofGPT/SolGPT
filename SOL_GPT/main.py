import os
from fastapi import FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from functools import lru_cache
import httpx
import logging
from typing import Optional

# ======= SETUP =======
app = FastAPI(title="Ultimate Solana Price API")

# Security
API_KEY_NAME = "X-API-KEY"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def validate_api_key(api_key: str = Security(api_key_header)):
    if api_key != os.getenv("INTERNAL_API_KEY"):
        raise HTTPException(status_code=403, detail="Invalid API Key")

# Config
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")

# ======= CORE FUNCTIONS =======
@lru_cache(maxsize=1000)
async def resolve_symbol(symbol: str) -> Optional[str]:
    """Check all sources for token symbol"""
    symbol = symbol.upper().strip("$")
    
    # 1. Check official token list
    tokens = load_token_list()
    for t in tokens:
        if t.get("symbol", "").upper() == symbol:
            return t["address"]
    
    # 2. Check CoinGecko
    if COINGECKO_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"https://api.coingecko.com/api/v3/coins/solana/contract/{symbol}",
                    headers={"x-cg-pro-api-key": COINGECKO_API_KEY},
                    timeout=3
                )
                if r.status_code == 200:
                    return r.json()["contract_address"]
        except:
            pass
    
    return None

async def get_price_with_volume(mint: str):
    """Get best available price with volume data"""
    # Try Jupiter first
    try:
        async with httpx.AsyncClient() as client:
            jupiter_params = {
                "id": mint,
                "vsToken": USDC_MINT
            }
            r = await client.get("https://price.jup.ag/v1/price", params=jupiter_params, timeout=2)
            data = r.json()["data"]
            return {
                "price": float(data["price"]),
                "volume_24h": None,  # Jupiter doesn't provide volume
                "source": "jupiter"
            }
    except:
        pass
    
    # Fallback to Birdeye
    if BIRDEYE_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                headers = {"X-API-KEY": BIRDEYE_API_KEY}
                r = await client.get(
                    f"https://public-api.birdeye.so/public/price?address={mint}",
                    headers=headers,
                    timeout=2
                )
                data = r.json()["data"]
                return {
                    "price": float(data["value"]),
                    "volume_24h": float(data["volume"]["value"]),
                    "source": "birdeye"
                }
        except:
            pass
    
    # Final fallback to CoinGecko
    if COINGECKO_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"https://api.coingecko.com/api/v3/coins/solana/contract/{mint}",
                    headers={"x-cg-pro-api-key": COINGECKO_API_KEY},
                    timeout=3
                )
                data = r.json()["market_data"]
                return {
                    "price": float(data["current_price"]["usd"]),
                    "volume_24h": float(data["total_volume"]["usd"]),
                    "source": "coingecko"
                }
        except:
            pass
    
    return None

# ======= API ENDPOINTS =======
@app.get("/price/{identifier}", dependencies=[Security(validate_api_key)])
async def get_full_price_data(identifier: str):
    # Resolve identifier to mint
    if identifier.upper() in ["SOL", "WSOL"]:
        mint = WSOL_MINT
    elif identifier.upper() == "USDC":
        return {
            "mint": USDC_MINT,
            "price": 1.0,
            "volume_24h": None,
            "source": "static"
        }
    elif len(identifier) in [32, 44] and identifier.isalnum():
        mint = identifier
    else:
        mint = await resolve_symbol(identifier)
        if not mint:
            raise HTTPException(404, detail=f"Token '{identifier}' not found in any registry")

    # Get data
    result = await get_price_with_volume(mint)
    if not result:
        raise HTTPException(404, detail="No price data available")

    return {
        "mint": mint,
        "symbol": identifier.upper(),
        **result
    }

# ======= UTILITIES =======
@lru_cache(maxsize=1)
def load_token_list():
    """Load token list with fallback"""
    urls = [
        "https://cdn.jsdelivr.net/gh/solana-labs/token-list@main/src/tokens/solana.tokenlist.json",
        "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
    ]
    for url in urls:
        try:
            r = httpx.get(url, timeout=10)
            return r.json()["tokens"]
        except:
            continue
    return []
