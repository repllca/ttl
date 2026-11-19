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
        # Because session started ALREADY_CONNECTED, bastion should still be alive at the end
        self.assertEqual(session.state, "BASTION", "Existing bastion session must not be disconnected at end")

    def test_unconnected_interactive_dialog_mode(self):
        """Verify macro execution when launched before connecting to bastion (prompts for Host/User/Pass)."""
        logs_dir = os.path.join(WORKSPACE_DIR, "logs")
        if os.path.exists(logs_dir):
            import shutil
            shutil.rmtree(logs_dir)

        session = MockSession()
        session.state = "DISCONNECTED"
        engine = TTLEngine(WORKSPACE_DIR, session, params=[])
        engine.load_script(TTL_FILE)
        engine.run()

        summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
        self.assertGreater(len(summary_files), 0)
        with open(summary_files[-1], "r", encoding="utf-8") as f:
            content = f.read()
        lines = [l for l in content.splitlines() if l.strip() and not l.startswith("Timestamp")]
        self.assertGreaterEqual(len(lines), 6)
        for line in lines:
            parts = line.split(",")
            self.assertEqual(parts[5], "OK")

    def test_failure_resilience_and_bastion_survival(self):
        """
        Verify that when devices fail (unreachable IP, wrong password, SSH algorithm mismatch),
        the macro NEVER mistakes the bastion prompt for the device prompt, NEVER disconnects the
        bastion session, and successfully processes subsequent valid devices.
        """
        import tempfile
        logs_dir = os.path.join(WORKSPACE_DIR, "logs")
        if os.path.exists(logs_dir):
            import shutil
            shutil.rmtree(logs_dir)

        with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=WORKSPACE_DIR, delete=False, encoding="utf-8") as tf:
            tf.write(
                "# ホスト名,IPアドレス,ユーザー名,パスワード,管理者パスワード,プロトコル,コマンドファイル\n"
                "UNREACHABLE-01, 192.168.10.254 , admin , Pass1234 , , ssh , commands_cisco.txt\n"
                "WRONG-PASS-01, 192.168.10.1 , admin , WRONG_PASS , , ssh , commands_cisco.txt\n"
                "OLD-SSH-KEX-01, 192.168.10.253 , admin , Pass1234 , , ssh , commands_cisco.txt\n"
                "\"VALID-LINUX-01\", \"192.168.10.3\" , user1 , Pass1234 , , ssh , commands_linux.txt\n"
            )
            temp_csv = tf.name

        try:
            session = MockSession()
            engine = TTLEngine(
                WORKSPACE_DIR,
                session,
                params=["device_runner.ttl", "logs", "mock_pass", temp_csv]
            )
            engine.load_script(TTL_FILE)
            engine.run()

            summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
            self.assertGreater(len(summary_files), 0)
            with open(summary_files[-1], "r", encoding="utf-8") as f:
                content = f.read()

            lines = [l for l in content.splitlines() if l.strip() and not l.startswith("Timestamp")]
            self.assertEqual(len(lines), 4, f"All 4 rows must be processed without bastion drop! Got: {lines}")

            results = {}
            for line in lines:
                parts = line.split(",")
                results[parts[1]] = (parts[5], parts[6])

            self.assertEqual(results["UNREACHABLE-01"][0], "NG")
            self.assertIn("Unreachable", results["UNREACHABLE-01"][1])

            self.assertEqual(results["WRONG-PASS-01"][0], "NG")
            self.assertIn("Auth Failed", results["WRONG-PASS-01"][1])

            self.assertEqual(results["OLD-SSH-KEX-01"][0], "NG")
            self.assertIn("SSH Algorithm Mismatch", results["OLD-SSH-KEX-01"][1])

            self.assertEqual(results["VALID-LINUX-01"], ("OK", "Success"))
        finally:
            if os.path.exists(temp_csv):
                os.remove(temp_csv)

    def test_multiple_credentials_retry_logic(self):
        """
        Verify that multiple credential pairs can be specified in CSV (pipe-separated, colon/slash paired)
        and that the macro tries each pair in order upon authentication failure without dropping the bastion.
        """
        import tempfile
        logs_dir = os.path.join(WORKSPACE_DIR, "logs")
        if os.path.exists(logs_dir):
            import shutil
            shutil.rmtree(logs_dir)

        with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=WORKSPACE_DIR, delete=False, encoding="utf-8") as tf:
            tf.write(
                "# ホスト名,IPアドレス,ユーザー名,パスワード,管理者パスワード,プロトコル,コマンドファイル\n"
                # Case 1: Multiple passwords with single user (retry pass2 succeeds)
                "MULTI-PASS-01, 192.168.10.1 , admin , WRONG_PASS|Pass1234 , , ssh , commands_cisco.txt\n"
                # Case 2: Parallel users & passwords (retry user2 & pass2 succeeds)
                "MULTI-USER-PASS-01, 192.168.10.1 , wrong_user|admin , WRONG_PASS|Pass1234 , , ssh , commands_cisco.txt\n"
                # Case 3: Combined colon pair format in user column (retry pair 2 succeeds)
                "COLON-PAIR-01, 192.168.10.1 , bad_user:bad_pass|admin:Pass1234 , , , ssh , commands_cisco.txt\n"
                # Case 4: Combined slash pair format in user column (retry pair 2 succeeds)
                "SLASH-PAIR-01, 192.168.10.1 , bad_user/bad_pass|admin/Pass1234 , , , ssh , commands_cisco.txt\n"
                # Case 5: All candidates fail (3 wrong passwords)
                "ALL-FAIL-01, 192.168.10.1 , admin , WRONG_1|WRONG_2|WRONG_3 , , ssh , commands_cisco.txt\n"
                # Case 6: Subsequent normal device succeeds, proving bastion session remained healthy
                "SUBSEQUENT-OK-01, 192.168.10.3 , user1 , Pass1234 , , ssh , commands_linux.txt\n"
            )
            temp_csv = tf.name

        try:
            session = MockSession()
            engine = TTLEngine(
                WORKSPACE_DIR,
                session,
                params=["device_runner.ttl", "logs", "mock_pass", temp_csv]
            )
            engine.load_script(TTL_FILE)
            engine.run()

            summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
            self.assertGreater(len(summary_files), 0)
            with open(summary_files[-1], "r", encoding="utf-8") as f:
                content = f.read()

            lines = [l for l in content.splitlines() if l.strip() and not l.startswith("Timestamp")]
            self.assertEqual(len(lines), 6, f"All 6 rows must be processed! Got: {lines}")

            results = {}
            for line in lines:
                parts = line.split(",")
                results[parts[1]] = (parts[5], parts[6])

            # Case 1: Succeeded on candidate #2
            self.assertEqual(results["MULTI-PASS-01"][0], "OK")
            self.assertIn("Candidate #2", results["MULTI-PASS-01"][1])

            # Case 2: Succeeded on candidate #2
            self.assertEqual(results["MULTI-USER-PASS-01"][0], "OK")
            self.assertIn("Candidate #2", results["MULTI-USER-PASS-01"][1])

            # Case 3: Succeeded on candidate #2
            self.assertEqual(results["COLON-PAIR-01"][0], "OK")
            self.assertIn("Candidate #2", results["COLON-PAIR-01"][1])

            # Case 4: Succeeded on candidate #2
            self.assertEqual(results["SLASH-PAIR-01"][0], "OK")
            self.assertIn("Candidate #2", results["SLASH-PAIR-01"][1])

            # Case 5: All 3 attempts failed
            self.assertEqual(results["ALL-FAIL-01"][0], "NG")
            self.assertIn("All 3 Auth Attempts Failed", results["ALL-FAIL-01"][1])

            # Case 6: Bastion session remained healthy and subsequent node succeeded
            self.assertEqual(results["SUBSEQUENT-OK-01"], ("OK", "Success"))

        finally:
            if os.path.exists(temp_csv):
                os.remove(temp_csv)


if __name__ == "__main__":
    unittest.main()
