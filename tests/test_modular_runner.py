#!/usr/bin/env python3
"""
Syntax, Tera Term 5.4.1 modular include compatibility, and full E2E test suite
for runner.ttl + lib/*.ttl.
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
MAIN_TTL = os.path.join(WORKSPACE_DIR, "runner.ttl")
MODULAR_FILES = [
    MAIN_TTL,
    os.path.join(WORKSPACE_DIR, "lib", "init.ttl"),
    os.path.join(WORKSPACE_DIR, "lib", "auth.ttl"),
    os.path.join(WORKSPACE_DIR, "lib", "exec_device.ttl"),
    os.path.join(WORKSPACE_DIR, "lib", "abort_bastion.ttl"),
]

VALID_TTL_5X_COMMANDS = {
    "connect", "disconnect", "passwordbox", "messagebox", "inputbox", "yesnobox",
    "wait", "waitln", "waitrecv", "waitregex", "flushrecv", "sendln", "send",
    "fileopen", "filereadln", "filewriteln", "fileclose",
    "strconcat", "strcopy", "strcompare", "strlen", "strscan", "sprintf", "sprintf2",
    "strsplit", "strtrim", "strdim", "tolower", "toupper", "strmatch", "getdate",
    "logopen", "logwrite", "logclose", "pause", "mpause", "timeout", "end", "exit", "goto",
    "include", "if", "then", "else", "elseif", "endif",
    "closett", "call", "return", "exec", "getdir", "setdir", "testlink",
    "statusbox", "closesbox", "settitle",
}


class TestModularTTLSyntax(unittest.TestCase):
    def test_main_and_modules_are_compact(self):
        """Verify main runner.ttl is under 450 lines and every module exists and is compact."""
        for path in MODULAR_FILES:
            self.assertTrue(os.path.exists(path), f"Missing module: {path}")
        with open(MAIN_TTL, "r", encoding="utf-8") as f:
            main_lines = f.readlines()
        self.assertLess(len(main_lines), 450, f"runner.ttl should stay < 450 lines, got {len(main_lines)}")

    def test_all_modules_syntax_local_labels_and_waits(self):
        """Verify every module has file-local labels/gotos/calls, balanced if/endif, <=10 wait args, valid 5.x cmds."""
        for path in MODULAR_FILES:
            with open(path, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f]

            declared = set()
            targets = []
            if_stack = []

            for line_no, raw in enumerate(lines, 1):
                code = raw.split(";", 1)[0].strip()
                if not code:
                    continue
                self.assertNotIn(r"\n", code, f"{path}:{line_no}: raw \\n found; use CRLF")
                if code.startswith(":"):
                    lbl = code[1:].strip()
                    self.assertNotIn(lbl, declared, f"{path}:{line_no}: duplicate label :{lbl}")
                    declared.add(lbl)
                    continue

                m_goto = re.search(r"\b(?:goto|call)\s+([a-zA-Z0-9_]+)", code)
                if m_goto:
                    targets.append((m_goto.group(1), line_no))

                if re.match(r"^if\b.*?\bthen\b", code, re.IGNORECASE):
                    if_stack.append(line_no)
                elif re.match(r"^endif\b", code, re.IGNORECASE):
                    self.assertTrue(if_stack, f"{path}:{line_no}: unmatched endif")
                    if_stack.pop()

                if code.lower().startswith(("wait ", "waitln ")):
                    arg_part = code.split(None, 1)[1]
                    args = re.findall(r"'[^']*'|\"[^\"]*\"|\S+", arg_part)
                    self.assertLessEqual(len(args), 10, f"{path}:{line_no}: wait exceeds 10 args: {args}")

                if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*(?:\[[^\]]+\])?\s*=", code):
                    cmd = code.split()[0].lower()
                    self.assertIn(cmd, VALID_TTL_5X_COMMANDS, f"{path}:{line_no}: unknown command '{cmd}'")

                if re.match(r"^if\b", code, re.IGNORECASE):
                    ok_if = (
                        re.search(r"\bthen\s*$", code, re.IGNORECASE)
                        or re.search(r"\bgoto\s+[a-zA-Z0-9_]+\s*$", code, re.IGNORECASE)
                        or re.search(r"\b(?:exit|end)\s*$", code, re.IGNORECASE)
                    )
                    self.assertTrue(ok_if, f"{path}:{line_no}: invalid if syntax: {code}")

            self.assertEqual(len(if_stack), 0, f"{path}: unclosed if blocks: {if_stack}")
            for target, line_no in targets:
                self.assertIn(
                    target,
                    declared,
                    f"{path}:{line_no}: label '{target}' must be declared in the SAME file (Tera Term include rule)",
                )


class TestModularTTLEndToEnd(unittest.TestCase):
    def _clean_logs(self):
        logs_dir = os.path.join(WORKSPACE_DIR, "logs")
        if os.path.exists(logs_dir):
            shutil.rmtree(logs_dir)
        return logs_dir

    def test_modular_multivendor_legacy_ssh_telnet_eof_and_shorthand(self):
        """
        Full E2E test of modular runner.ttl + lib/*.ttl covering:
        - Multi-vendor SSH: Cisco (enable), Yamaha (administrator), Linux ($), Juniper (%)
        - Legacy SSH auto-fallback (192.168.10.249)
        - Dual-protocol auto-fallback (192.168.10.252: SSH refused -> Telnet OK)
        - Telnet not accepted 1-attempt immediate abort (192.168.10.251)
        - Wrong enable password clean recovery
        - 4-column shorthand device.csv ('HOST,IP,ssh,commands_cisco.txt')
        - UTF-8 BOM + final line without trailing newline on all 3 files
        """
        logs_dir = self._clean_logs()

        with tempfile.NamedTemporaryFile("wb", suffix=".txt", dir=WORKSPACE_DIR, delete=False) as tf_cmd:
            tf_cmd.write(b"\xef\xbb\xbfshow version\nshow ip interface brief")
            temp_cmd = tf_cmd.name

        with tempfile.NamedTemporaryFile("wb", suffix=".csv", dir=WORKSPACE_DIR, delete=False) as tf_cred:
            tf_cred.write(
                b"\xef\xbb\xbf"
                + (
                    "# Pattern,Username,Password,EnablePassword,Protocol\n"
                    "Telnet1,t_only1,bad1,,telnet\n"
                    "Telnet2,t_only2,bad2,,telnet\n"
                    "WrongPat,wrong_u,WRONG_PASS,,\n"
                    "CiscoStd,admin,Pass1234,EnablePass123,"
                ).encode("utf-8")
            )
            temp_cred = tf_cred.name

        with tempfile.NamedTemporaryFile("wb", suffix=".csv", dir=WORKSPACE_DIR, delete=False) as tf_dev:
            tf_dev.write(
                b"\xef\xbb\xbf"
                + (
                    "# Hostname,IP,Username,Password,EnablePassword,Protocol,CommandFile\n"
                    f"MOD-CISCO-01,192.168.10.1,,,,ssh,{os.path.basename(temp_cmd)}\n"
                    "MOD-YAMAHA-01,192.168.10.2,admin,Pass1234,,ssh,commands_cisco.txt\n"
                    "MOD-LINUX-01,192.168.10.3,user1,Pass1234,,ssh,commands_linux.txt\n"
                    "MOD-JUNIPER-01,192.168.10.4,,,,ssh,commands_juniper.txt\n"
                    "MOD-LEGACY-SSH,192.168.10.249,,,,ssh,commands_cisco.txt\n"
                    "MOD-TELNET-AUTO,192.168.10.252,,,,,commands_cisco.txt\n"
                    "MOD-NO-TELNET,192.168.10.251,,,,telnet,commands_cisco.txt\n"
                    "MOD-BAD-ENABLE,192.168.10.1,admin,Pass1234,WRONG_EN,ssh,commands_cisco.txt\n"
                    "MOD-SHORTHAND,192.168.10.1,ssh,commands_cisco.txt"
                ).encode("utf-8")
            )
            temp_dev = tf_dev.name

        try:
            session = MockSession()
            engine = TTLEngine(
                WORKSPACE_DIR,
                session,
                params=["runner.ttl", "logs", "mock_pass", temp_dev, temp_cred],
            )
            engine.load_script(MAIN_TTL)
            engine.run(strict_402=False)

            summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
            self.assertGreater(len(summary_files), 0)
            with open(summary_files[-1], "r", encoding="utf-8") as f:
                rows = [l.split(",") for l in f.read().splitlines() if l.strip() and not l.startswith("Timestamp")]
            self.assertEqual(len(rows), 9, f"Expected 9 devices, got {len(rows)}: {rows}")
            res = {r[1]: r for r in rows}

            self.assertEqual(res["MOD-CISCO-01"][5], "OK")
            self.assertEqual(res["MOD-CISCO-01"][4], "#")
            self.assertEqual(res["MOD-YAMAHA-01"][5], "OK")
            self.assertEqual(res["MOD-YAMAHA-01"][4], "#")
            self.assertEqual(res["MOD-LINUX-01"][5], "OK")
            self.assertEqual(res["MOD-LINUX-01"][4], "$")
            self.assertEqual(res["MOD-JUNIPER-01"][5], "OK")
            self.assertEqual(res["MOD-LEGACY-SSH"][5], "OK")
            self.assertEqual(res["MOD-LEGACY-SSH"][3], "ssh")
            self.assertEqual(res["MOD-TELNET-AUTO"][5], "OK")
            self.assertEqual(res["MOD-TELNET-AUTO"][3], "telnet")
            self.assertEqual(res["MOD-NO-TELNET"][5], "NG")
            self.assertIn("Telnet Not Accepted", res["MOD-NO-TELNET"][6])
            self.assertEqual(res["MOD-BAD-ENABLE"][5], "OK")
            self.assertEqual(res["MOD-BAD-ENABLE"][4], ">")
            self.assertIn("Enable Failed", res["MOD-BAD-ENABLE"][6])
            self.assertEqual(res["MOD-SHORTHAND"][5], "OK")
            self.assertEqual(res["MOD-SHORTHAND"][3], "ssh")
            self.assertEqual(res["MOD-SHORTHAND"][7], "admin")
            self.assertEqual(res["MOD-SHORTHAND"][8], "CiscoStd")
            self.assertEqual(res["MOD-YAMAHA-01"][8], "Direct(device.csv)")
            self.assertEqual(res["MOD-NO-TELNET"][7], "-")
            self.assertEqual(res["MOD-NO-TELNET"][8], "-")

            # Verify resolved_accounts_*.csv contains Pattern & LoggedInUser
            resolved_files = glob.glob(os.path.join(logs_dir, "resolved_accounts_*.csv"))
            self.assertGreater(len(resolved_files), 0)
            with open(resolved_files[-1], "r", encoding="utf-8") as f:
                res_text = f.read()
            self.assertIn("MOD-CISCO-01,192.168.10.1,CiscoStd,admin,ssh,OK", res_text)

            # Verify device logs are separated into logs/devices/, NOT directly under logs/
            self.assertEqual(len(glob.glob(os.path.join(logs_dir, "*.log"))), 0)
            cisco_logs = glob.glob(os.path.join(logs_dir, "devices", "MOD-CISCO-01_*.log"))
            self.assertEqual(len(cisco_logs), 1)
            with open(cisco_logs[0], "r", encoding="utf-8") as f:
                clog = f.read()
            self.assertIn("==================== [ show version ] ====================", clog)
            self.assertIn("==================== [ show ip interface brief ] ====================", clog)

            # Verify real-time credentials.csv reordering:
            # 'CiscoStd,admin,Pass1234,EnablePass123,' was row 4 initially, and must now be the FIRST data row!
            with open(temp_cred, "r", encoding="utf-8") as f:
                cred_lines = [l for l in f.read().splitlines() if l.strip()]
            self.assertEqual(cred_lines[0], "# Pattern,Username,Password,EnablePassword,Protocol")
            self.assertEqual(cred_lines[1], "CiscoStd,admin,Pass1234,EnablePass123,")
            self.assertEqual(len(cred_lines), 5)

            att_files = glob.glob(os.path.join(logs_dir, "attempts_*.csv"))
            self.assertGreater(len(att_files), 0)
            with open(att_files[-1], "r", encoding="utf-8") as f:
                att_lines = f.read().splitlines()
            att_text = "\n".join(att_lines)
            self.assertIn("SKIP_PROTO (CSV=telnet Need=ssh)", att_text)
            self.assertIn("RETRY_LEGACY_SSH", att_text)
            self.assertIn("FAIL (Auth", att_text)
            self.assertIn(",WrongPat,wrong_u,TRYING", att_text)
            # Because MOD-CISCO-01 promoted CiscoStd to row 1, MOD-JUNIPER-01 & MOD-SHORTHAND must succeed on #1!
            juniper_tries = [l for l in att_lines if ",MOD-JUNIPER-01," in l and ",TRYING" in l]
            self.assertEqual(len(juniper_tries), 1)
            self.assertIn(",#1,ssh,CiscoStd,admin,TRYING", juniper_tries[0])
            no_telnet_tries = [l for l in att_lines if ",MOD-NO-TELNET," in l and ",TRYING" in l]
            self.assertEqual(len(no_telnet_tries), 1)
        finally:
            for p in (temp_cmd, temp_cred, temp_dev):
                if os.path.exists(p):
                    os.remove(p)

    def test_modular_unconnected_bastion_startup(self):
        """Verify lib/init.ttl connects to bastion when launched while disconnected."""
        logs_dir = self._clean_logs()
        with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=WORKSPACE_DIR, delete=False, encoding="utf-8") as tf_dev:
            tf_dev.write("INT-MOD-01,192.168.10.1,admin,Pass1234,EnablePass123,ssh,commands_cisco.txt\n")
            temp_dev = tf_dev.name
        try:
            session = MockSession()
            session.state = "DISCONNECTED"
            engine = TTLEngine(
                WORKSPACE_DIR,
                session,
                params=["runner.ttl", "logs", "mock_pass", temp_dev, ""],
            )
            engine.load_script(MAIN_TTL)
            engine.run(strict_402=False)
            summary_files = glob.glob(os.path.join(logs_dir, "summary_*.csv"))
            self.assertGreater(len(summary_files), 0)
            with open(summary_files[-1], "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("INT-MOD-01,192.168.10.1,ssh,#,OK", content)
        finally:
            if os.path.exists(temp_dev):
                os.remove(temp_dev)

    def test_modular_multiple_cred_promotions_no_strdim_redeclare(self):
        """
        Regression test: calling auth_promote_cred_row multiple times in one run
        must NOT re-execute strdim (which raises a dialog error in real Tera Term 5.4.1).
        """
        logs_dir = self._clean_logs()
        with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=WORKSPACE_DIR, delete=False, encoding="utf-8") as tf_cred:
            tf_cred.write(
                "# Pattern,Username,Password,EnablePassword,Protocol\n"
                "Wrong1,w1,WRONG_PASS,,\n"
                "CiscoPat,admin,Pass1234,EnablePass123,ssh\n"
                "TelnetPat,admin,Pass1234,EnablePass123,telnet\n"
            )
            temp_cred = tf_cred.name

        with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=WORKSPACE_DIR, delete=False, encoding="utf-8") as tf_dev:
            # Device 1 promotes CiscoPat (row 2 -> row 1)
            # Device 2 (telnet-only) promotes TelnetPat (row 3 -> row 1)
            tf_dev.write(
                "PROMO-1,192.168.10.1,,,,ssh,commands_cisco.txt\n"
                "PROMO-2,192.168.10.252,,,,telnet,commands_cisco.txt\n"
            )
            temp_dev = tf_dev.name

        try:
            session = MockSession()
            engine = TTLEngine(
                WORKSPACE_DIR,
                session,
                params=["runner.ttl", "logs", "mock_pass", temp_dev, temp_cred],
            )
            engine.load_script(MAIN_TTL)
            engine.run(strict_402=False)

            with open(temp_cred, "r", encoding="utf-8") as f:
                clines = [l.strip() for l in f if l.strip()]
            self.assertEqual(clines[1], "TelnetPat,admin,Pass1234,EnablePass123,telnet")
            self.assertEqual(clines[2], "CiscoPat,admin,Pass1234,EnablePass123,ssh")
        finally:
            for p in (temp_cred, temp_dev):
                if os.path.exists(p):
                    os.remove(p)


if __name__ == "__main__":
    unittest.main()
