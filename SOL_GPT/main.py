import os
import httpx
import logging
from fastapi import FastAPI, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from functools import lru_cache
from typing import List, Dict, Optional

# ======= INITIALIZATION =======
app = FastAPI(
    title="Ultimate Solana API",
    version="2.0",
    description="Complete Solana toolkit: Prices, Wallets & Swaps",
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
JUPITER_QUOTE_API = "https://quote-api.jup.ag/v1/quote"
HELIUS_BALANCE_URL = "https://api.helius.xyz/v0/addresses/{address}/balances"

# ======= SECURITY =======
API_KEY_NAME = "X-API-KEY"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def validate_api_key(api_key: str = Security(api_key_header)):
    if api_key != os.getenv("INTERNAL_API_KEY"):
        raise HTTPException(status_code=403, detail="Invalid API Key")

# ======= UTILITIES =======
@lru_cache(maxsize=1)
def load_token_list() -> List[Dict]:
    """Cached token list loader with fallback URLs"""
    urls = [
        "https://cdn.jsdelivr.net/gh/solana-labs/token-list@main/src/tokens/solana.tokenlist.json",
        "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
    ]
    for url in urls:
        try:
            response = httpx.get(url, timeout=10)
            return response.json().get("tokens", [])
        except Exception as e:
            logging.warning(f"Failed to load token list from {url}: {e}")
    return []

def resolve_identifier(identifier: str) -> str:
    """Convert symbol to mint address"""
    identifier = identifier.strip().upper()
    
    if identifier in ["SOL", "WSOL"]:
        return WSOL_MINT
    if identifier == "USDC":
        return USDC_MINT
    
    # Check if already a mint address
    if len(identifier) in [32, 44] and identifier.isalnum():
        return identifier
    
    # Search token list
    for token in load_token_list():
        if token.get("symbol", "").upper() == identifier:
            return token["address"]
    
    raise HTTPException(404, detail=f"Token '{identifier}' not found")

# ======= CORE FUNCTIONALITY =======
@app.get("/")
async def root():
    return {
        "endpoints": {
            "price": "/price/{token_symbol_or_mint}",
            "wallet": "/wallet/{address}",
            "swap": "/swap?inputMint=...&outputMint=...&amount=..."
        },
        "example": "/price/SOL or /wallet/your_wallet_address"
    }

@app.get("/price/{identifier}")
async def get_price(identifier: str):
    """Get price and volume for any token"""
    mint = resolve_identifier(identifier)
    
    # Special case for USDC
    if mint == USDC_MINT:
        return {
            "mint": mint,
            "price": 1.0,
            "volume_24h": None,
            "source": "static"
        }
    
    # Try Jupiter first
    try:
        async with httpx.AsyncClient() as client:
            params = {
                "inputMint": mint,
                "outputMint": USDC_MINT,
                "amount": 10**6,  # 1 token if 6 decimals
                "slippageBps": 50
            }
            response = await client.get(JUPITER_QUOTE_API, params=params, timeout=5)
            data = response.json()
            return {
                "mint": mint,
                "price": int(data["outAmount"]) / 10**6,
                "volume_24h": None,
                "source": "jupiter"
            }
    except Exception as e:
        logging.warning(f"Jupiter failed: {e}")
    
    # Fallback to Birdeye if available
    if os.getenv("BIRDEYE_API_KEY"):
        try:
            async with httpx.AsyncClient() as client:
                headers = {"X-API-KEY": os.getenv("BIRDEYE_API_KEY")}
                response = await client.get(
                    f"https://public-api.birdeye.so/public/price?address={mint}",
                    headers=headers,
                    timeout=5
                )
                data = response.json()
                return {
                    "mint": mint,
                    "price": float(data["data"]["value"]),
                    "volume_24h": float(data["data"]["volume"]["value"]),
                    "source": "birdeye"
                }
        except Exception as e:
            logging.warning(f"Birdeye failed: {e}")
    
    raise HTTPException(503, detail="All price sources failed")

@app.get("/wallet/{address}")
async def get_wallet_balances(address: str):
    """Get SOL and SPL token balances for a wallet"""
    if len(address) < 32 or len(address) > 44:
        raise HTTPException(400, detail="Invalid Solana address")
    
    if not os.getenv("HELIUS_API_KEY"):
        raise HTTPException(501, detail="Helius API key not configured")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                HELIUS_BALANCE_URL.format(address=address),
                params={"api-key": os.getenv("HELIUS_API_KEY")},
                timeout=10
            )
            data = response.json()
            
            sol_balance = data.get("lamports", 0) / 10**9
            tokens = []
            
            for token in data.get("tokens", []):
                tokens.append({
                    "mint": token["mint"],
                    "amount": str(int(token["amount"]) / 10**token["decimals"]),
                    "decimals": token["decimals"]
                })
            
            return {
                "address": address,
                "sol_balance": sol_balance,
                "tokens": tokens
            }
    except Exception as e:
        raise HTTPException(502, detail=f"Helius error: {str(e)}")

@app.get("/swap")
async def simulate_swap(
    inputMint: str = Query(..., description="Input token mint address"),
    outputMint: str = Query(..., description="Output token mint address"),
    amount: float = Query(..., description="Amount to swap"),
    slippageBps: int = Query(50, description="Slippage in basis points (1/100th percent)")
):
    """Get real swap quote from Jupiter"""
    try:
        input_mint = resolve_identifier(inputMint)
        output_mint = resolve_identifier(outputMint)
        
        async with httpx.AsyncClient() as client:
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": int(amount * 10**6),  # Assuming 6 decimals
                "slippageBps": slippageBps
            }
            response = await client.get(JUPITER_QUOTE_API, params=params, timeout=10)
            data = response.json()
            
            return {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "inAmount": str(amount),
                "outAmount": str(int(data["outAmount"]) / 10**6),
                "slippageBps": slippageBps,
                "route": data.get("route", []),
                "priceImpact": data.get("priceImpactPct"),
                "platformFee": data.get("platformFee")
            }
    except Exception as e:
        raise HTTPException(502, detail=f"Swap error: {str(e)}")

@app.get("/")
def root():
    return {
        "message": "Endpoints: /price/{identifier} (mint or symbol), /wallet/{address} (via Helius optional), /swap (mock)."
    }

# ======= HEALTH ENDPOINT =======
@app.get("/health")
async def health_check():
    return {"status": "healthy", "services": ["jupiter", "helius"]}
