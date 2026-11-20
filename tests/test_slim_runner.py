#!/usr/bin/env python3
"""
Syntax, Tera Term 4.85+ compatibility, and full E2E test suite for device_runner_slim.ttl.
"""

import glob
import os
import re
import shutil
import tempfile
import unittest

from tests.mock_lab import MockSession
from tests.ttl_engine import TTLEngine

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLIM_TTL = os.path.join(WORKSPACE_DIR, "device_runner_slim.ttl")

VALID_TTL_485_COMMANDS = {
    "connect", "disconnect", "passwordbox", "messagebox", "inputbox", "yesnobox",
    "wait", "waitln", "waitrecv", "flushrecv", "sendln", "send",
    "fileopen", "filereadln", "filewriteln", "fileclose",
    "strconcat", "strcopy", "strcompare", "strlen", "strscan", "sprintf", "sprintf2",
    "strsplit", "strtrim", "tolower", "toupper", "getdate",
    "logopen", "logwrite", "logclose", "pause", "mpause", "timeout", "end", "goto",
    "if", "then", "else", "elseif", "endif",
    "closett", "call", "return", "exec", "getdir", "setdir", "testlink",
    "statusbox", "closesbox", "settitle",
}


class TestSlimTTLSyntax(unittest.TestCase):
    def setUp(self):
        self.assertTrue(os.path.exists(SLIM_TTL))
        with open(SLIM_TTL, "r", encoding="utf-8") as f:
            self.lines = [line.strip() for line in f]

    def test_line_count_compact(self):
        """Verify device_runner_slim.ttl is cut to less than half of device_runner.ttl (~2,866 lines)."""
        self.assertLess(len(self.lines), 1350, f"Expected slim TTL < 1350 lines, got {len(self.lines)}")

    def test_labels_blocks_waits_and_commands(self):
        declared = set()
        gotos = []
        if_stack = []

        for line_no, raw in enumerate(self.lines, 1):
            code = raw.split(";", 1)[0].strip()
            if not code:
                continue
            self.assertNotIn(r"\n", code, f"Line {line_no}: raw \\n found; use CRLF")
            if code.startswith(":"):
                lbl = code[1:].strip()
                self.assertNotIn(lbl, declared, f"Duplicate label :{lbl} at line {line_no}")
                declared.add(lbl)
                continue

            m_goto = re.search(r"\bgoto\s+([a-zA-Z0-9_]+)", code)
            if m_goto:
                gotos.append((m_goto.group(1), line_no))

            if re.match(r"^if\b.*?\bthen\b", code, re.IGNORECASE):
                if_stack.append(line_no)
            elif re.match(r"^endif\b", code, re.IGNORECASE):
                self.assertTrue(if_stack, f"Line {line_no}: unmatched endif")
                if_stack.pop()

            if code.lower().startswith("wait "):
                args = re.findall(r"'[^']*'|\"[^\"]*\"|\S+", code[5:].strip())
                self.assertLessEqual(len(args), 10, f"Line {line_no}: wait exceeds 10 args: {args}")

            # Verify valid command or assignment
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*\s*=", code):
                cmd = code.split()[0].lower()
                self.assertIn(cmd, VALID_TTL_485_COMMANDS, f"Line {line_no}: unknown command '{cmd}'")

            # Verify strict if syntax (either 'if ... then', 'if ... goto <lbl>', or 'if ... end')
            if re.match(r"^if\b", code, re.IGNORECASE):
                ok_if = (
                    re.search(r"\bthen\s*$", code, re.IGNORECASE)
                    or re.search(r"\bgoto\s+[a-zA-Z0-9_]+\s*$", code, re.IGNORECASE)
                    or re.search(r"\bend\s*$", code, re.IGNORECASE)
                )
                self.assertTrue(ok_if, f"Line {line_no}: invalid single-line if statement: {code}")

        self.assertEqual(len(if_stack), 0, f"Unclosed if blocks: {if_stack}")
        for target, line_no in gotos:
            self.assertIn(target, declared, f"Line {line_no}: undefined goto target '{target}'")


