import os
from fastapi import FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from functools import lru_cache
import httpx
import logging
from typing import Optional

# ======= INITIALIZATION =======
app = FastAPI(
    title="Solana Price API",
    description="Get prices and volumes for any Solana token",
    version="1.0",
    servers=[{"url": "https://your-app-name.up.railway.app", "description": "Production"}],
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======= CONSTANTS =======
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL_MINT = "So11111111111111111111111111111111111111112"

# ======= SECURITY =======
API_KEY_NAME = "X-API-KEY"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def validate_api_key(api_key: str = Security(api_key_header)):
    if api_key != os.getenv("INTERNAL_API_KEY"):
        raise HTTPException(status_code=403, detail="Invalid API Key")

# ======= UTILITIES =======
@lru_cache(maxsize=1)
def load_token_list():
    """Load token list with multiple fallback URLs"""
    urls = [
        "https://cdn.jsdelivr.net/gh/solana-labs/token-list@main/src/tokens/solana.tokenlist.json",
        "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
    ]
    for url in urls:
        try:
            r = httpx.get(url, timeout=10)
            return r.json().get("tokens", [])
        except Exception as e:
            logging.warning(f"Failed to load token list from {url}: {e}")
    return []

# ======= CORE FUNCTIONS =======
async def get_token_data(mint: str):
    """Get price and volume from multiple sources"""
    sources = [
        ("jupiter", f"https://price.jup.ag/v1/price?id={mint}&vsToken={USDC_MINT}"),
        ("birdeye", f"https://public-api.birdeye.so/public/price?address={mint}"),
    ]
    
    for source_name, url in sources:
        try:
            async with httpx.AsyncClient() as client:
                headers = {}
                if source_name == "birdeye" and os.getenv("BIRDEYE_API_KEY"):
                    headers["X-API-KEY"] = os.getenv("BIRDEYE_API_KEY")
                
                r = await client.get(url, headers=headers, timeout=3)
                data = r.json()
                
                if source_name == "jupiter":
                    return {
                        "price": float(data["data"]["price"]),
                        "volume_24h": None,
                        "source": "jupiter"
                    }
                elif source_name == "birdeye":
                    return {
                        "price": float(data["data"]["value"]),
                        "volume_24h": float(data["data"]["volume"]["value"]),
                        "source": "birdeye"
                    }
        except Exception as e:
            logging.warning(f"{source_name} failed for {mint}: {e}")
            continue
    
    raise HTTPException(503, detail="All price sources failed")

# ======= API ENDPOINTS =======
@app.get("/", include_in_schema=False)
async def root():
    return {
        "message": "Solana Price API - use /price/{token_symbol_or_mint}",
        "example": "/price/SOL or /price/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    }

@app.get("/price/{identifier}")
async def get_price(identifier: str):
    """Get price for any Solana token by symbol or mint address"""
    identifier = identifier.strip().upper()
    
    # Special cases
    if identifier in ["SOL", "WSOL"]:
        mint = WSOL_MINT
    elif identifier == "USDC":
        return {
            "mint": USDC_MINT,
            "price": 1.0,
            "volume_24h": None,
            "source": "static"
        }
    else:
        # Check if it's a mint address
        if len(identifier) in [32, 44] and identifier.isalnum():
            mint = identifier
        else:
            # Search token list
            for token in load_token_list():
                if token.get("symbol", "").upper() == identifier:
                    mint = token["address"]
                    break
            else:
                raise HTTPException(404, detail=f"Token '{identifier}' not found")
    
    return await get_token_data(mint)

# ======= HEALTH CHECK =======
@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# ======= ERROR HANDLERS =======
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )
