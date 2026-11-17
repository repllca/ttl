#!/usr/bin/env python3
"""
End-to-End verification test suite using the Mock Lab and TTL Engine.
Proves that device_runner.ttl runs correctly on Tera Term 4.02 compatibility rules
and successfully connects, automates authentication, collects commands, and produces logs.
"""

import os
import glob
import unittest
from tests.mock_lab import MockSession
from tests.ttl_engine import TTLEngine

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TTL_FILE = os.path.join(WORKSPACE_DIR, "device_runner.ttl")
DEVICE_CSV = os.path.join(WORKSPACE_DIR, "device.csv")

class TestEndToEndSimulation(unittest.TestCase):
    def setUp(self):
        self.mock_session = MockSession()
        self.engine = TTLEngine(WORKSPACE_DIR, self.mock_session)
        self.engine.load_script(TTL_FILE)

    def test_strict_402_compatibility_verification(self):
        """Verify that device_runner.ttl has NO forbidden post-4.02 commands."""
        # This will raise SyntaxError if commands like strsplit, break, continue, sprintf2 are used
        self.engine.verify_strict_402_compatibility()

    def test_full_run_with_mock_lab(self):
        """Execute the entire macro against mock multi-vendor devices."""
        # Clean logs directory before run
        logs_dir = os.path.join(WORKSPACE_DIR, "logs")
        if os.path.exists(logs_dir):
            import shutil
            shutil.rmtree(logs_dir)

        # Run execution
        self.engine.run()

        # Check logs directory
        logs_dir = os.path.join(WORKSPACE_DIR, "logs")
        self.assertTrue(os.path.exists(logs_dir), "logs/ directory should be created")

        # Verify summary CSV was created
        summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
        self.assertGreater(len(summary_files), 0, "summary_YYYYMMDD_HHMMSS.csv should be created in logs/")

        # Read summary CSV
        with open(summary_files[-1], "r", encoding="utf-8") as f:
            content = f.read()

        # Verify summary header
        self.assertIn("Timestamp,Hostname,IP,Protocol,Prompt,Status,Detail", content)

        # Verify that all 5 device rows from device.csv were processed with Status: OK
        lines = [l for l in content.splitlines() if l.strip() and not l.startswith("Timestamp")]
        self.assertGreaterEqual(len(lines), 5, f"Should process all devices in CSV, got {len(lines)}")

        for line in lines:
            parts = line.split(",")
            hostname = parts[1]
            status = parts[5]
            detail = parts[6]
            self.assertEqual(status, "OK", f"Device {hostname} failed with detail: {detail}")

        # Verify individual log files were created in logs/
        log_files = glob.glob(os.path.join(logs_dir, "*.log"))
        self.assertGreaterEqual(len(log_files), 5, f"Expected at least 5 device log files in logs/, found {len(log_files)}")

        # Check that individual log files contain the expected headers
        for lf in log_files:
            with open(lf, "r", encoding="utf-8") as f:
                log_data = f.read()
            self.assertIn("==================== [", log_data, f"Log file {lf} should contain command headers")

    def test_interactive_dialog_mode(self):
        """Verify macro execution in interactive mode (popups for CSV, logs, password)."""
        logs_dir = os.path.join(WORKSPACE_DIR, "logs")
        if os.path.exists(logs_dir):
            import shutil
            shutil.rmtree(logs_dir)

        session = MockSession()
        # params = [] simulates manual launch without CLI args (triggers inputbox & passwordbox)
        engine = TTLEngine(WORKSPACE_DIR, session, params=[])
        engine.load_script(TTL_FILE)
        engine.run()

        # Check logs directory and summary
        self.assertTrue(os.path.exists(logs_dir), "logs/ should be created in interactive mode")
        summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
        self.assertGreater(len(summary_files), 0, "summary CSV should be created in interactive mode")

        with open(summary_files[-1], "r", encoding="utf-8") as f:
            content = f.read()

        lines = [l for l in content.splitlines() if l.strip() and not l.startswith("Timestamp")]
        self.assertGreaterEqual(len(lines), 5)
        for line in lines:
            parts = line.split(",")
            self.assertEqual(parts[5], "OK", f"Device {parts[1]} failed in interactive mode: {parts[6]}")


if __name__ == "__main__":
    unittest.main()
