import os
import requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

app = FastAPI(
    title="SolanaGPT Plugin API",
    description="Fetch wallet balances, token prices, and simulate swaps on Solana memecoins.",
    version="2.0.0"
)

# Middleware to allow frontend/GPT to call this
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load keys from env
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "c0c91b06-2f29-4038-8b5f-9d3ff220eb1c")

@app.get("/")
def home():
    return {"message": "SolanaGPT API running ✅"}

@app.get("/wallet/{address}")
def get_wallet_balance(address: str):
    url = f"https://api.helius.xyz/v0/addresses/{address}/balances?api-key={HELIUS_API_KEY}"
    res = requests.get(url)
    if res.status_code == 200:
        return res.json()
    else:
        return {"error": res.text, "status_code": res.status_code}

@app.get("/price/{symbol}")
def get_token_price(symbol: str):
    url = f"https://price.jup.ag/v4/price?ids={symbol}"
    res = requests.get(url)
    if res.status_code == 200:
        return res.json()
    else:
        return {"error": res.text, "status_code": res.status_code}

@app.get("/swap")
def simulate_swap(input_mint: str, output_mint: str, amount: float):
    return {
        "input_mint": input_mint,
        "output_mint": output_mint,
        "input_amount": amount,
        "estimated_output": round(amount * 1000, 2),
        "slippage": "0.5%",
        "route": "SOL → USDC → target token",
        "platform": "Jupiter Aggregator (simulated)"
    }

# Patch OpenAPI with server URL
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema["servers"] = [{"url": os.getenv("PUBLIC_URL", "https://solgpt-production.up.railway.app")}]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
