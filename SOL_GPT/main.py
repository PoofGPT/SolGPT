from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os

app = FastAPI()

# ✅ CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "your-birdeye-api-key")

# ✅ Models
class SwapRequest(BaseModel):
    input_mint: str
    output_mint: str
    amount: float

# ✅ Endpoints
@app.get("/wallet/{address}")
def wallet_info(address: str):
    url = f"https://public-api.birdeye.so/public/wallet/token_list?address={address}"
    headers = {"x-api-key": BIRDEYE_API_KEY}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    return {"error": "Failed to fetch wallet info", "detail": response.text}

@app.get("/price/{symbol}")
def get_token_price(symbol: str):
    url = f"https://public-api.birdeye.so/public/price?address={symbol}"
    headers = {"x-api-key": BIRDEYE_API_KEY}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    return {"error": "Failed to fetch token price", "detail": response.text}

@app.get("/swap")
def simulate_swap(input_mint: str, output_mint: str, amount: float):
    # This is mocked — replace with real Jupiter Aggregator logic if needed
    return {
        "input_mint": input_mint,
        "output_mint": output_mint,
        "amount": amount,
        "estimated_output": amount * 1000,  # fake multiplier
        "slippage": "0.5%",
        "route": f"{input_mint[:4]}... → USDC → {output_mint[:4]}...",
        "platform": "Jupiter"
    }
