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

    def test_external_password_csv_feature(self):
        """
        Verify that an external credentials CSV (user, password, enable_password) can be used
        where target devices have blank credentials in device.csv. The macro should try each candidate
        in credentials.csv in order, and cleanly succeed on the matching account.
        """
        import tempfile
        logs_dir = os.path.join(WORKSPACE_DIR, "logs")
        if os.path.exists(logs_dir):
            import shutil
            shutil.rmtree(logs_dir)

        # 1. Create a custom external credentials CSV
        with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=WORKSPACE_DIR, delete=False, encoding="utf-8") as tf_cred:
            tf_cred.write(
                "# ユーザー名,パスワード,管理者パスワード\n"
                "wrong_user, WRONG_PASS ,\n"
                "bad_user, bad_pass ,\n"
                "admin, Pass1234 , EnablePass123\n"
            )
            temp_cred = tf_cred.name

        # 2. Create device.csv with completely blank credentials
        with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=WORKSPACE_DIR, delete=False, encoding="utf-8") as tf_dev:
            tf_dev.write(
                "# ホスト名,IPアドレス,ユーザー名,パスワード,管理者パスワード,プロトコル,コマンドファイル\n"
                "AUTO-NODE-01, 192.168.10.1 , , , , ssh , commands_cisco.txt\n"
            )
            temp_dev = tf_dev.name

        try:
            session = MockSession()
            engine = TTLEngine(
                WORKSPACE_DIR,
                session,
                params=["device_runner.ttl", "logs", "mock_pass", temp_dev, temp_cred]
            )
            engine.load_script(TTL_FILE)
            engine.run()

            summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
            self.assertGreater(len(summary_files), 0)
            with open(summary_files[-1], "r", encoding="utf-8") as f:
                content = f.read()

            lines = [l for l in content.splitlines() if l.strip() and not l.startswith("Timestamp")]
            self.assertEqual(len(lines), 1)
            parts = lines[0].split(",")
            self.assertEqual(parts[1], "AUTO-NODE-01")
            self.assertEqual(parts[5], "OK")
            self.assertIn("Candidate #3", parts[6])
            self.assertEqual(parts[7], "admin", "Auth_User column in summary CSV should record the logged-in user")

            # Verify resolved_accounts_*.csv was created with logged-in user
            resolved_files = glob.glob(os.path.join(logs_dir, "resolved_accounts_*.csv"))
            self.assertGreater(len(resolved_files), 0, "resolved_accounts_*.csv should be created")
            with open(resolved_files[-1], "r", encoding="utf-8") as f:
                res_content = f.read()
            self.assertIn("AUTO-NODE-01,192.168.10.1,admin,ssh,OK", res_content)

            # Verify individual log contains the Logged-in User header
            log_files = glob.glob(os.path.join(logs_dir, "AUTO-NODE-01_*.log"))
            self.assertGreater(len(log_files), 0, "Device log file should be created")
            with open(log_files[0], "r", encoding="utf-8") as f:
                log_content = f.read()
            self.assertIn("Logged-in User: admin", log_content)
        finally:
            if os.path.exists(temp_cred):
                os.remove(temp_cred)
            if os.path.exists(temp_dev):
                os.remove(temp_dev)

    def test_enable_disconnect_resilience(self):
        """Verify that when a target device drops during enable password escalation,
        the macro does not crash on sendln and safely returns to bastion with error recorded."""
        temp_dev = os.path.join(WORKSPACE_DIR, "temp_dev_drop_enable.csv")
        try:
            with open(temp_dev, "w", encoding="utf-8") as f:
                f.write("Hostname,IP,Username,Password,EnablePassword,Protocol,CommandFile\n")
                f.write("DROP-ENABLE-01,192.168.10.250,admin,cisco,cisco,ssh,commands_cisco.txt\n")

            logs_dir = os.path.join(WORKSPACE_DIR, "logs")
            if os.path.exists(logs_dir):
                import shutil
                shutil.rmtree(logs_dir)

            session = MockSession()
            engine = TTLEngine(
                WORKSPACE_DIR,
                session,
                params=["device_runner.ttl", "logs", "mock_pass", temp_dev]
            )
            engine.load_script(TTL_FILE)
            engine.run()

            summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
            self.assertGreater(len(summary_files), 0)
            with open(summary_files[-1], "r", encoding="utf-8") as f:
                content = f.read()

            lines = [l for l in content.splitlines() if l.strip() and not l.startswith("Timestamp")]
            self.assertEqual(len(lines), 1)
            parts = lines[0].split(",")
            self.assertEqual(parts[1], "DROP-ENABLE-01")
            self.assertEqual(parts[5], "NG")
            self.assertIn("Disconnected During Enable", parts[6])
        finally:
            if os.path.exists(temp_dev):
                os.remove(temp_dev)

    def test_protocol_filtering_and_dual_try_feature(self):
        """
        Verify:
        1. When device protocol is specified in device.csv (e.g. ssh),
           telnet candidates in credentials.csv are skipped, and matching ssh candidate is used.
        2. When device protocol is NOT specified in device.csv (empty),
           candidates with specific protocols (ssh / telnet) are tried,
           and candidates with empty protocol try both SSH and Telnet.
        """
        import tempfile
        logs_dir = os.path.join(WORKSPACE_DIR, "logs")
        if os.path.exists(logs_dir):
            import shutil
            shutil.rmtree(logs_dir)

        # 1. Credentials CSV with protocols
        with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=WORKSPACE_DIR, delete=False, encoding="utf-8") as tf_cred:
            tf_cred.write(
                "# ユーザー名,パスワード,管理者パスワード,プロトコル\n"
                "telnet_user, Pass1234 , , telnet\n"
                "ssh_user, Pass1234 , EnablePass123 , ssh\n"
            )
            temp_cred = tf_cred.name

        # 2. Device CSV:
        # - DEV-SSH-01 explicitly specifies 'ssh'
        # - DEV-UNSPEC-01 specifies empty protocol (should match telnet candidate!)
        with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=WORKSPACE_DIR, delete=False, encoding="utf-8") as tf_dev:
            tf_dev.write(
                "# ホスト名,IPアドレス,ユーザー名,パスワード,管理者パスワード,プロトコル,コマンドファイル\n"
                "DEV-SSH-01, 192.168.10.1 , , , , ssh , commands_cisco.txt\n"
                "DEV-TELNET-01, 192.168.10.5 , , , , , commands_cisco.txt\n"
            )
            temp_dev = tf_dev.name

        try:
            session = MockSession()
            engine = TTLEngine(
                WORKSPACE_DIR,
                session,
                params=["device_runner.ttl", "logs", "mock_pass", temp_dev, temp_cred]
            )
            engine.load_script(TTL_FILE)
            engine.run()

            summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
            self.assertGreater(len(summary_files), 0)
            with open(summary_files[-1], "r", encoding="utf-8") as f:
                content = f.read()

            lines = [l for l in content.splitlines() if l.strip() and not l.startswith("Timestamp")]
            self.assertEqual(len(lines), 2)

            res = {l.split(",")[1]: l.split(",") for l in lines}

            # DEV-SSH-01 should skip telnet_user and succeed with ssh_user via ssh
            self.assertEqual(res["DEV-SSH-01"][3], "ssh")
            self.assertEqual(res["DEV-SSH-01"][5], "OK")
            self.assertEqual(res["DEV-SSH-01"][7], "ssh_user")

            # DEV-TELNET-01 had empty protocol in device.csv, so it matches telnet_user and succeeds via telnet
            self.assertEqual(res["DEV-TELNET-01"][3], "telnet")
            self.assertEqual(res["DEV-TELNET-01"][5], "OK")
            self.assertEqual(res["DEV-TELNET-01"][7], "telnet_user")
        finally:
            if os.path.exists(temp_cred):
                os.remove(temp_cred)
            if os.path.exists(temp_dev):
                os.remove(temp_dev)

    def test_telnet_only_cisco_dual_protocol_and_ctrl_bracket_recovery(self):
        """
        Verify realistic Cisco Telnet-only device (192.168.10.252):
        - SSH port 22 returns Connection refused -> sets SSH_PORT_DEAD and skips subsequent SSH rows
        - Telnet prompts 'Username: ' (NOT 'login:'), rejects wrong PW with '% Authentication failed'
          and does NOT exit on Ctrl+C -> macro cleanly exits via Ctrl+] (#29) + quit
        - Succeeds on valid account, enters enable, ignores '% Invalid input' during disable_paging,
          captures exact 'TELNET-CISCO#' prompt, and runs commands cleanly without 30s timeout.
        """
        import tempfile
        logs_dir = os.path.join(WORKSPACE_DIR, "logs")
        if os.path.exists(logs_dir):
            import shutil
            shutil.rmtree(logs_dir)

        with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=WORKSPACE_DIR, delete=False, encoding="utf-8") as tf_cred:
            tf_cred.write(
                "# ユーザー名,パスワード,管理者パスワード,プロトコル\n"
                "ssh_only_1, Pass1234 , EnablePass123 , SSH\n"
                "ssh_only_2, Pass1234 , EnablePass123 , ssh\n"
                "wrong_user, WRONG_PASS , , TELNET\n"
                "admin, WRONG_PASS , , \n"
                "admin, Pass1234 , EnablePass123 , \n"
            )
            temp_cred = tf_cred.name

        with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=WORKSPACE_DIR, delete=False, encoding="utf-8") as tf_dev:
            tf_dev.write(
                "# ホスト名,IPアドレス,ユーザー名,パスワード,管理者パスワード,プロトコル,コマンドファイル\n"
                "CISCO-TELNET-01, 192.168.10.252 , , , , , commands_cisco.txt\n"
            )
            temp_dev = tf_dev.name

        try:
            session = MockSession()
            engine = TTLEngine(
                WORKSPACE_DIR,
                session,
                params=["device_runner.ttl", "logs", "mock_pass", temp_dev, temp_cred]
            )
            engine.load_script(TTL_FILE)
            engine.run()

            summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
            self.assertGreater(len(summary_files), 0)
            with open(summary_files[-1], "r", encoding="utf-8") as f:
                content = f.read()

            lines = [l for l in content.splitlines() if l.strip() and not l.startswith("Timestamp")]
            self.assertEqual(len(lines), 1)
            parts = lines[0].split(",")
            self.assertEqual(parts[1], "CISCO-TELNET-01")
            self.assertEqual(parts[3], "telnet")
            self.assertEqual(parts[4], "#")
            self.assertEqual(parts[5], "OK")
            self.assertEqual(parts[7], "admin")
            # Confirm FINAL_PROMPT was captured accurately (NOT '% Invalid input...')
            self.assertEqual(engine.vars.get("FINAL_PROMPT"), "TELNET-CISCO#")
        finally:
            if os.path.exists(temp_cred):
                os.remove(temp_cred)
            if os.path.exists(temp_dev):
                os.remove(temp_dev)

    def test_dual_protocol_inline_and_wrong_enable_and_bom_trailing_commas(self):
        """
        Verify:
        - UTF-8 BOM at top of device.csv and credentials.csv is cleanly stripped
        - Trailing empty columns (e.g. 'BOM-NODE,192.168.10.1,,,,,') do not corrupt DEV_CMD
        - Inline credentials with empty protocol automatically try SSH -> refused -> Telnet
        - Wrong enable password does NOT leave macro stuck at enable Password prompt
        """
        import tempfile
        logs_dir = os.path.join(WORKSPACE_DIR, "logs")
        if os.path.exists(logs_dir):
            import shutil
            shutil.rmtree(logs_dir)

        with tempfile.NamedTemporaryFile("wb", suffix=".csv", dir=WORKSPACE_DIR, delete=False) as tf_cred:
            # Write with UTF-8 BOM
            tf_cred.write(
                b"\xef\xbb\xbf"
                + (
                    "admin, Pass1234 , EnablePass123 , ssh,\n"
                ).encode("utf-8")
            )
            temp_cred = tf_cred.name

        with tempfile.NamedTemporaryFile("wb", suffix=".csv", dir=WORKSPACE_DIR, delete=False) as tf_dev:
            tf_dev.write(
                b"\xef\xbb\xbf"
                + (
                    "BOM-TRAILING-01,192.168.10.1,,,,,\n"
                    "INLINE-DUAL-01,192.168.10.252,admin,Pass1234,EnablePass123,,commands_cisco.txt\n"
                    "BAD-ENABLE-01,192.168.10.1,admin,Pass1234,WRONG_ENPASS,ssh,commands_cisco.txt\n"
                ).encode("utf-8")
            )
            temp_dev = tf_dev.name

        try:
            session = MockSession()
            engine = TTLEngine(
                WORKSPACE_DIR,
                session,
                params=["device_runner.ttl", "logs", "mock_pass", temp_dev, temp_cred]
            )
            engine.load_script(TTL_FILE)
            engine.run()

            summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
            self.assertGreater(len(summary_files), 0)
            with open(summary_files[-1], "r", encoding="utf-8") as f:
                content = f.read()

            lines = [l for l in content.splitlines() if l.strip() and not l.startswith("Timestamp")]
            self.assertEqual(len(lines), 3, f"Expected 3 rows, got: {lines}")
            res = {l.split(",")[1]: l.split(",") for l in lines}

            # 1. BOM + trailing empty columns -> uses universal commands & credentials.csv cleanly
            self.assertEqual(res["BOM-TRAILING-01"][5], "OK")
            self.assertEqual(res["BOM-TRAILING-01"][3], "ssh")
            self.assertEqual(res["BOM-TRAILING-01"][7], "admin")

            # 2. Inline creds + empty protocol on telnet-only Cisco -> SSH refused -> Telnet OK!
            self.assertEqual(res["INLINE-DUAL-01"][5], "OK")
            self.assertEqual(res["INLINE-DUAL-01"][3], "telnet")

            # 3. Wrong enable password -> cleanly cancels Password prompt, finishes in user mode (>)
            self.assertEqual(res["BAD-ENABLE-01"][5], "OK")
            self.assertEqual(res["BAD-ENABLE-01"][4], ">")
            self.assertIn("Enable Failed", res["BAD-ENABLE-01"][6])
        finally:
            if os.path.exists(temp_cred):
                os.remove(temp_cred)
            if os.path.exists(temp_dev):
                os.remove(temp_dev)

    def test_telnet_not_accepted_stops_after_one_attempt_and_logs_attempts(self):
        """
        Verify:
        - When Telnet is not accepted (192.168.10.251 closes before login prompt),
          it stops after 1 attempt and does NOT retry the remaining 4 Telnet credentials.
        - attempts_*.log records the exact user, password, and protocol tried.
        """
        import tempfile
        logs_dir = os.path.join(WORKSPACE_DIR, "logs")
        if os.path.exists(logs_dir):
            import shutil
            shutil.rmtree(logs_dir)

        with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=WORKSPACE_DIR, delete=False, encoding="utf-8") as tf_cred:
            tf_cred.write(
                "# ユーザー名,パスワード,管理者パスワード,プロトコル\n"
                "u1, p1 , , telnet\n"
                "u2, p2 , , telnet\n"
                "u3, p3 , , telnet\n"
                "u4, p4 , , telnet\n"
                "u5, p5 , , telnet\n"
            )
            temp_cred = tf_cred.name

        with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=WORKSPACE_DIR, delete=False, encoding="utf-8") as tf_dev:
            tf_dev.write(
                "# ホスト名,IPアドレス,ユーザー名,パスワード,管理者パスワード,プロトコル,コマンドファイル\n"
                "NO-TELNET-01, 192.168.10.251 , , , , telnet , commands_cisco.txt\n"
            )
            temp_dev = tf_dev.name

        try:
            session = MockSession()
            engine = TTLEngine(
                WORKSPACE_DIR,
                session,
                params=["device_runner.ttl", "logs", "mock_pass", temp_dev, temp_cred]
            )
            engine.load_script(TTL_FILE)
            engine.run()

            summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
            self.assertGreater(len(summary_files), 0)
            with open(summary_files[-1], "r", encoding="utf-8") as f:
                content = f.read()

            lines = [l for l in content.splitlines() if l.strip() and not l.startswith("Timestamp")]
            self.assertEqual(len(lines), 1)
            parts = lines[0].split(",")
            self.assertEqual(parts[1], "NO-TELNET-01")
            self.assertEqual(parts[5], "NG")
            self.assertIn("Telnet Not Accepted", parts[6])

            # Confirm only 1 attempt was made (not 5!)
            attempt_files = glob.glob(os.path.join(logs_dir, "attempts_*.csv"))
            self.assertGreater(len(attempt_files), 0, "attempts_*.csv should be created")
            with open(attempt_files[-1], "r", encoding="utf-8") as f:
                att_lines = [l for l in f.read().splitlines() if l.strip() and not l.startswith("Timestamp")]
            trying_lines = [l for l in att_lines if ",TRYING" in l]
            self.assertEqual(len(trying_lines), 1, f"Expected exactly 1 Telnet attempt, got: {trying_lines}")
            self.assertIn(",u1,p1,TRYING", trying_lines[0])
        finally:
            if os.path.exists(temp_cred):
                os.remove(temp_cred)
            if os.path.exists(temp_dev):
                os.remove(temp_dev)


if __name__ == "__main__":
    unittest.main()
