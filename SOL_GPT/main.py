from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import requests

app = FastAPI()

# Allow all origins for commercial/public API usage
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Replace this with your preferred Solana data source
SOLANA_API_BASE = "https://public-api.solscan.io/account"
HEADERS = {"accept": "application/json"}

@app.get("/wallet/{address}")
async def get_wallet_info(address: str):
    clean_address = address.lstrip("/")  # remove accidental leading slashes
    url = f"{SOLANA_API_BASE}/{clean_address}"

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)

        if response.status_code == 200:
            data = response.json()
            # Optional: Format it the way your frontend expects
            return {
                "address": clean_address,
                "sol_balance": data.get("lamports", 0) / 1_000_000_000,  # Convert lamports to SOL
                "tokens": data.get("tokenInfoList", [])
            }

        elif response.status_code == 404:
            return JSONResponse(status_code=200, content={
                "error": "Wallet inactive or empty",
                "detail": f"Address {clean_address} not found or has no recent data."
            })

        else:
            raise HTTPException(status_code=response.status_code, detail="Error fetching wallet info")

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Wallet data fetch failed: {str(e)}")
