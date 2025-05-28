# SolanaGPT Plugin (Full Version)

This plugin provides endpoints for:
- Checking Solana wallet SPL token balances
- Fetching token prices via Birdeye
- Simulating DEX swaps between tokens

## Setup

1. Clone or unzip:
   ```
   cd solana_gpt_plugin_full
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Create a `.env` file and add:
   ```
   BIRDEYE_API_KEY=YOUR_API_KEY_HERE
   ```

4. Run the server:
   ```
   uvicorn main:app --reload --port 8000
   ```

## Endpoints

- `GET /wallet/{address}`  
- `GET /price/{symbol}`  
- `GET /swap?input_mint=<mint>&output_mint=<mint>&amount=<float>`

## Plugin Registration

1. Expose via ngrok or host on HTTPS.
2. Add manifest URL to ChatGPT Plugins:
   ```
   https://<your-domain>/.well-known/ai-plugin.json
   ```
3. Use in GPT-4+Plugins mode.

Enjoy your advanced SolanaGPT Plugin!
