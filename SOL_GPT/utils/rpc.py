import requests
from dotenv import load_dotenv
load_dotenv()

LAMPORTS_PER_SOL = 1_000_000_000
RPC_URL = "https://api.mainnet-beta.solana.com"

def get_wallet_info(address: str):
    # 1) Get native SOL balance
    bal_payload = {
        "jsonrpc":"2.0",
        "id":1,
        "method":"getBalance",
        "params":[ address ]
    }
    bal_resp = requests.post(RPC_URL, json=bal_payload).json()
    sol_balance = bal_resp["result"]["value"] / LAMPORTS_PER_SOL

    # 2) Get SPL token accounts
    spl_payload = {
        "jsonrpc":"2.0",
        "id":1,
        "method":"getTokenAccountsByOwner",
        "params":[ address,
                   {"programId":"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                   {"encoding":"jsonParsed"} ]
    }
    spl_resp = requests.post(RPC_URL, json=spl_payload).json()
    tokens = []
    for acct in spl_resp.get("result",{}).get("value",[]):
        info = acct["account"]["data"]["parsed"]["info"]
        amt = info["tokenAmount"]
        tokens.append({
            "mint": info["mint"],
            "amount": amt["uiAmountString"],
            "decimals": amt["decimals"]
        })

    return {
        "address": address,
        "sol_balance": sol_balance,
        "tokens": tokens
    }
