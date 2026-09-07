#!/usr/bin/env python3
"""Measure subscription nodes through an isolated Mihomo instance. Python 3.11+."""

import argparse
import csv
import hashlib
import html
import http.client
import json
import math
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENDPOINT = "https://speed.cloudflare.com/__down"
MIB = 1024 * 1024
MAX_SUBSCRIPTION_BYTES = 4 * MIB
SUCCESS = {"ok", "partial"}
CSV_FIELDS = ["node", "type", "status", "download_mbps", "download_mib_s",
              "http_ttfb_ms", "bytes_received", "seconds", "http_status", "curl_exit"]


class SpeedtestError(Exception):
    """An error whose message is safe to include in Actions logs."""


class HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise SpeedtestError("Subscription redirected to a non-HTTPS URL.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def classify_download(stats, returncode, expected_bytes):
    received = int(stats.get("size_download", 0) or 0)
    elapsed = float(stats.get("time_total", 0) or 0)
    first_byte = float(stats.get("time_starttransfer", 0) or 0)
    http_status = int(stats.get("http_code", 0) or 0)
    content_type = (stats.get("content_type") or "").split(";", 1)[0].strip().lower()
    result = {
        "status": "invalid_measurement", "download_mbps": None,
        "download_mib_s": None,
        "http_ttfb_ms": round(first_byte * 1000, 1) if first_byte > 0 else None,
        "bytes_received": received, "seconds": round(elapsed, 4),
        "http_status": http_status, "curl_exit": returncode,
    }
    if http_status not in (0, 200):
        result["status"] = f"http_{http_status}"
    elif returncode == 28 and received < min(65536, expected_bytes):
        result["status"] = "timeout"
    elif returncode not in (0, 28):
        result["status"] = f"curl_{returncode}"
    elif http_status != 200:
        result["status"] = "no_response"
    elif content_type != "application/octet-stream":
        result["status"] = "invalid_content"
    elif received > expected_bytes or (returncode == 0 and received != expected_bytes):
        result["status"] = "short_response"
    elif elapsed > 0 and math.isfinite(elapsed) and received > 0:
        result["status"] = "partial" if returncode == 28 else "ok"
        result["download_mbps"] = round(received * 8 / elapsed / 1_000_000, 3)
        result["download_mib_s"] = round(received / elapsed / MIB, 3)
    return result


def measure(proxy, payload_bytes, timeout):
    query = urllib.parse.urlencode({"bytes": payload_bytes, "nonce": secrets.token_hex(8)})
    # Disable curlrc and NO_PROXY so every node request uses the chosen proxy.
    command = ["curl", "--disable", "--silent", "--show-error", "--fail",
               "--proxy", proxy, "--noproxy", "", "--http1.1",
               "--proto", "=https", "--connect-timeout", str(min(6, timeout)),
               "--max-time", str(timeout), "--max-filesize", str(payload_bytes),
               "--header", "Accept-Encoding: identity", "--header", "Cache-Control: no-cache",
               "--output", os.devnull,
               "--write-out", ('{"http_code":"%{http_code}","size_download":%{size_download},'
               '"time_total":%{time_total},"time_starttransfer":%{time_starttransfer},'
               '"content_type":"%{content_type}"}'),
               "--url", f"{ENDPOINT}?{query}"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout + 5, check=False)
        stats = json.loads(completed.stdout)
    except subprocess.TimeoutExpired:
        return classify_download({}, 28, payload_bytes)
    except (json.JSONDecodeError, ValueError):
        return classify_download({}, 99, payload_bytes)
    return classify_download(stats, completed.returncode, payload_bytes)


def make_config(proxy_port, api_port, token):
    return {
        "mixed-port": proxy_port, "bind-address": "127.0.0.1", "allow-lan": False,
        "mode": "rule", "log-level": "silent", "ipv6": False,
        "external-controller": f"127.0.0.1:{api_port}", "secret": token,
        "profile": {"store-selected": False},
        "proxy-providers": {
            "subscription": {"type": "file", "path": "subscription.txt",
                             "health-check": {"enable": False}},
        },
        "proxy-groups": [{"name": "SPEEDTEST", "type": "select", "use": ["subscription"]}],
        "rules": ["MATCH,SPEEDTEST"],
    }


def eligible_nodes(entries):
    excluded = {"direct", "reject", "rejectdrop", "compatible", "pass", "selector",
                "urltest", "fallback", "loadbalance", "relay"}
    return [entry for entry in entries
            if entry.get("name") and entry.get("type", "").lower() not in excluded]


def read_subscription(local_file):
    if local_file:
        try:
            with Path(local_file).open("rb") as handle:
                data = handle.read(MAX_SUBSCRIPTION_BYTES + 1)
        except OSError:
            raise SpeedtestError("Cannot read the subscription file.") from None
    else:
        url = os.environ.get("SUBSCRIPTION_URL") or os.environ.get("CLASH_SOURCE_URL", "")
        try:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError
        except ValueError:
            raise SpeedtestError("Set SUBSCRIPTION_URL to an HTTPS subscription URL in Actions secrets.") from None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), HttpsOnlyRedirect())
        request = urllib.request.Request(url, headers={"User-Agent": "clash.meta",
                                                       "Accept-Encoding": "identity"})
        try:
            with opener.open(request, timeout=30) as response:
                if urllib.parse.urlsplit(response.url).scheme != "https":
                    raise SpeedtestError("Subscription redirected to a non-HTTPS URL.")
                data = response.read(MAX_SUBSCRIPTION_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise SpeedtestError(f"Subscription download failed: HTTP {exc.code}.") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            raise SpeedtestError("Subscription download failed: connection or TLS error.") from None
    if not data.strip() or len(data) > MAX_SUBSCRIPTION_BYTES:
        raise SpeedtestError("Subscription is empty or larger than 4 MiB.")
    return data


class Mihomo:
    def __init__(self, binary, subscription):
        self.binary = binary
        self.subscription = subscription
        self.process = None
        self.directory = None
        self.token = secrets.token_hex(24)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def api(self, method, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self.api_url + path, data=body, method=method,
                                        headers={"Authorization": f"Bearer {self.token}",
                                                 "Content-Type": "application/json"})
        with self.opener.open(request, timeout=3) as response:
            payload = response.read()
            return json.loads(payload) if payload else {}

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="proxy-speedtest-")
        directory = Path(self.directory.name)
        # Reserve two distinct ports until just before starting the child process.
        with socket.socket() as proxy_socket, socket.socket() as api_socket:
            proxy_socket.bind(("127.0.0.1", 0))
            api_socket.bind(("127.0.0.1", 0))
            proxy_port, api_port = proxy_socket.getsockname()[1], api_socket.getsockname()[1]
        self.proxy_url = f"http://127.0.0.1:{proxy_port}"
        self.api_url = f"http://127.0.0.1:{api_port}"
        try:
            (directory / "subscription.txt").write_bytes(self.subscription)
            config = directory / "config.json"
            config.write_text(json.dumps(make_config(proxy_port, api_port, self.token)), encoding="utf-8")
            child_env = dict(os.environ)
            for key in ("SUBSCRIPTION_URL", "CLASH_SOURCE_URL", "GITHUB_TOKEN", "GH_TOKEN"):
                child_env.pop(key, None)
            self.process = subprocess.Popen(
                [self.binary, "-d", str(directory), "-f", str(config)], cwd=directory,
                env=child_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise SpeedtestError("Mihomo exited during startup. Check the subscription format and node parameters.")
                try:
                    version = self.api("GET", "/version")
                    entries = self.api("GET", "/providers/proxies/subscription").get("proxies", [])
                    self.nodes = eligible_nodes(entries)
                    if self.nodes:
                        self.version = version.get("version", "unknown")
                        return self
                except (urllib.error.URLError, OSError, ValueError):
                    pass
                time.sleep(0.25)
            raise SpeedtestError("No usable nodes loaded within 25 seconds. Supply Clash YAML with proxies, or a supported URI/base64 subscription.")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def select(self, name):
        try:
            self.api("PUT", "/proxies/SPEEDTEST", {"name": name})
            self.api("DELETE", "/connections")
            if self.api("GET", "/proxies/SPEEDTEST").get("now") != name:
                raise SpeedtestError("Mihomo did not select the requested node.")
        except (urllib.error.URLError, OSError, ValueError):
            raise SpeedtestError("Cannot control the isolated Mihomo instance.") from None

    def __exit__(self, *unused):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.directory:
            self.directory.cleanup()


def plain_name(value):
    return " ".join(str(value).split())[:200]


def markdown_cell(value):
    return html.escape(plain_name(value), quote=True).replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`")


def csv_cell(value):
    if not isinstance(value, str):
        return value
    value = plain_name(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def write_reports(output, metadata, rows):
    output.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda row: (row.get("download_mbps") is None,
                                           -(row.get("download_mbps") or 0)))
    report = {"metadata": metadata, "results": ordered}
    (output / "results.json").write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    with (output / "results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({key: csv_cell(value) for key, value in row.items()} for row in ordered)
    successful = sum(row["status"] in SUCCESS for row in rows)
    lines = ["# Proxy download speed test", "",
             f"UTC: {metadata.get('started_at', '')}", "",
             f"Measured successfully: {successful} / {len(rows)} listed nodes.", "",
             "Path: runner -> selected proxy -> Cloudflare. This is not home broadband speed.", "",
             ("Mbps is received bytes / total elapsed time, including connection setup. "
             "HTTP TTFB includes DNS, proxy connection, TLS and server response. "
             "Partial samples reached the time limit; they are estimates."), ""]
    if metadata.get("endpoint_check"):
        lines += [f"Direct endpoint check: {metadata['endpoint_check']['status']} (1 KiB probe only).", ""]
    if metadata.get("error"):
        lines += [f"Run error: {markdown_cell(metadata['error'])}", ""]
    lines += ["| Node | Type | Status | Mbps | MiB/s | HTTP TTFB ms | Received MiB |",
              "| --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for row in ordered:
        fields = [markdown_cell(row["node"]), markdown_cell(row["type"]), row["status"],
                  row.get("download_mbps"), row.get("download_mib_s"), row.get("http_ttfb_ms"),
                  round(row.get("bytes_received", 0) / MIB, 3)]
        lines.append("| " + " | ".join("N/A" if field is None else str(field) for field in fields) + " |")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mihomo", default=os.environ.get("MIHOMO_BIN", "mihomo"))
    parser.add_argument("--subscription-file", type=Path)
    parser.add_argument("--output", type=Path, default=Path("results"))
    parser.add_argument("--download-mib", type=int, default=os.environ.get("DOWNLOAD_MIB", "10"))
    parser.add_argument("--timeout", type=int, default=os.environ.get("TEST_TIMEOUT", "12"))
    parser.add_argument("--max-nodes", type=int, default=os.environ.get("MAX_NODES", "200"))
    parser.add_argument("--run-budget", type=int, default=os.environ.get("RUN_BUDGET_SECONDS", "1800"))
    parser.add_argument("--node-filter", default=os.environ.get("NODE_FILTER", ""),
                        help="Case-insensitive substring in the node name.")
    args = parser.parse_args(argv)
    for name, lower, upper in (("download_mib", 1, 100), ("timeout", 2, 60),
                               ("max_nodes", 1, 500), ("run_budget", 30, 1800)):
        if not lower <= getattr(args, name) <= upper:
            parser.error(f"{name} must be between {lower} and {upper}")
    return args


def rotate_nodes(nodes, hour=None):
    if not nodes:
        return []
    ordered = sorted(nodes, key=lambda node: node["name"])
    offset = (int(time.time() // 3600) if hour is None else hour) % len(ordered)
    return ordered[offset:] + ordered[:offset]


def run(args):
    rows = []
    metadata = {"started_at": utc_now(), "endpoint": ENDPOINT,
                "download_mib": args.download_mib, "timeout_seconds": args.timeout,
                "max_nodes": args.max_nodes, "run_budget_seconds": args.run_budget,
                "runner_os": os.environ.get("RUNNER_OS", sys.platform),
                "run_id": os.environ.get("GITHUB_RUN_ID"), "measurement": "end_to_end_download"}
    exit_code = 1
    try:
        binary = shutil.which(args.mihomo)
        if not binary or not shutil.which("curl"):
            raise SpeedtestError("Mihomo and curl must both be installed.")
        binary = str(Path(binary).resolve())
        subscription = read_subscription(args.subscription_file)
        metadata["subscription_sha256"] = hashlib.sha256(subscription).hexdigest()
        metadata["endpoint_check"] = measure("", 1024, min(args.timeout, 6))
        with Mihomo(binary, subscription) as core:
            metadata["mihomo_version"] = core.version
            metadata["loaded_nodes"] = len(core.nodes)
            nodes = [node for node in core.nodes
                     if args.node_filter.casefold() in node["name"].casefold()]
            if not nodes:
                raise SpeedtestError("No nodes match NODE_FILTER.")
            # Rotate the start index hourly when a budget prevents testing all nodes.
            nodes = rotate_nodes(nodes)
            deadline = time.monotonic() + args.run_budget
            for index, node in enumerate(nodes):
                result = {"node": plain_name(node["name"]), "type": node.get("type", "unknown")}
                if index >= args.max_nodes:
                    result["status"] = "skipped_limit"
                elif time.monotonic() + args.timeout + 15 > deadline:
                    result["status"] = "skipped_budget"
                else:
                    core.select(node["name"])
                    result.update(measure(core.proxy_url, args.download_mib * MIB, args.timeout))
                rows.append(result)
                write_reports(args.output, metadata, rows)
                print(f"{index + 1}/{len(nodes)}: {result['status']} "
                      f"{result.get('download_mbps', 'N/A')} Mbps", flush=True)
        successful = sum(row["status"] in SUCCESS for row in rows)
        metadata["successful_nodes"] = successful
        metadata["skipped_nodes"] = sum(row["status"].startswith("skipped_") for row in rows)
        exit_code = 0 if successful else 2
        if not successful:
            metadata["error"] = "No valid download samples. Check endpoint status and per-node errors."
    except SpeedtestError as exc:
        metadata["error"] = str(exc)
        print(str(exc), file=sys.stderr)
    except (OSError, ValueError, KeyError) as exc:
        metadata["error"] = f"Local setup or data error ({type(exc).__name__})."
        print(metadata["error"], file=sys.stderr)
    finally:
        metadata["finished_at"] = utc_now()
        metadata["exit_code"] = exit_code
        write_reports(args.output, metadata, rows)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write((args.output / "summary.md").read_text(encoding="utf-8"))
    return exit_code


if __name__ == "__main__":
    sys.exit(run(parse_args()))
