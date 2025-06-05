# main.py

import os
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional

app = FastAPI()

# Load your Helius API key from environment
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", None)
if not HELIUS_API_KEY:
    # In Railway, make sure you set HELIUS_API_KEY under "Variables"
    raise RuntimeError("HELIUS_API_KEY environment variable is not set")


class TokenBalance(BaseModel):
    mint: str
    amount: str
    decimals: int


class WalletResponse(BaseModel):
    address: str
    sol_balance: float
    tokens: List[TokenBalance]


@app.get("/wallet/{address}", response_model=WalletResponse)
def get_wallet_balance(address: str):
    """
    Fetch SOL & SPL balances for a given wallet address using Helius.
    """
    # Validate basic format (PDA or Base58), you can add more validation if needed
    if len(address) < 32 or len(address) > 44:
        raise HTTPException(status_code=400, detail="Invalid Solana address")

    helius_url = (
        f"https://api.helius.xyz/v0/addresses/{address}/balances"
        f"?api-key={HELIUS_API_KEY}"
    )

    try:
        resp = requests.get(helius_url, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Helius RPC error: {e}")

    data = resp.json()

    # Helius returns a structure like:
    # {
    #   "owner": "<address>",
    #   "lamports": 1234567890,
    #   "tokens": [
    #     {
    #       "mint": "<mint_address>",
    #       "amount": "100000000",
    #       "decimals": 6,
    #       ...
    #     },
    #     ...
    #   ]
    # }
    # If address is valid but empty, Helius still returns a JSON with lamports=0, tokens=[].

    sol_lamports = data.get("lamports", 0)
    sol_balance = sol_lamports / 1e9  # convert lamports to SOL

    token_list = []
    for t in data.get("tokens", []):
        # Only include tokens with a positive balance
        amt = t.get("amount", "0")
        dec = t.get("decimals", 0)
        try:
            # Helius returns amount as a string; convert to float at human scale
            human_amt = int(amt) / (10 ** dec) if dec >= 0 else int(amt)
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


@app.get("/price/{mint}")
def get_token_price(mint: str):
    """
    Fetch token price from Helius Price API:
    https://api.helius.xyz/v0/token/price?addresses[]=<mint>&api-key=<key>
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
        raise HTTPException(status_code=502, detail=f"Helius price API error: {e}")

    data = resp.json()
    # Helius returns a list: [ { "address": "...", "price": 0.123, ... } ]
    if not isinstance(data, list) or len(data) == 0:
        raise HTTPException(status_code=404, detail=f"No price data for mint {mint}")

    return data[0]


@app.get("/swap")
def simulate_swap(inputMint: str, outputMint: str, amount: float):
    """
    Simulate a swap route using Helius’ (or a fallback mock if needed).
    You can integrate Jupiter’s route API or return a simple mock.
    """
    # For demonstration, we’ll return a mocked route.
    # In production, replace this with real Jupiter route calls if you have an API.
    return {
        "inputMint": inputMint,
        "outputMint": outputMint,
        "amount": amount,
        "estimatedOutput": amount * 1000,  # mock: pretend 1 SOL -> 1000 XYZ
        "slippageBps": 50,
        "route": ["SOL", "USDC", outputMint],
        "platform": "Jupiter (mocked)",
    }
