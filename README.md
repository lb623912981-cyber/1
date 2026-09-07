# Clash Speedtest Cloud

## Hourly Download Measurements

The [Hourly Proxy Speed Test](https://github.com/lb623912981-cyber/1/actions/workflows/speedtest.yml) workflow tests subscription nodes through Mihomo against Cloudflare every hour at minute 17. Open a run to see its speed ranking and download the CSV/JSON reports. It reads the existing `CLASH_SOURCE_URL` secret, or `SUBSCRIPTION_URL` when set.

See [the Chinese setup and results guide](SPEEDTEST.md) for configuration, traffic limits, and result interpretation. Measurements reflect the path from the GitHub runner through the selected proxy, rather than home broadband performance.

## Daily Subscription Update

GitHub Actions runs a full Clash speed test every day at 04:00 GMT+8, keeps the ten fastest nodes, and publishes a directly importable Clash configuration at a stable URL. If no usable proxy is found, the previous subscription remains unchanged.

The automatic selector checks `https://telegram.org` so its latency choice is optimized for Telegram access rather than Google connectivity.

Subscription URL:

https://raw.githubusercontent.com/lb623912981-cyber/1/main/single_node_test.yaml
