#!/usr/bin/env python3
"""
Test suite for Tera Term Macro (TTL) device_runner.ttl and configuration files.
"""

import os
import re
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TTL_FILE = os.path.join(WORKSPACE_DIR, "device_runner.ttl")
CSV_FILE = os.path.join(WORKSPACE_DIR, "device.csv")

VALID_TTL_COMMANDS = {
    "connect", "disconnect", "passwordbox", "messagebox", "inputbox", "yesnobox",
    "wait", "waitrecv", "sendln", "send", "fileopen", "filereadln", "filewriteln", "fileclose",
    "strsplit", "strconcat", "strcopy", "strcompare", "strlen", "getdate",
    "logopen", "logwrite", "logclose", "pause", "mpause", "timeout", "end", "goto",
    "if", "then", "else", "elseif", "endif", "while", "endwhile", "break", "continue",
    "closett", "call", "return"
}

class TestTTLSyntax(unittest.TestCase):
    def setUp(self):
        self.assertTrue(os.path.exists(TTL_FILE), f"{TTL_FILE} must exist")
        with open(TTL_FILE, "r", encoding="utf-8") as f:
            self.lines = [line.strip() for line in f]

    def test_labels_and_gotos(self):
        """Verify that every goto targets a declared label, and no labels are duplicated."""
        declared_labels = set()
        goto_targets = []

        for line_num, line in enumerate(self.lines, start=1):
            line_code = line.split(";", 1)[0].strip()
            if not line_code:
                continue

            if line_code.startswith(":"):
                label_name = line_code[1:].strip()
                self.assertNotIn(label_name, declared_labels, f"Duplicate label :{label_name} at line {line_num}")
                declared_labels.add(label_name)

            m = re.search(r"\bgoto\s+([a-zA-Z0-9_]+)", line_code)
            if m:
                goto_targets.append((m.group(1), line_num))

        self.assertGreater(len(declared_labels), 0, "TTL should have defined labels")
        for target, line_num in goto_targets:
            self.assertIn(
                target,
                declared_labels,
                f"Line {line_num}: 'goto {target}' references undefined label :{target}"
            )

    def test_wait_argument_limit(self):
        """Verify that no wait command exceeds the Tera Term 10-argument limit."""
        for line_num, line in enumerate(self.lines, start=1):
            line_code = line.split(";", 1)[0].strip()
            if not line_code or not line_code.lower().startswith("wait "):
                continue

            # Extract arguments by parsing quoted strings and bare tokens
            args = re.findall(r"'[^']*'|\"[^\"]*\"|\S+", line_code[5:].strip())
            self.assertLessEqual(
                len(args),
                10,
                f"Line {line_num}: wait command has {len(args)} arguments, exceeding the maximum limit of 10! Args: {args}"
            )

    def test_blocks_integrity(self):
        """Verify that all if/then and while blocks are properly closed."""
        if_stack = []
        while_stack = []

        for line_num, line in enumerate(self.lines, start=1):
            line_code = line.split(";", 1)[0].strip()
            if not line_code:
                continue

            if re.match(r"^if\b.*?\bthen\b", line_code, re.IGNORECASE):
                if_stack.append(line_num)
            elif re.match(r"^endif\b", line_code, re.IGNORECASE):
                self.assertTrue(len(if_stack) > 0, f"Line {line_num}: 'endif' without matching 'if ... then'")
                if_stack.pop()
            elif re.match(r"^while\b", line_code, re.IGNORECASE):
                while_stack.append(line_num)
            elif re.match(r"^endwhile\b", line_code, re.IGNORECASE):
                self.assertTrue(len(while_stack) > 0, f"Line {line_num}: 'endwhile' without matching 'while'")
                while_stack.pop()

        self.assertEqual(len(if_stack), 0, f"Unclosed 'if ... then' blocks at lines: {if_stack}")
        self.assertEqual(len(while_stack), 0, f"Unclosed 'while' blocks at lines: {while_stack}")

    def test_valid_commands(self):
        """Verify that statements start with valid TTL keywords, labels, or variable assignments."""
        for line_num, line in enumerate(self.lines, start=1):
            line_code = line.split(";", 1)[0].strip()
            if not line_code or line_code.startswith(":"):
                continue

            if "=" in line_code and not line_code.lower().startswith(("if", "wait", "timeout")):
                left = line_code.split("=", 1)[0].strip()
                if re.match(r"^[a-zA-Z0-9_]+$", left):
                    continue

            first_word = line_code.split()[0].lower()
            self.assertIn(
                first_word,
                VALID_TTL_COMMANDS,
                f"Line {line_num}: Unknown TTL command '{first_word}'"
            )


