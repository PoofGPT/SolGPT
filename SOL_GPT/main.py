# main.py

import os
import requests
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict
from functools import lru_cache

app = FastAPI()

# ────────────────────────────────────────────────────────────────────────────
# 1) Load Helius API key from the environment
# ────────────────────────────────────────────────────────────────────────────
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
if not HELIUS_API_KEY:
    # If running locally, set: export HELIUS_API_KEY="YOUR_KEY_HERE"
    raise RuntimeError("HELIUS_API_KEY environment variable is not set")


# ────────────────────────────────────────────────────────────────────────────
# 2) Pydantic models for structured responses
# ────────────────────────────────────────────────────────────────────────────
class TokenBalance(BaseModel):
    mint: str
    amount: str
    decimals: int


class WalletResponse(BaseModel):
    address: str
    sol_balance: float
    tokens: List[TokenBalance]


# ────────────────────────────────────────────────────────────────────────────
# 3) Caching & resolving SPL Token List (symbol → mint-address mapping)
# ────────────────────────────────────────────────────────────────────────────
TOKEN_LIST_URL = (
    "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
)

@lru_cache(maxsize=1)
def get_token_list_map() -> Dict[str, str]:
    """
    Download and cache the on-chain SPL Token List, returning a dict:
       { SYMBOL (uppercase): mintAddress }
    """
    try:
        r = requests.get(TOKEN_LIST_URL, timeout=10)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        # If the token list fails, symbol resolution will not work
        raise RuntimeError(f"Unable to fetch SPL Token List: {e}")

    obj = r.json()
    entries = obj.get("tokens", [])
    symbol_to_mint: Dict[str, str] = {}
    for entry in entries:
        sym = entry.get("symbol", "").upper()
        maddr = entry.get("address", "")
        if sym and maddr:
            symbol_to_mint[sym] = maddr
    return symbol_to_mint


def resolve_symbol_to_mint(symbol: str) -> Optional[str]:
    """
    If `symbol` (case-insensitive) exists in the SPL Token List, return its mint.
    Else return None.
    """
    return get_token_list_map().get(symbol.upper())


# ────────────────────────────────────────────────────────────────────────────
# 4) GET /wallet/{address}  →  returns SOL + SPL balances via Helius
# ────────────────────────────────────────────────────────────────────────────
@app.get("/wallet/{address}", response_model=WalletResponse)
def get_wallet_balance(address: str):
    """
    Fetch SOL and SPL token balances for a given Solana wallet address via Helius.
    Always returns JSON (even if zero balance). Raises 400 for invalid address format.
    """
    # Basic Base58 length check: 32–44 characters
    if len(address) < 32 or len(address) > 44:
        raise HTTPException(status_code=400, detail="Invalid Solana address format.")

    helius_url = (
        f"https://api.helius.xyz/v0/addresses/{address}/balances"
        f"?api-key={HELIUS_API_KEY}"
    )
    try:
        resp = requests.get(helius_url, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=502, detail=f"Helius RPC error while fetching wallet balances: {e}"
        )

    data = resp.json()
    # Helius returns:
    #   { "owner": "...", "lamports": <int>, "tokens": [ { "mint": "...", "amount": "123456", "decimals": 6, ... }, ... ] }

    sol_lamports = data.get("lamports", 0)
    sol_balance = sol_lamports / 1e9  # convert lamports → SOL

    token_list = []
    for t in data.get("tokens", []):
        amt_str = t.get("amount", "0")
        dec = t.get("decimals", 0)
        try:
            human_amt = int(amt_str) / (10 ** dec) if dec >= 0 else int(amt_str)
        except Exception:
            human_amt = 0
        token_list.append(
            TokenBalance(
                mint=t.get("mint", ""),
                amount=str(human_amt),
                decimals=dec,
            )
        )

    return WalletResponse(address=address, sol_balance=sol_balance, tokens=token_list)


# ────────────────────────────────────────────────────────────────────────────
# 5) GET /price/{identifier}
#    Accepts either a mint address (32–44 chars) or a symbol (e.g. "BONK", "RIZZMAS")
# ────────────────────────────────────────────────────────────────────────────
@app.get("/price/{identifier}")
def get_token_price(identifier: str):
    """
    Fetch token price by either:
      - Mint address (32–44 characters)
      - Symbol (e.g. "BONK", "RIZZMAS"), resolved via the SPL Token List
    Once we have a mint, call Helius Price API:
      https://api.helius.xyz/v0/token/price?addresses[]=<mint>&api-key=<KEY>
    """
    # Decide if `identifier` is a mint or a symbol:
    mint: Optional[str] = None

    if 32 <= len(identifier) <= 44:
        # Looks like a mint (Base58 length)
        mint = identifier
    else:
        # Treat as symbol → look up in token list
        mint = resolve_symbol_to_mint(identifier)
        if not mint:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"‘{identifier}’ is neither a valid mint (32–44 chars) nor a known symbol.\n"
                    f"Make sure you pass an on‐chain mint address or a valid token symbol (e.g. BONK → DezXbQ3iE4U6siJ33rMZ9Gx8ZUGGLgtd4MUCZ3B9hJtG)."
                ),
            )

    helius_url = (
        "https://api.helius.xyz/v0/token/price"
        f"?addresses[]={mint}"
        f"&api-key={HELIUS_API_KEY}"
    )
    try:
        resp = requests.get(helius_url, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Helius price API error: {e}")

    data = resp.json()
    # Helius returns a list such as [ { "address": mint, "price": 0.00000123, … } ]
    if not isinstance(data, list) or len(data) == 0:
        raise HTTPException(status_code=404, detail=f"No price data found for mint {mint}")

    return data[0]


# ────────────────────────────────────────────────────────────────────────────
# 6) GET /swap
#    Simulate a swap. Accepts either:
#      - inputMint & outputMint
#      - OR input_mint & output_mint
# ────────────────────────────────────────────────────────────────────────────
@app.get("/swap")
def simulate_swap(
    input_mint: str = Query(..., alias="inputMint"),    # alias covers both styles
    output_mint: str = Query(..., alias="outputMint"),
    amount: float = Query(...),
):
    """
    Simulate a swap route (mock). In production, replace with Jupiter route logic.
    Both query styles work:
      /swap?inputMint=<mint>&outputMint=<mint>&amount=2
      /swap?input_mint=<mint>&output_mint=<mint>&amount=2
    """
    # Validate mint lengths:
    if len(input_mint) < 32 or len(input_mint) > 44:
        raise HTTPException(status_code=400, detail="Invalid input_mint format.")
    if len(output_mint) < 32 or len(output_mint) > 44:
        raise HTTPException(status_code=400, detail="Invalid output_mint format.")

    # Mocked swap: pretend 1 unit of input_mint → 1000 units of output_mint
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
