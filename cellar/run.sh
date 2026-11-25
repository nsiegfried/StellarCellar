#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Stellar Cellar"

export ANTHROPIC_API_KEY="$(bashio::config 'anthropic_api_key')"
export CELLAR_MODEL="$(bashio::config 'claude_model')"
export CELLAR_PUBLIC_BASE="$(bashio::config 'public_base_url')"
export CELLAR_CURRENCY="$(bashio::config 'currency')"
export CELLAR_DRINK_SOON_MONTHS="$(bashio::config 'drink_soon_months')"
export CELLAR_PRICE_TIER_LOW="$(bashio::config 'price_tier_low')"
export CELLAR_PRICE_TIER_HIGH="$(bashio::config 'price_tier_high')"
export CELLAR_ZEBRA_HOST="$(bashio::config 'zebra_host' '')"
export CELLAR_ZEBRA_PORT="$(bashio::config 'zebra_port' '9100')"
export CELLAR_ZEBRA_DPI="$(bashio::config 'zebra_dpi' '203')"
export CELLAR_ZEBRA_LABEL_W_MM="$(bashio::config 'zebra_label_width_mm' '25.4')"
export CELLAR_ZEBRA_LABEL_H_MM="$(bashio::config 'zebra_label_height_mm' '12.7')"
export CELLAR_ZEBRA_DARKNESS="$(bashio::config 'zebra_darkness' '20')"
export CELLAR_ZEBRA_LAYOUT="$(bashio::config 'zebra_layout' 'compact')"
export CELLAR_ZEBRA_QR_SCALE="$(bashio::config 'zebra_qr_scale' '1.0')"
export CELLAR_FRIDGE_TEMP_ENTITY="$(bashio::config 'fridge_temp_entity' 'sensor.wine_tt_mt_temperature')"
export CELLAR_FRIDGE_HUMIDITY_ENTITY="$(bashio::config 'fridge_humidity_entity' 'sensor.wine_tt_mt_humidity')"
export CELLAR_FRIDGE_TEMP_MIN="$(bashio::config 'fridge_temp_min' '45')"
export CELLAR_FRIDGE_TEMP_MAX="$(bashio::config 'fridge_temp_max' '65')"
export CELLAR_FRIDGE_RH_MIN="$(bashio::config 'fridge_humidity_min' '40')"
export CELLAR_FRIDGE_RH_MAX="$(bashio::config 'fridge_humidity_max' '80')"
export CELLAR_DATA_DIR="/data"

cd /app
exec uvicorn app.main:app --host 0.0.0.0 --port 8099 --proxy-headers
