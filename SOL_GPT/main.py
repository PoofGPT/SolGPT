import os
import requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# FastAPI app initialization
app = FastAPI(
    title="SolanaGPT Plugin API",
    description="Fetch Solana wallet info, token prices, and simulate swaps.",
    version="1.0.0"
)

# Middleware for CORS to support all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Helius API endpoint
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "your-helius-api-key")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "your-birdeye-api-key")

# Health check route
@app.get("/")
def root():
    return {"message": "SolanaGPT API is live"}

# Route to fetch wallet balances using Helius API
@app.get("/wallet/{address}")
def get_wallet_balance(address: str):
    url = f"https://api.helius.xyz/v0/addresses/{address}/balances"
    headers = {"Authorization": f"Bearer {HELIUS_API_KEY}"}
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        wallet_data = response.json()
        sol_balance = next(
            (item["amount"] for item in wallet_data if item["mint"] == "So11111111111111111111111111111111111111112"), 
            0
        )
        spl_tokens = [item for item in wallet_data if item["mint"] != "So11111111111111111111111111111111111111112"]
        return {"SOL": sol_balance, "tokens": spl_tokens}
    return {"error": "Failed to fetch wallet balance", "detail": response.text}

# Route to fetch token price using Birdeye API
@app.get("/price/{symbol}")
def get_token_price(symbol: str):
    url = f"https://public-api.birdeye.so/public/price?address={symbol}"
    headers = {"x-api-key": BIRDEYE_API_KEY}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    return {"error": "Failed to fetch token price", "detail": response.text}

# Route to simulate swap using mocked data (You can later replace this with real logic)
@app.get("/swap")
def simulate_swap(input_mint: str, output_mint: str, amount: float):
    return {
        "input_mint": input_mint,
        "output_mint": output_mint,
        "amount": amount,
        "estimated_output": round(amount * 1000, 2),  # mocked output
        "slippage": "0.5%",
        "route": f"{input_mint[:4]}... → USDC → {output_mint[:4]}...",
        "platform": "Jupiter Aggregator"
    }

# Override OpenAPI to inject server URL for Custom GPT integration
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema["servers"] = [{"url": os.getenv("PUBLIC_URL", "https://solgpt-production.up.railway.app/")}]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
