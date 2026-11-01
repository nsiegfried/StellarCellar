# Stellar Cellar

Wine cellar tracker for Home Assistant OS: QR labels on bottles and places,
drinking windows, price tiers, and Claude recommendations from the bottles you own.

## Install
1. Settings → Add-ons → Add-on Store → ⋮ → Repositories → add this repo URL.
2. Install **Stellar Cellar**, open Configuration, paste your Anthropic API key, set
   **public_base_url** to your Nabu Casa URL (see "Labels" below), start it.
3. Open **Stellar Cellar** in the sidebar.

## Options
| Option | Meaning |
|---|---|
| anthropic_api_key | Leave blank to run with rule-based picks only |
| claude_model | Default `claude-sonnet-5-5` |
| public_base_url | Base URL encoded in printed QR labels |
| currency | ISO code used for display |
| drink_soon_months | Window closing within this many months = "Drink soon" |
| price_tier_low / high | The two price buckets on the Tonight page (default 50 / 100) |

## Adding bottles by photo
On the Add page (or the Scan page when a bottle has no QR yet), tap **Photo the
label**. The phone downsizes the picture, Claude reads producer, name, vintage,
region and grapes, and estimates a drinking window. Fields are pre-filled for
you to check before saving. Photos are not stored. Requires the API key.

## Labels
Labels encode `<public_base_url>/q/<CODE>`. Scanning inside the app works
today. For the phone's native camera to open a label from outside the house,
Home Assistant needs a route from that URL to the add-on panel; that is the
custom integration's job in the next phase. Until then, scan with the in-app
Scan button or type the code.

## Develop
```
pip install -r requirements-dev.txt
python -m pytest tests
CELLAR_DATA_DIR=./data uvicorn app.main:app --reload --port 8099
```
