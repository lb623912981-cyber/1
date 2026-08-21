# Clash Speedtest Cloud

GitHub Actions runs a full Clash speed test every day at 04:00 GMT+8, keeps the ten fastest nodes, and publishes a directly importable Clash configuration at a stable URL. If no usable proxy is found, the previous subscription remains unchanged.

The automatic selector checks `https://telegram.org` so its latency choice is optimized for Telegram access rather than Google connectivity.

Subscription URL:

https://raw.githubusercontent.com/lb623912981-cyber/1/main/single_node_test.yaml
