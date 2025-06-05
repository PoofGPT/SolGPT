import os
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List

app = FastAPI()

# ────────────────────────────────────────────────────────────────────────────
# 1) Load Helius API key (must be set in your environment: HELIUS_API_KEY)
# ────────────────────────────────────────────────────────────────────────────
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
if not HELIUS_API_KEY:
    raise RuntimeError("HELIUS_API_KEY environment variable is not set")


# ────────────────────────────────────────────────────────────────────────────
# 2) Pydantic models for response schemas
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
# 3) GET /wallet/{address} → returns SOL + SPL balances via Helius
# ────────────────────────────────────────────────────────────────────────────
@app.get("/wallet/{address}", response_model=WalletResponse)
def get_wallet_balance(address: str):
    """
    Fetch SOL & SPL balances for a given wallet address from Helius.
    Always returns a JSON object (even if balance is 0), never a 404 for a valid address.
    """
    # Basic length check (Solana addresses are 32‐44 base58 chars); adjust if needed.
    if len(address) < 32 or len(address) > 44:
        raise HTTPException(status_code=400, detail="Invalid Solana address format")
    
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
            detail=f"Helius RPC error while fetching wallet balances: {e}"
        )

    data = resp.json()
    # Helius always returns a JSON like:
    # {
    #   "owner": "<address>",
    #   "lamports": <int>,
    #   "tokens": [ { "mint": "...", "amount": "1000000", "decimals": 6, ... }, ... ]
    # }
    sol_lamports = data.get("lamports", 0)
    sol_balance = sol_lamports / 1e9  # convert lamports to SOL

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
                decimals=dec
            )
        )

    return WalletResponse(address=address, sol_balance=sol_balance, tokens=token_list)


# ────────────────────────────────────────────────────────────────────────────
# 4) GET /price/{mint} → returns token price via Helius Price API
# ────────────────────────────────────────────────────────────────────────────
@app.get("/price/{mint}")
def get_token_price(mint: str):
    """
    Fetch token price for a given mint from Helius Price API.
    Returns the first JSON object in the Helius response list.
    """
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
    # Helius returns a list like: [ { "address": "...", "price": 0.00123, ... } ]
    if not isinstance(data, list) or len(data) == 0:
        raise HTTPException(status_code=404, detail=f"No price data for mint {mint}")

    return data[0]


# ────────────────────────────────────────────────────────────────────────────
# 5) GET /swap → simple mock simulation endpoint
# ────────────────────────────────────────────────────────────────────────────
@app.get("/swap")
def simulate_swap(inputMint: str, outputMint: str, amount: float):
    """
    Simulate a swap (mock). In production, replace this with Jupiter route logic.
    """
    return {
        "inputMint": inputMint,
        "outputMint": outputMint,
        "amount": amount,
        "estimatedOutput": round(amount * 1000, 6),  # mock example
        "slippageBps": 50,
        "route": ["SOL", "USDC", outputMint],
        "platform": "Jupiter (mocked)",
    }
