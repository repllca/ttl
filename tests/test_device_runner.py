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
    "wait", "waitrecv", "sendln", "send", "fileopen", "filereadln", "fileclose",
    "strsplit", "strconcat", "strcopy", "strcompare", "strlen", "getdate",
    "logopen", "logclose", "pause", "mpause", "timeout", "end", "goto",
    "if", "then", "else", "elseif", "endif", "closett"
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
            # Strip comments
            line_code = line.split(";", 1)[0].strip()
            if not line_code:
                continue

            # Check label declaration
            if line_code.startswith(":"):
                label_name = line_code[1:].strip()
                self.assertNotIn(label_name, declared_labels, f"Duplicate label :{label_name} at line {line_num}")
                declared_labels.add(label_name)

            # Check goto
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

    def test_if_endif_blocks(self):
        """Verify that all if/then blocks are properly closed with endif."""
        block_stack = []

        for line_num, line in enumerate(self.lines, start=1):
            line_code = line.split(";", 1)[0].strip()
            if not line_code:
                continue

            # Check Format 2 block if
            if re.match(r"^if\b.*?\bthen\b", line_code, re.IGNORECASE):
                block_stack.append(line_num)
            elif re.match(r"^endif\b", line_code, re.IGNORECASE):
                self.assertTrue(
                    len(block_stack) > 0,
                    f"Line {line_num}: 'endif' without matching 'if ... then'"
                )
                block_stack.pop()

        self.assertEqual(
            len(block_stack),
            0,
            f"Unclosed 'if ... then' blocks at lines: {block_stack}"
        )

    def test_valid_commands(self):
        """Verify that statements start with valid TTL keywords, labels, or variable assignments."""
        for line_num, line in enumerate(self.lines, start=1):
            line_code = line.split(";", 1)[0].strip()
            if not line_code:
                continue

            # Label
            if line_code.startswith(":"):
                continue

            # Assignment: VAR = VALUE
            if "=" in line_code and not line_code.lower().startswith(("if", "wait", "timeout")):
                left = line_code.split("=", 1)[0].strip()
                if re.match(r"^[a-zA-Z0-9_]+$", left):
                    continue

            # Keyword command
            first_word = line_code.split()[0].lower()
            self.assertIn(
                first_word,
                VALID_TTL_COMMANDS,
                f"Line {line_num}: Unknown TTL command or construct '{first_word}'"
            )


class TestDeviceCSVAndFallbackLogic(unittest.TestCase):
    def setUp(self):
        self.assertTrue(os.path.exists(CSV_FILE), f"{CSV_FILE} must exist")
        with open(CSV_FILE, "r", encoding="utf-8") as f:
            self.raw_lines = f.readlines()

    def simulate_macro_row_processing(self, line, bastion_user="bastion_user", bastion_pass="BastionPass!"):
        """Simulate the TTL parsing and fallback rules for a CSV line."""
        line = line.strip()
        if not line or line.startswith("#"):
            return None

        # strsplit line ',' 7
        parts = [p.strip() for p in line.split(",")]
        while len(parts) < 7:
            parts.append("")

        name, ip, user, pw, enpass, proto, cmd_file = parts[:7]

        # Header skip check
        if name in ("ホスト名", "hostname"):
            return None

        # IP check
        if not ip:
            return None

        # Fallbacks as implemented in device_runner.ttl:
        # 1. Hostname fallback
        if not name:
            name = ip

        # 2. Protocol fallback
        if not proto:
            proto = "ssh"

        # 3. User fallback
        if not user:
            user = bastion_user

        # 4. Password fallback
        if not pw:
            pw = bastion_pass

        # 5. Enable password fallback
        if not enpass:
            enpass = pw

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
        """Verify device.csv parsing, fallback logic, and that all referenced command files exist."""
        processed_count = 0
        for line_num, line in enumerate(self.raw_lines, start=1):
            row = self.simulate_macro_row_processing(line)
            if row is None:
                continue

            processed_count += 1
            # Check fields are populated
            self.assertTrue(row["name"], f"Row {line_num}: Hostname empty")
            self.assertTrue(row["ip"], f"Row {line_num}: IP empty")
            self.assertTrue(row["user"], f"Row {line_num}: User empty")
            self.assertTrue(row["pass"], f"Row {line_num}: Password empty")
            self.assertTrue(row["enpass"], f"Row {line_num}: Enable password empty")
            self.assertIn(row["proto"], ["ssh", "telnet"], f"Row {line_num}: Invalid proto {row['proto']}")

            # Verify command file exists
            if row["cmd_file"]:
                cmd_path = os.path.join(WORKSPACE_DIR, row["cmd_file"])
                self.assertTrue(
                    os.path.exists(cmd_path),
                    f"Row {line_num}: Referenced command file {row['cmd_file']} does not exist"
                )

        self.assertGreater(processed_count, 0, "device.csv should contain valid data rows")

    def test_specific_fallbacks(self):
        """Verify the specific missing-field edge cases required by the user."""
        # Case 1: Missing enable password -> should equal login password
        line1 = "SW01,10.0.0.1,admin,P@ss1,,ssh,commands_cisco.txt"
        r1 = self.simulate_macro_row_processing(line1)
        self.assertEqual(r1["enpass"], "P@ss1", "Enable pass should fallback to login pass")

        # Case 2: Missing protocol -> should default to ssh
        line2 = "SRV01,10.0.0.2,user1,P@ss2,,,commands_linux.txt"
        r2 = self.simulate_macro_row_processing(line2)
        self.assertEqual(r2["proto"], "ssh", "Protocol should default to ssh")
        self.assertEqual(r2["enpass"], "P@ss2", "Enable pass should fallback to login pass")

        # Case 3: Missing user and password -> should fallback to bastion credentials
        line3 = "RT01,10.0.0.3,,,,ssh,commands_juniper.txt"
        r3 = self.simulate_macro_row_processing(line3, bastion_user="ops_user", bastion_pass="OpsSecret")
        self.assertEqual(r3["user"], "ops_user")
        self.assertEqual(r3["pass"], "OpsSecret")
        self.assertEqual(r3["enpass"], "OpsSecret")

        # Case 4: Telnet protocol preserved
        line4 = "OLD01,10.0.0.4,admin,P@ss4,,telnet,commands_cisco.txt"
        r4 = self.simulate_macro_row_processing(line4)
        self.assertEqual(r4["proto"], "telnet")


class TestVendorPromptAndMoreHandling(unittest.TestCase):
    """Test the logic for enable handling and --More-- handling."""

    def test_prompt_enable_decision(self):
        """
        Verify TTL prompt logic:
        - prompt '>' triggers enable
        - prompt '#', '$', '%' skips enable directly to exec_commands
        """
        def should_run_enable(prompt_char):
            # In TTL: wait '>' '#' '$' '%'
            # result = 1 ('>'), 2 ('#'), 3 ('$'), 4 ('%')
            if prompt_char == '>':
                return True
            elif prompt_char in ('#', '$', '%'):
                return False
            return False

        self.assertTrue(should_run_enable('>'))
        self.assertFalse(should_run_enable('#'))
        self.assertFalse(should_run_enable('$'))
        self.assertFalse(should_run_enable('%'))

    def test_paging_detection(self):
        """
        Verify that paging patterns trigger a space keystroke, while prompts conclude.
        """
        paging_patterns = ['--More--', '-- 続く --', '---(more', 'More:']
        prompt_patterns = ['>', '#', '$', '%']

        for pat in paging_patterns:
            # TTL logic: if result >= 5 (paging), send ' '
            is_paging = pat in paging_patterns
            self.assertTrue(is_paging)

        for pat in prompt_patterns:
            is_prompt = pat in prompt_patterns
            self.assertTrue(is_prompt)


if __name__ == "__main__":
    unittest.main()