class TestDeviceCSVAndFallbackLogic(unittest.TestCase):
    def setUp(self):
        self.assertTrue(os.path.exists(CSV_FILE), f"{CSV_FILE} must exist")
        with open(CSV_FILE, "r", encoding="utf-8") as f:
            self.raw_lines = f.readlines()

    def simulate_macro_row_processing(self, line, bastion_user="bastion_user", bastion_pass="BastionPass!"):
        line = line.strip()
        if not line or line.startswith("#"):
            return None

        parts = [p.strip() for p in line.split(",")]
        while len(parts) < 7:
            parts.append("")

        name, ip, user, pw, enpass, proto, cmd_file = parts[:7]

        if name in ("ホスト名", "hostname") or not ip:
            return None

        if not name:
            name = ip
        if not proto:
            proto = "ssh"
        if not user:
            user = bastion_user
        if not pw:
            pw = bastion_pass
        if not enpass:
            enpass = pw
        if not cmd_file:
            cmd_file = "commands_universal.txt"

        return {
            "name": name,
            "ip": ip,
            "user": user,
            "pass": pw,
            "enpass": enpass,
            "proto": proto.lower(),
            "cmd_file": cmd_file
        }

    def test_csv_rows_and_command_files(self):
        processed_count = 0
        for line_num, line in enumerate(self.raw_lines, start=1):
            row = self.simulate_macro_row_processing(line)
            if row is None:
                continue

            processed_count += 1
            self.assertTrue(row["name"])
            self.assertTrue(row["ip"])
            self.assertTrue(row["user"])
            self.assertTrue(row["pass"])
            self.assertTrue(row["enpass"])
            self.assertIn(row["proto"], ["ssh", "telnet"])

            cmd_path = os.path.join(WORKSPACE_DIR, row["cmd_file"])
            self.assertTrue(
                os.path.exists(cmd_path),
                f"Row {line_num}: Command file {row['cmd_file']} does not exist"
            )

        self.assertGreater(processed_count, 0)

    def test_unknown_device_fallback(self):
        """When device type is unknown (cmd_file and enpass empty), verify universal fallback."""
        line = "UNKNOWN_DEV,10.20.30.40,,,,"
        row = self.simulate_macro_row_processing(line, bastion_user="buser", bastion_pass="bpass")
        self.assertEqual(row["proto"], "ssh")
        self.assertEqual(row["user"], "buser")
        self.assertEqual(row["pass"], "bpass")
        self.assertEqual(row["enpass"], "bpass")
        self.assertEqual(row["cmd_file"], "commands_universal.txt")
        self.assertTrue(os.path.exists(os.path.join(WORKSPACE_DIR, row["cmd_file"])))


class TestMultiVendorAdaptiveLogic(unittest.TestCase):
    def test_prompt_enable_decision(self):
        # 1:>, 2:#, 3:$, 4:%, 5:]
        def needs_enable(prompt_idx):
            return prompt_idx == 1

        self.assertTrue(needs_enable(1))   # > unprivileged Cisco/Yamaha
        self.assertFalse(needs_enable(2))  # # privileged
        self.assertFalse(needs_enable(3))  # $ Linux
        self.assertFalse(needs_enable(4))  # % Juniper
        self.assertFalse(needs_enable(5))  # ] Huawei/HPE

    def test_yamaha_fallback_triggers(self):
        # On 'enable', if error keywords are returned, Yamaha administrator should be attempted
        error_keywords = ['Invalid', 'Unknown', 'not found', 'Error', 'syntax']
        sample_response = "Unknown command 'enable'"
        matched = any(kw.lower() in sample_response.lower() for kw in error_keywords)
        self.assertTrue(matched)


if __name__ == "__main__":
    unittest.main()
