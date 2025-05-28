import os
import requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

app = FastAPI(
    title="SolanaGPT Plugin API",
    description="An API for SolanaGPT to fetch wallet balances, token prices, and simulate swaps.",
    version="1.0.0"
)

# CORS: allow ChatGPT, browsers, etc.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Key from Birdeye
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "your-api-key")

@app.get("/")
def root():
    return {"message": "SolanaGPT API is online 🚀"}

@app.get("/wallet/{address}")
def get_wallet_balance(address: str):
    url = f"https://public-api.birdeye.so/public/wallet/token_list?address={address}"
    headers = {"x-api-key": BIRDEYE_API_KEY}
    res = requests.get(url, headers=headers)
    return res.json() if res.status_code == 200 else {"error": res.text}

@app.get("/price/{symbol}")
def get_token_price(symbol: str):
    url = f"https://public-api.birdeye.so/public/price?address={symbol}"
    headers = {"x-api-key": BIRDEYE_API_KEY}
    res = requests.get(url, headers=headers)
    return res.json() if res.status_code == 200 else {"error": res.text}

@app.get("/swap")
def simulate_swap(input_mint: str, output_mint: str, amount: float):
    return {
        "input_mint": input_mint,
        "output_mint": output_mint,
        "amount": amount,
        "estimated_output": round(amount * 1000, 2),  # Fake multiplier
        "slippage": "0.5%",
        "route": f"{input_mint[:4]}... → USDC → {output_mint[:4]}...",
        "platform": "Jupiter Aggregator (simulated)"
    }

# Inject OpenAPI servers info for GPT integration
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema["servers"] = [
        {
            "url": "https://solgpt-production.up.railway.app"
        }
    ]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
