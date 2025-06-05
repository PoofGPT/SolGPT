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
    version="0.1.1",
    description="SolGPT—check Solana wallet balances and token prices via Helius",
)

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
if not HELIUS_API_KEY:
    raise RuntimeError("Please set the Helius_API_KEY environment variable")


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
    price: float
    symbol: Optional[str]


TOKEN_LIST_URL = (
    "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
)


@lru_cache(maxsize=1)
def get_token_list_map() -> Dict[str, str]:
    """
    Download & cache the SPL Token List → returns { SYMBOL (upper): mintAddress }.
    """
    try:
        resp = requests.get(TOKEN_LIST_URL, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Unable to fetch SPL Token List: {e}")

    payload = resp.json().get("tokens", [])
    mapping: Dict[str, str] = {}
    for entry in payload:
        symbol = entry.get("symbol", "").upper()
        mint = entry.get("address", "")
        if symbol and mint:
            mapping[symbol] = mint
    return mapping


def resolve_symbol_to_mint(raw_symbol: str) -> Optional[str]:
    """
    Strip leading '$' or whitespace, uppercase, and look up in SPL Token List.
    """
    symbol = raw_symbol.lstrip("$ ").upper()
    return get_token_list_map().get(symbol)


@app.get("/")
def root():
    return {
        "message": "Welcome to SolGPT API. Available endpoints: /wallet/{address}, /price/{token}, /swap"
    }


@app.get("/wallet/{address}", response_model=WalletResponse)
def get_wallet_balance(address: str):
    """
    Returns SOL balance and SPL token balances for a given Solana wallet via Helius.
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
        raise HTTPException(
            status_code=502,
            detail=f"Error fetching balances from Helius: {e}"
        )

    data = resp.json()
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
        tokens.append(
            TokenBalance(
                mint=mint,
                amount=str(human_amt),
                decimals=decimals,
            )
        )

    return WalletResponse(address=address, sol_balance=sol_balance, tokens=tokens)


@app.get("/price/{identifier}", response_model=PriceResponse)
def get_token_price(identifier: str):
    """
    Accepts a token symbol (with or without leading '$') or a mint address.
    Strips '$' if present, resolves symbol → mint via SPL Token List,
    then fetches USD price via Helius.
    """
    # Strip leading '$' and whitespace
    raw = identifier.strip()
    if raw.startswith("$"):
        raw = raw.lstrip("$").strip()

    # Determine if 'raw' is a mint (32-44 char base58) or symbol
    if 32 <= len(raw) <= 44 and all(c.isalnum() for c in raw):
        mint = raw
        symbol = None
    else:
        resolved = resolve_symbol_to_mint(raw)
        if not resolved:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{identifier}' is not a valid mint or known symbol. "
                    "Use a 32–44 char mint address or an SPL token symbol (e.g., BONK, $BONK)."
                )
            )
        mint = resolved
        symbol = raw.upper()

    helius_url = (
        f"https://api.helius.xyz/v0/token/price?addresses[]={mint}&api-key={HELIUS_API_KEY}"
    )
    try:
        resp = requests.get(helius_url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error fetching price from Helius: {e}")

    prices = resp.json()
    if not isinstance(prices, list) or not prices:
        raise HTTPException(status_code=404, detail=f"No price data found for mint {mint}")

    price_entry = prices[0]
    # If Helius returns null or missing price, treat it as not found
    if price_entry.get("price") is None:
        raise HTTPException(status_code=404, detail=f"No USD price available for {mint}")

    return PriceResponse(address=mint, price=price_entry.get("price", 0.0), symbol=symbol)


@app.get("/swap")
def simulate_swap(
    input_mint: str = Query(..., alias="inputMint"),
    output_mint: str = Query(..., alias="outputMint"),
    amount: float = Query(...),
):
    """
    Simulate a swap: returns a mocked route and estimated output.
    Both query styles work:
      /swap?inputMint=<mint>&outputMint=<mint>&amount=2
      /swap?input_mint=<mint>&output_mint=<mint>&amount=2
    """
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


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # Only include your live HTTPS endpoint here
    openapi_schema["servers"] = [
        {
            "url": "https://solgpt-production.up.railway.app",
            "description": "Production (Railway)"
        }
    ]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi
