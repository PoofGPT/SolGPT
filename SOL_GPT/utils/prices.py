import os
import requests
from dotenv import load_dotenv

# Load .env for API keys
load_dotenv()
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")

def get_token_price(symbol: str):
    """
    Fetch token price from Birdeye (Solana DEX aggregator).
    Make sure BIRDEYE_API_KEY is set in your .env!
    """
    url = f"https://public-api.birdeye.so/public/price?address={symbol}"
    headers = {"x-api-key": BIRDEYE_API_KEY}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        return {"error": f"Unable to fetch price ({response.status_code}): {response.text}"}
