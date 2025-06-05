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
    version="0.1.0",
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
    Download & cache SPL Token List → returns { SYMBOL (upper): mintAddress }.
    """
    try:
        resp = requests.get(TOKEN_LIST_URL, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Unable to fetch SPL Token List: {e}")

    tokens_payload = resp.json().get("tokens", [])
    mapping: Dict[str, str] = {}
    for entry in tokens_payload:
        symbol = entry.get("symbol", "").upper()
        mint = entry.get("address", "")
        if symbol and mint:
            mapping[symbol] = mint
    return mapping


def resolve_symbol_to_mint(symbol: str) -> Optional[str]:
    return get_token_list_map().get(symbol.upper())


@app.get("/")
def root():
    return {
        "message": "Welcome to SolGPT API. Available endpoints: /wallet/{address}, /price/{token}, /swap"
    }


@app.get("/wallet/{address}", response_model=WalletResponse)
def get_wallet_balance(address: str):
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
    if 32 <= len(identifier) <= 44:
        mint = identifier
        symbol = None
    else:
        resolved_mint = resolve_symbol_to_mint(identifier)
        if not resolved_mint:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{identifier}' is not a valid mint or known symbol. "
                    "Provide a 32–44 char mint address or an SPL token symbol."
                )
            )
        mint = resolved_mint
        symbol = identifier.upper()

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
    return PriceResponse(address=mint, price=price_entry.get("price", 0.0), symbol=symbol)


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


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # Only include the production (HTTPS) URL here
    openapi_schema["servers"] = [
        {
            "url": "https://solgpt-production.up.railway.app",
            "description": "Production (Railway)"
        }
    ]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi
