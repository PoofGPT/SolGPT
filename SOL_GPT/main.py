# main.py

import os
import requests
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict
from functools import lru_cache

app = FastAPI(
    title="SolGPT API",
    servers=[
        {"url": "http://localhost:8000", "description": "Local development"},
        {"url": "https://solgpt-production.up.railway.app", "description": "Production"}
    ],
)

# ────────────────────────────────────────────────────────────────────────────
# 1) Load Helius API key from environment
# ────────────────────────────────────────────────────────────────────────────
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
if not HELIUS_API_KEY:
    raise RuntimeError("Please set the HELIUS_API_KEY environment variable")


# ────────────────────────────────────────────────────────────────────────────
# 2) Pydantic models for responses
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
    price: float
    symbol: Optional[str]


# ────────────────────────────────────────────────────────────────────────────
# 3) Caching & resolving SPL Token List (symbol → mint) via on‐chain list
# ────────────────────────────────────────────────────────────────────────────
TOKEN_LIST_URL = (
    "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
)


@lru_cache(maxsize=1)
def get_token_list_map() -> Dict[str, str]:
    """
    Download & cache SPL Token List → returns { SYMBOL (upper): mintAddress }
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


def resolve_symbol_to_mint(symbol: str) -> Optional[str]:
    """
    Given an uppercase or lowercase symbol, return its mint address (or None).
    """
    return get_token_list_map().get(symbol.upper())


# ────────────────────────────────────────────────────────────────────────────
# 4) Root endpoint
# ────────────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "message": "Welcome to SolGPT API. Available endpoints: /wallet/{address}, /price/{token}, /swap"
    }


# ────────────────────────────────────────────────────────────────────────────
# 5) GET /wallet/{address} → fetch SOL + SPL balances via Helius
# ────────────────────────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────────────────────────
# 6) GET /price/{identifier} → mint or symbol → price via Helius
# ────────────────────────────────────────────────────────────────────────────
@app.get("/price/{identifier}", response_model=PriceResponse)
def get_token_price(identifier: str):
    """
    If identifier length is 32–44 chars, treat as mint.
    Otherwise, treat as symbol and resolve mint.
    Then fetch USD price via Helius.
    """
    if 32 <= len(identifier) <= 44:
        mint = identifier
        symbol = None
    else:
        resolved = resolve_symbol_to_mint(identifier)
        if not resolved:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{identifier}' is not a valid mint or known symbol. "
                    "Use a valid 32–44 char mint address or an SPL token symbol."
                )
            )
        mint = resolved
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


# ────────────────────────────────────────────────────────────────────────────
# 7) GET /swap → mock swap simulation (inputMint, outputMint, amount)
# ────────────────────────────────────────────────────────────────────────────
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
