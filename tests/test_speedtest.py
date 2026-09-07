import http.client
import importlib.util
import json
import os
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "speedtest.py"
spec = importlib.util.spec_from_file_location("speedtest", SOURCE)
speedtest = importlib.util.module_from_spec(spec) if SOURCE.exists() else None
if speedtest is not None:
    spec.loader.exec_module(speedtest)


class SpeedtestTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(speedtest, "speedtest.py has not been implemented")

    def stats(self, **updates):
        data = {
            "http_code": 200,
            "content_type": "application/octet-stream",
            "size_download": 1048576,
            "time_total": 2.0,
            "time_starttransfer": 0.5,
        }
        data.update(updates)
        return data

    def test_completed_download_uses_received_bytes_and_total_time(self):
        result = speedtest.classify_download(self.stats(), 0, 1048576)
        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(result["download_mbps"], 4.194, places=3)
        self.assertEqual(result["http_ttfb_ms"], 500)

    def test_timeout_with_payload_is_clearly_a_partial_measurement(self):
        result = speedtest.classify_download(self.stats(), 28, 10485760)
        self.assertEqual(result["status"], "partial")
        self.assertGreater(result["download_mbps"], 0)

    def test_timeout_without_payload_does_not_become_zero_mbps(self):
        result = speedtest.classify_download(self.stats(size_download=0), 28, 1048576)
        self.assertEqual(result["status"], "timeout")
        self.assertIsNone(result["download_mbps"])

    def test_html_block_page_cannot_count_as_a_speed_measurement(self):
        result = speedtest.classify_download(self.stats(content_type="text/html"), 0, 1048576)
        self.assertEqual(result["status"], "invalid_content")
        self.assertIsNone(result["download_mbps"])

    def test_http_403_preserves_the_http_failure(self):
        result = speedtest.classify_download(self.stats(http_code=403), 22, 1048576)
        self.assertEqual(result["status"], "http_403")
        self.assertIsNone(result["download_mbps"])

    def test_short_response_is_not_reported_as_success(self):
        result = speedtest.classify_download(self.stats(size_download=900), 0, 1048576)
        self.assertEqual(result["status"], "short_response")
        self.assertIsNone(result["download_mbps"])

    def test_invalid_time_cannot_produce_an_infinite_rate(self):
        result = speedtest.classify_download(self.stats(time_total=0), 0, 1048576)
        self.assertIsNone(result["download_mbps"])

    def test_failing_proxy_does_not_fall_back_to_a_direct_request(self):
        with patch.object(speedtest.subprocess, "run") as run:
            run.return_value.returncode = 7
            run.return_value.stdout = json.dumps(self.stats(http_code=0, size_download=0))
            speedtest.measure("http://127.0.0.1:19870", 1048576, 2)
        self.assertEqual(run.call_count, 1)
        argv = run.call_args.args[0]
        self.assertEqual(argv[1], "--disable")
        self.assertEqual(argv[argv.index("--proxy") + 1], "http://127.0.0.1:19870")
        self.assertEqual(argv[argv.index("--noproxy") + 1], "")
        self.assertNotIn("--insecure", argv)

    def test_real_curl_preserves_connection_refused_error(self):
        with socket.socket() as unused_port:
            unused_port.bind(("127.0.0.1", 0))
            port = unused_port.getsockname()[1]
            result = speedtest.measure(f"http://127.0.0.1:{port}", 1048576, 2)
        self.assertIn(result["status"], {"curl_7", "timeout"})
        self.assertIsNone(result["download_mbps"])

    def test_invalid_subscription_url_does_not_leak_the_url(self):
        with (
            patch.dict(os.environ, {"SUBSCRIPTION_URL": "https://example.com/private?token=secret value"}),
            patch.object(speedtest.urllib.request, "build_opener") as make_opener,
        ):
            make_opener.return_value.open.side_effect = http.client.InvalidURL("token=secret value")
            with self.assertRaises(speedtest.SpeedtestError) as error:
                speedtest.read_subscription(None)
        self.assertNotIn("secret value", str(error.exception))

    def test_subscription_redirect_cannot_downgrade_https(self):
        handler = speedtest.HttpsOnlyRedirect()
        request = urllib.request.Request("https://example.com/subscription")
        with self.assertRaises(speedtest.SpeedtestError):
            handler.redirect_request(request, None, 302, "Found", {}, "http://example.com/private")

    def test_hourly_rotation_covers_the_tail_when_budget_is_short(self):
        nodes = [{"name": f"node-{i:03}"} for i in range(200)]
        self.assertNotEqual(speedtest.rotate_nodes(nodes, hour=200)[0],
                            speedtest.rotate_nodes(nodes, hour=201)[0])
        starts = {speedtest.rotate_nodes(nodes, hour=h)[0]["name"] for h in range(200)}
        self.assertEqual(len(starts), 200)

    def test_local_config_has_only_the_subscription_in_test_group(self):
        config = speedtest.make_config(19870, 19090, "test-token")
        group = config["proxy-groups"][0]
        self.assertEqual(group["use"], ["subscription"])
        self.assertNotIn("proxies", group)
        self.assertEqual(config["rules"], ["MATCH,SPEEDTEST"])
        self.assertFalse(config["allow-lan"])
        self.assertEqual(config["proxy-providers"]["subscription"]["type"], "file")

    def test_direct_and_group_entries_are_excluded_from_nodes(self):
        entries = [{"name": "DIRECT", "type": "Direct"},
                   {"name": "group", "type": "Selector"},
                   {"name": "DE", "type": "Vless"},
                   {"name": "US", "type": "Vmess"}]
        self.assertEqual([p["name"] for p in speedtest.eligible_nodes(entries)], ["DE", "US"])

    def test_report_escapes_names_and_csv_formulas(self):
        row = {"node": "=HYPERLINK(\"bad\")|<script>\nsecond", "type": "Vless",
               **speedtest.classify_download(self.stats(), 0, 1048576)}
        with tempfile.TemporaryDirectory() as directory:
            speedtest.write_reports(Path(directory), {"started_at": "test"}, [row])
            markdown = (Path(directory) / "summary.md").read_text(encoding="utf-8")
            csv_text = (Path(directory) / "results.csv").read_text(encoding="utf-8-sig")
            self.assertNotIn("<script>", markdown)
            self.assertIn("&lt;script&gt;", markdown)
            self.assertIn("'=HYPERLINK", csv_text)
            self.assertEqual(len(json.loads((Path(directory) / "results.json").read_text())["results"]), 1)


if __name__ == "__main__":
    unittest.main()
