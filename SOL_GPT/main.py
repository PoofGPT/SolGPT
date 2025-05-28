import os
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from dotenv import load_dotenv

from utils.rpc import get_wallet_info
from utils.prices import get_token_price
from utils.trade import simulate_swap

# Load environment variables (including PUBLIC_URL)
load_dotenv()
PUBLIC_URL = os.getenv(
    "PUBLIC_URL",
    "https://solgpt-production.up.railway.app"
)

app = FastAPI(
    title="SolanaGPT Plugin API",
    version="1.1",
    docs_url="/docs",
    redoc_url=None,
)

# Allow ChatGPT (and any origin) to call your API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/wallet/{address}")
def wallet_info(address: str):
    """
    Get SPL token balances for a wallet address (and native SOL).
    """
    return get_wallet_info(address)

@app.get("/price/{symbol}")
def price(symbol: str):
    """
    Get token price for a given token mint address or symbol.
    """
    return get_token_price(symbol)

@app.get("/swap")
def swap_simulation(
    input_mint: str = Query(..., description="Mint address of the input token"),
    output_mint: str = Query(..., description="Mint address of the output token"),
    amount: float = Query(..., description="Amount of input token to swap"),
):
    """
    Simulate a token swap from input_mint to output_mint.
    """
    return simulate_swap(input_mint, output_mint, amount)

def custom_openapi():
    """
    Inject your public server URL into the OpenAPI schema so
    the Custom GPT builder knows where to send requests.
    """
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    openapi_schema["servers"] = [{"url": PUBLIC_URL}]
    app.openapi_schema = openapi_schema
    return app.openapi_schema

# Override the default openapi method
app.openapi = custom_openapi

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