class TestSlimTTLEndToEnd(unittest.TestCase):
    def _clean_logs(self):
        logs_dir = os.path.join(WORKSPACE_DIR, "logs")
        if os.path.exists(logs_dir):
            shutil.rmtree(logs_dir)
        return logs_dir

    def test_slim_multivendor_legacy_ssh_telnet_eof_and_shorthand(self):
        """
        Comprehensive E2E verification of device_runner_slim.ttl covering:
        - Multi-vendor SSH: Cisco (enable), Yamaha (administrator), Linux ($), Juniper (%)
        - Legacy SSH auto-fallback (192.168.10.249: 'no matching' -> SSH_LEGACY_MODE -> OK)
        - Dual-protocol auto-fallback (192.168.10.252: SSH refused -> Telnet OK)
        - Telnet not accepted 1-attempt immediate abort (192.168.10.251)
        - 4-column shorthand device.csv ('HOST,IP,ssh,commands_cisco.txt')
        - UTF-8 BOM + trailing commas + final line without trailing newline on all 3 files
        - Wrong enable password clean recovery
        """
        logs_dir = self._clean_logs()

        with tempfile.NamedTemporaryFile("wb", suffix=".txt", dir=WORKSPACE_DIR, delete=False) as tf_cmd:
            # Final command with NO trailing newline
            tf_cmd.write(b"\xef\xbb\xbfshow version\nshow ip interface brief")
            temp_cmd = tf_cmd.name

        with tempfile.NamedTemporaryFile("wb", suffix=".csv", dir=WORKSPACE_DIR, delete=False) as tf_cred:
            # BOM + 2 telnet rows + 1 bad ssh + final valid ssh row with NO trailing newline
            tf_cred.write(
                b"\xef\xbb\xbf"
                + (
                    "# Username,Password,EnablePassword,Protocol\n"
                    "t_only1,bad1,,telnet\n"
                    "t_only2,bad2,,telnet\n"
                    "wrong_u,WRONG_PASS,,\n"
                    "admin,Pass1234,EnablePass123,"
                ).encode("utf-8")
            )
            temp_cred = tf_cred.name

        with tempfile.NamedTemporaryFile("wb", suffix=".csv", dir=WORKSPACE_DIR, delete=False) as tf_dev:
            # Final device row with NO trailing newline
            tf_dev.write(
                b"\xef\xbb\xbf"
                + (
                    "# Hostname,IP,Username,Password,EnablePassword,Protocol,CommandFile\n"
                    f"SLIM-CISCO-01,192.168.10.1,,,,ssh,{os.path.basename(temp_cmd)}\n"
                    "SLIM-YAMAHA-01,192.168.10.2,admin,Pass1234,,ssh,commands_cisco.txt\n"
                    "SLIM-LINUX-01,192.168.10.3,user1,Pass1234,,ssh,commands_linux.txt\n"
                    "SLIM-JUNIPER-01,192.168.10.4,,,,ssh,commands_juniper.txt\n"
                    "SLIM-LEGACY-SSH,192.168.10.249,,,,ssh,commands_cisco.txt\n"
                    "SLIM-TELNET-AUTO,192.168.10.252,,,,,commands_cisco.txt\n"
                    "SLIM-NO-TELNET,192.168.10.251,,,,telnet,commands_cisco.txt\n"
                    "SLIM-BAD-ENABLE,192.168.10.1,admin,Pass1234,WRONG_EN,ssh,commands_cisco.txt\n"
                    "SLIM-SHORTHAND,192.168.10.1,ssh,commands_cisco.txt"
                ).encode("utf-8")
            )
            temp_dev = tf_dev.name

        try:
            session = MockSession()
            engine = TTLEngine(
                WORKSPACE_DIR,
                session,
                params=["device_runner_slim.ttl", "logs", "mock_pass", temp_dev, temp_cred],
            )
            engine.load_script(SLIM_TTL)
            engine.run(strict_402=False)

            summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
            self.assertGreater(len(summary_files), 0)
            with open(summary_files[-1], "r", encoding="utf-8") as f:
                rows = [l.split(",") for l in f.read().splitlines() if l.strip() and not l.startswith("Timestamp")]
            self.assertEqual(len(rows), 9, f"Expected 9 devices, got {len(rows)}: {rows}")
            res = {r[1]: r for r in rows}

            # Multi-vendor SSH
            self.assertEqual(res["SLIM-CISCO-01"][5], "OK")
            self.assertEqual(res["SLIM-CISCO-01"][4], "#")
            self.assertEqual(res["SLIM-YAMAHA-01"][5], "OK")
            self.assertEqual(res["SLIM-YAMAHA-01"][4], "#")
            self.assertEqual(res["SLIM-LINUX-01"][5], "OK")
            self.assertEqual(res["SLIM-LINUX-01"][4], "$")
            self.assertEqual(res["SLIM-JUNIPER-01"][5], "OK")

            # Legacy SSH auto-fallback
            self.assertEqual(res["SLIM-LEGACY-SSH"][5], "OK")
            self.assertEqual(res["SLIM-LEGACY-SSH"][3], "ssh")

            # Dual-protocol auto-fallback (SSH refused -> Telnet OK)
            self.assertEqual(res["SLIM-TELNET-AUTO"][5], "OK")
            self.assertEqual(res["SLIM-TELNET-AUTO"][3], "telnet")

            # Telnet not accepted -> 1 attempt immediate abort
            self.assertEqual(res["SLIM-NO-TELNET"][5], "NG")
            self.assertIn("Telnet Not Accepted", res["SLIM-NO-TELNET"][6])

            # Wrong enable password -> still OK in user mode (>)
            self.assertEqual(res["SLIM-BAD-ENABLE"][5], "OK")
            self.assertEqual(res["SLIM-BAD-ENABLE"][4], ">")
            self.assertIn("Enable Failed", res["SLIM-BAD-ENABLE"][6])

            # 4-col shorthand + EOF without trailing newline -> OK via SSH
            self.assertEqual(res["SLIM-SHORTHAND"][5], "OK")
            self.assertEqual(res["SLIM-SHORTHAND"][3], "ssh")
            self.assertEqual(res["SLIM-SHORTHAND"][7], "admin")

            # Verify final no-newline command ran on SLIM-CISCO-01
            cisco_logs = glob.glob(os.path.join(logs_dir, "SLIM-CISCO-01_*.log"))
            self.assertEqual(len(cisco_logs), 1)
            with open(cisco_logs[0], "r", encoding="utf-8") as f:
                clog = f.read()
            self.assertIn("==================== [ show version ] ====================", clog)
            self.assertIn("==================== [ show ip interface brief ] ====================", clog)

            # Verify attempts_*.csv recorded SKIP_PROTO, RETRY_LEGACY_SSH, FAIL, and only 1 Telnet attempt for SLIM-NO-TELNET
            att_files = glob.glob(os.path.join(logs_dir, "attempts_*.csv"))
            self.assertGreater(len(att_files), 0)
            with open(att_files[-1], "r", encoding="utf-8") as f:
                att_lines = f.read().splitlines()
            att_text = "\n".join(att_lines)
            self.assertIn("SKIP_PROTO (CSV=telnet Need=ssh)", att_text)
            self.assertIn("RETRY_LEGACY_SSH", att_text)
            self.assertIn("FAIL (Auth", att_text)
            no_telnet_tries = [l for l in att_lines if ",SLIM-NO-TELNET," in l and ",TRYING" in l]
            self.assertEqual(len(no_telnet_tries), 1)
        finally:
            for p in (temp_cmd, temp_cred, temp_dev):
                if os.path.exists(p):
                    os.remove(p)

    def test_slim_unconnected_interactive_mode(self):
        """Verify unconnected interactive startup connects to bastion and completes cleanly."""
        logs_dir = self._clean_logs()
        with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=WORKSPACE_DIR, delete=False, encoding="utf-8") as tf_dev:
            tf_dev.write("INT-SSH-01,192.168.10.1,admin,Pass1234,EnablePass123,ssh,commands_cisco.txt\n")
            temp_dev = tf_dev.name
        try:
            session = MockSession()
            session.state = "DISCONNECTED"
            engine = TTLEngine(
                WORKSPACE_DIR,
                session,
                params=["device_runner_slim.ttl", "logs", "mock_pass", temp_dev, ""],
            )
            engine.load_script(SLIM_TTL)
            engine.run(strict_402=False)
            summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
            self.assertGreater(len(summary_files), 0)
            with open(summary_files[-1], "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("INT-SSH-01,192.168.10.1,ssh,#,OK", content)
        finally:
            if os.path.exists(temp_dev):
                os.remove(temp_dev)


if __name__ == "__main__":
    unittest.main()
