from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os

app = FastAPI(
    title="SolanaGPT Plugin API",
    description="Get Solana wallet info, token prices, and simulate swaps using Birdeye.",
    version="1.0.0"
)

# CORS for GPT/Browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load Birdeye API Key
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "your-api-key")

# Health check
@app.get("/")
def root():
    return {"message": "SolanaGPT API is live"}

# Get wallet balance
@app.get("/wallet/{address}")
def wallet_info(address: str):
    url = f"https://public-api.birdeye.so/public/wallet/token_list?address={address}"
    headers = {"x-api-key": BIRDEYE_API_KEY}
    res = requests.get(url, headers=headers)
    return res.json() if res.status_code == 200 else {"error": res.text}

# Get token price
@app.get("/price/{symbol}")
def get_token_price(symbol: str):
    url = f"https://public-api.birdeye.so/public/price?address={symbol}"
    headers = {"x-api-key": BIRDEYE_API_KEY}
    res = requests.get(url, headers=headers)
    return res.json() if res.status_code == 200 else {"error": res.text}

# Simulate swap
@app.get("/swap")
def simulate_swap(input_mint: str, output_mint: str, amount: float):
    return {
        "input_mint": input_mint,
        "output_mint": output_mint,
        "amount": amount,
        "estimated_output": round(amount * 1000, 2),  # mocked
        "slippage": "0.5%",
        "route": f"{input_mint[:4]}... → USDC → {output_mint[:4]}...",
        "platform": "Jupiter Aggregator"
    }
