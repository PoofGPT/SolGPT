import os
import requests
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict
from functools import lru_cache

app = FastAPI()

# ────────────────────────────────────────────────────────────────────────────
# 1) Helius API key must be set in environment:
#    HELIUS_API_KEY = "your‐real‐helius‐key"
# ────────────────────────────────────────────────────────────────────────────
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
if not HELIUS_API_KEY:
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
# 3) Helper: Fetch & cache the on-chain SPL Token List (so we can resolve symbols→mint)
# ────────────────────────────────────────────────────────────────────────────
TOKEN_LIST_URL = (
    "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
)

@lru_cache(maxsize=1)
def get_token_list() -> Dict[str, str]:
    """
    Download the SPL Token List JSON and build a dict mapping:
       symbol_uppercase -> mint_address

    This caches the result (LRU cache of size 1).  
    """
    try:
        resp = requests.get(TOKEN_LIST_URL, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        # If token list fetch fails, we can still proceed but symbol resolution won't work.
        raise RuntimeException(f"Unable to fetch SPL Token List: {e}")

    data = resp.json()
    tokens = data.get("tokens", [])
    symbol_to_mint: Dict[str, str] = {}
    for entry in tokens:
        sym = entry.get("symbol", "").upper()
        mint_addr = entry.get("address", "")
        if sym and mint_addr:
            symbol_to_mint[sym] = mint_addr
    return symbol_to_mint


def resolve_symbol_to_mint(identifier: str) -> Optional[str]:
    """
    If identifier is a known symbol (e.g. "BONK"), return its mint address.
    Returns None if not found.
    """
    symbol = identifier.upper()
    token_map = get_token_list()
    return token_map.get(symbol)


# ────────────────────────────────────────────────────────────────────────────
# 4) GET /wallet/{address}
#    Fetch SOL & SPL balances for a wallet via Helius
# ────────────────────────────────────────────────────────────────────────────
@app.get("/wallet/{address}", response_model=WalletResponse)
def get_wallet_balance(address: str):
    """
    Fetch SOL and SPL token balances for a given Solana wallet address via Helius.
    Always returns JSON, even if balances are zero. Raises 400 for invalid address format.
    """
    # Validate - very basic length check (32-44 chars for base58)
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
            status_code=502,
            detail=f"Helius RPC error while fetching wallet balances: {e}",
        )

    data = resp.json()
    # Example response:
    #   { "owner": "ADDRESS", "lamports": 1234567890,
    #     "tokens": [ { "mint": "MINT", "amount": "1000000", "decimals": 6, ... }, … ] }

    sol_lamports = data.get("lamports", 0)
    sol_balance = sol_lamports / 1e9

    token_list = []
    for t in data.get("tokens", []):
        amt_str = t.get("amount", "0")
        dec = t.get("decimals", 0)
        try:
            human_amt = int(amt_str) / (10**dec) if dec >= 0 else int(amt_str)
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
#    If {identifier} looks like a 32–44 char base58 string: treat as mint
#    Otherwise treat as symbol → resolve mint via SPL Token List
# ────────────────────────────────────────────────────────────────────────────
@app.get("/price/{identifier}")
def get_token_price(identifier: str):
    """
    Fetch token price given either a mint address (32-44 chars) or a symbol (e.g. "BONK").

    1) If len(identifier) in [32..44], assume it's a mint.  
    2) Else, treat as symbol, look up mint from the SPL Token List.  
    3) If symbol not found ⇒ 400 with helpful message.  
    4) Once we have the mint, call Helius price API:
         https://api.helius.xyz/v0/token/price?addresses[]=<mint>&api-key=<KEY>
    """
    mint: Optional[str] = None

    # Step A) If identifier length matches a typical mint length, accept it as a mint
    if 32 <= len(identifier) <= 44:
        mint = identifier
    else:
        # Step B) Try to resolve identifier as a symbol → mint
        mint = resolve_symbol_to_mint(identifier)
        if not mint:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"‘{identifier}’ is not a valid on-chain mint address (32–44 chars),\n"
                    "and it was not found as a symbol in the SPL Token List.\n"
                    "Make sure you either pass a valid mint address or a known symbol (e.g. BONK → DezXbQ…)."
                )
            )

    # Step C) Call Helius to fetch price
    helius_url = (
        "https://api.helius.xyz/v0/token/price"
        f"?addresses[]={mint}"
        f"&api-key={HELIUS_API_KEY}"
    )
    try:
        resp = requests.get(helius_url, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=502,
            detail=f"Helius price API error: {e}"
        )

    data = resp.json()
    # Expect data = [ { "address": mint, "price": ..., … } ] or []
    if not isinstance(data, list) or len(data) == 0:
        raise HTTPException(status_code=404, detail=f"No price data found for mint {mint}")

    return data[0]


# ────────────────────────────────────────────────────────────────────────────
# 6) GET /swap
#    Simulate a swap (mock), accept both inputMint/input_mint and outputMint/output_mint
# ────────────────────────────────────────────────────────────────────────────
@app.get("/swap")
def simulate_swap(
    input_mint: str = Query(..., alias="inputMint"),  # accepts inputMint or input_mint
    output_mint: str = Query(..., alias="outputMint"),  # accepts outputMint or output_mint
    amount: float = Query(...),
):
    """
    Simulate a swap. In production, replace this with real Jupiter route logic.
    E.g. POST or GET to Jupiter /quote endpoints.
    Here we simply return a mocked route.
    """
    # Basic validation of mint format
    if len(input_mint) < 32 or len(input_mint) > 44:
        raise HTTPException(status_code=400, detail="Invalid inputMint format.")
    if len(output_mint) < 32 or len(output_mint) > 44:
        raise HTTPException(status_code=400, detail="Invalid outputMint format.")

    estimated_output = round(amount * 1000, 6)  # mock: 1 → 1000

    return {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "estimatedOutput": estimated_output,
        "slippageBps": 50,
        "route": ["SOL", "USDC", output_mint],
        "platform": "Jupiter (mocked)",
    }
