import os
import requests
from fastapi import FastAPI, HTTPException

app = FastAPI()

# Load your Helius API key from environment (or hard-code if you prefer, though env var is safer)
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "YOUR_HELIUS_API_KEY_HERE")

@app.get("/price/{mint}")
def get_token_price(mint: str):
    """
    Fetch token price from Helius Price API:
    Endpoint: https://api.helius.xyz/v0/token/price?addresses[]=<mint>&api-key=<KEY>
    Returns JSON containing price data or error message.
    """
    if not HELIUS_API_KEY or HELIUS_API_KEY == "YOUR_HELIUS_API_KEY_HERE":
        raise HTTPException(
            status_code=500,
            detail="Helius API key not configured. Set HELIUS_API_KEY in environment.",
        )
    
    helius_url = (
        "https://api.helius.xyz/v0/token/price"
        f"?addresses[]={mint}"
        f"&api-key={HELIUS_API_KEY}"
    )
    
    try:
        response = requests.get(helius_url, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        # Handle network errors, timeouts, DNS issues, etc.
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch price from Helius: {str(e)}"
        )
    
    data = response.json()
    # Helius returns a list, e.g. [{ "address": "...", "price": 0.123, … }]
    if not isinstance(data, list) or len(data) == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No price data found for mint {mint}"
        )
    
    # Return exactly what the user expects (you can adjust formatting if desired)
    return data[0]
