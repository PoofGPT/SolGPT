from utils.prices import get_token_price

def simulate_swap(input_mint: str, output_mint: str, amount: float):
    """
    Simulate a swap from input_mint to output_mint using Birdeye prices.
    Returns estimated output, slippage, and route.
    """
    in_data = get_token_price(input_mint)
    out_data = get_token_price(output_mint)
    try:
        in_price = float(in_data.get("price"))
        out_price = float(out_data.get("price"))
    except Exception as e:
        return {"error": "Price parse error", "details": str(e), "input_data": in_data, "output_data": out_data}

    # total value in USD-equivalent
    total_value = in_price * amount
    estimated_amount = total_value / out_price
    slippage = 0.005  # assume 0.5%
    final_amount = estimated_amount * (1 - slippage)

    return {
        "input": f"{amount} of {input_mint}",
        "estimated_output": f"{final_amount:.6f} of {output_mint}",
        "slippage": f"{slippage*100:.2f}%",
        "route": [input_mint, "USDC", output_mint]
    }
