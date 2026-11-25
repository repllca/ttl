"""
TTL (Tera Term Language) Execution Engine for testing and validation.
Strictly verifies Tera Term 4.0x compatibility and simulates macro execution.
"""

import os
import re
import datetime

FORBIDDEN_POST_402_COMMANDS = {
    "strsplit",   # 4.67+
    "break",      # 4.53+
    "continue",   # 4.77+
    "sprintf2",   # 4.62+
    "do",         # 4.56+
    "loop",       # 4.56+
    "until",      # 4.56+
    "enduntil"    # 4.56+
}

def strip_ttl_comment(line):
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ";" and not in_single and not in_double:
            return line[:i].strip()
    return line.strip()

class TTLEngine:
    def __init__(self, workspace_dir, mock_session, params=None):
        self.workspace_dir = workspace_dir
        self.mock_session = mock_session
        self.params = params or ["device_runner.ttl", "logs", "mock_pass"]
        self.vars = {
            "result": 0,
            "inputstr": "",
            "timeout": 10,
            "paramcnt": len(self.params)
        }
        for i, p in enumerate(self.params, start=1):
            if i <= 9:
                self.vars[f"param{i}"] = p
        self.labels = {}
        self.call_stack = []
        self.if_stack = []
        self.include_stack = []
        self.open_files = {}
        self.next_fd = 1
        self.active_log = None
        self.pc = 0
        self.instructions = []
        self.stream_buffer = ""
        self.max_steps = 50000

    def load_script(self, ttl_path):
        with open(ttl_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        self.instructions = []
        self.labels = {}
        for line_num, raw in enumerate(lines, start=1):
            code = strip_ttl_comment(raw)
            self.instructions.append((line_num, code))
            if code.startswith(":"):
                label = code[1:].strip()
                if label in self.labels:
                    raise SyntaxError(f"Duplicate label :{label} at line {line_num}")
                self.labels[label] = line_num - 1  # 0-indexed instruction ptr

    def verify_strict_402_compatibility(self):
        """Ensure no commands from Tera Term 4.53+ are present."""
        for line_num, code in self.instructions:
            if not code or code.startswith(":"):
                continue
            first_word = code.split()[0].lower()
            if first_word in FORBIDDEN_POST_402_COMMANDS:
                raise SyntaxError(
                    f"Line {line_num}: Command '{first_word}' was introduced in a newer Tera Term version "
                    f"and is incompatible with Tera Term 4.02!"
                )
            # Check wait / waitln argument count <= 10
            if first_word in ("wait", "waitln"):
                args = re.findall(r"'[^']*'|\"[^\"]*\"|\S+", code[len(first_word):].strip())
                if len(args) > 10:
                    raise SyntaxError(f"Line {line_num}: {first_word} command exceeds 10 arguments limit ({len(args)})")

    def run(self, strict_402=True):
        if strict_402:
            self.verify_strict_402_compatibility()
        self.pc = 0
        step_count = 0

        # Initial buffer from mock session
        self.stream_buffer += self.mock_session.initial_bastion_prompt()

        while step_count < self.max_steps:
            while self.pc >= len(self.instructions) and self.include_stack:
                self.instructions, self.labels, self.pc, self.call_stack, self.if_stack = self.include_stack.pop()
            if self.pc >= len(self.instructions):
                break
            step_count += 1
            line_num, code = self.instructions[self.pc]
            if not code or code.startswith(":"):
                self.pc += 1
                continue

            self._execute_line(line_num, code)

        if step_count >= self.max_steps:
            raise RuntimeError(f"TTL execution exceeded maximum step limit ({self.max_steps}) - possible infinite loop")

    def _execute_line(self, line_num, code):
        # 1a. Array Element Assignment: ARR[IDX] = EXPR
        m_arr_assign = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\[([^\]]+)\]\s*=\s*(.*)$", code)
        if m_arr_assign:
            arr_name = m_arr_assign.group(1)
            idx = int(self._eval_expr(m_arr_assign.group(2)))
            val = self._eval_expr(m_arr_assign.group(3))
            arr = self.vars.setdefault(arr_name, [])
            if isinstance(arr, list) and 0 <= idx < len(arr):
                arr[idx] = val
            self.pc += 1
            return

        # 1. Variable Assignment: VAR = EXPR
        m_assign = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.*)$", code)
        if m_assign and not code.lower().startswith(("if ", "wait ", "timeout ")):
            left = m_assign.group(1)
            right = m_assign.group(2)
            val = self._eval_expr(right)
            self.vars[left] = val
            self.pc += 1
            return

        parts = code.split()
        cmd = parts[0].lower()

        # 1b. strdim <arr_name> <size> (Tera Term 4.67+ / 5.x string array - cannot be re-declared!)
        if cmd == "strdim":
            arr_name = parts[1]
            if arr_name in self.vars:
                raise RuntimeError(f"Line {line_num}: Array variable '{arr_name}' already declared with strdim")
            size = int(self._eval_expr(parts[2]))
            self.vars[arr_name] = [""] * size
            self.pc += 1
            return

        # 2. if <cond> <statement> or if <cond> then
        if cmd == "if":
            self._handle_if(code)
            return

        if cmd == "elseif":
            # Reached while executing a previous true branch; skip to endif
            self._skip_to_endif()
            return

        if cmd == "else":
            # Reached while executing a previous true branch; skip to endif
            self._skip_to_endif()
            return

        if cmd == "endif":
            if self.if_stack:
                self.if_stack.pop()
            self.pc += 1
            return

        # 3. goto <label>
        if cmd == "goto":
            label = parts[1]
            if label not in self.labels:
                raise ValueError(f"Line {line_num}: Undefined label {label}")
            self.pc = self.labels[label]
            return

        # 4. call <subroutine>
        if cmd == "call":
            sub_label = parts[1]
            if sub_label not in self.labels:
                raise ValueError(f"Line {line_num}: Undefined subroutine {sub_label}")
            self.call_stack.append(self.pc + 1)
            self.pc = self.labels[sub_label]
            return

        # 5. return
        if cmd == "return":
            if not self.call_stack:
                raise RuntimeError(f"Line {line_num}: 'return' without matching 'call'")
            self.pc = self.call_stack.pop()
            return

        # 6. sprintf / sprintf2
        if cmd == "sprintf":
            m = re.match(r"^sprintf\s+('[^']*'|\"[^\"]*\")\s*(.*)$", code, re.IGNORECASE)
            if m:
                fmt = m.group(1)[1:-1]
                args_str = m.group(2).strip()
                args = [self._eval_token(tok) for tok in re.findall(r"'[^']*'|\"[^\"]*\"|\S+", args_str)] if args_str else []
                out = fmt
                for a in args:
                    out = re.sub(r"%[sd]", lambda _: str(a), out, count=1)
                self.vars["inputstr"] = out
            self.pc += 1
            return

        if cmd == "sprintf2":
            m = re.match(r"^sprintf2\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+('[^']*'|\"[^\"]*\")\s*(.*)$", code, re.IGNORECASE)
            if m:
                dest_var = m.group(1)
                fmt = m.group(2)[1:-1]
                args_str = m.group(3).strip()
                args = [self._eval_token(tok) for tok in re.findall(r"'[^']*'|\"[^\"]*\"|\S+", args_str)] if args_str else []
                out = fmt
                for a in args:
                    out = re.sub(r"%[sd]", lambda _: str(a), out, count=1)
                self.vars[dest_var] = out
                self.vars["result"] = 0
            self.pc += 1
            return

        # 6b. strsplit <strval> <sep> [<count>] (Tera Term 4.67+)
        if cmd == "strsplit":
            tokens = re.findall(r"'[^']*'|\"[^\"]*\"|\S+", code[len(cmd):].strip())
            src = str(self._eval_token(tokens[0]))
            sep = str(self._eval_token(tokens[1]))
            max_parts = min(9, int(self._eval_token(tokens[2]))) if len(tokens) >= 3 else 9
            parts_out = src.split(sep, max_parts - 1) if sep else [src]
            for idx_p in range(1, 10):
                self.vars[f"groupmatchstr{idx_p}"] = parts_out[idx_p - 1] if idx_p <= len(parts_out) else ""
            self.vars["result"] = len(parts_out)
            self.pc += 1
            return

        # 6c. strtrim <strvar> <trimchars> (Tera Term 4.67+)
        if cmd == "strtrim":
            var = parts[1]
            rest = code[len(cmd):].strip()[len(var):].strip()
            trim_chars = str(self._eval_expr(rest))
            old_val = str(self.vars.get(var, ""))
            new_val = old_val.strip(trim_chars)
            self.vars[var] = new_val
            self.vars["result"] = 1 if new_val != old_val else 0
            self.pc += 1
            return

        # 6d. tolower <destvar> <src> (Tera Term 4.53+)
        if cmd == "tolower":
            dest = parts[1]
            rest = code[len(cmd):].strip()[len(dest):].strip()
            src = str(self._eval_expr(rest))
            self.vars[dest] = src.lower()
            self.pc += 1
            return

        # 7. strscan <strvar> <substr>
        if cmd == "strscan":
            str_var = parts[1]
            rest = code[len(cmd):].strip()[len(str_var):].strip()
            substr = str(self._eval_expr(rest))
            s = str(self.vars.get(str_var, ""))
            idx = s.find(substr)
            self.vars["result"] = idx + 1 if idx != -1 else 0
            self.pc += 1
            return

        # 8. strcopy <strvar> <pos> <len> <destvar>
        if cmd == "strcopy":
            tokens = parts[1:]
            s = str(self.vars.get(tokens[0], ""))
            pos = int(self._eval_expr(tokens[1]))
            length = int(self._eval_expr(tokens[2]))
            dest = tokens[3]
            # 1-based indexing
            start = max(0, pos - 1)
            self.vars[dest] = s[start:start + length]
            self.pc += 1
            return

        # 9. strlen <strvar>
        if cmd == "strlen":
            s = str(self.vars.get(parts[1], ""))
            self.vars["result"] = len(s)
            self.pc += 1
            return

        # 10. strcompare <strvar> <val>
        if cmd == "strcompare":
            s1 = str(self.vars.get(parts[1], ""))
            rest = code[len(cmd):].strip()[len(parts[1]):].strip()
            s2 = str(self._eval_expr(rest))
            self.vars["result"] = 0 if s1 == s2 else (1 if s1 > s2 else -1)
            self.pc += 1
            return

        # 11. strconcat <var> <expr>
        if cmd == "strconcat":
            var = parts[1]
            rest = code[len(cmd):].strip()[len(var):].strip()
            val = self._eval_expr(rest)
            self.vars[var] = str(self.vars.get(var, "")) + str(val)
            self.pc += 1
            return

        # 12. inputbox / passwordbox / messagebox
        if cmd in ("inputbox", "passwordbox"):
            tokens = re.findall(r"'[^']*'|\"[^\"]*\"|\S+", code[len(cmd):].strip())
            default_val = self._eval_token(tokens[2]) if len(tokens) > 2 else "mock_pass"
            self.vars["inputstr"] = default_val
            self.pc += 1
            return

        if cmd in ("messagebox", "statusbox", "closesbox", "settitle"):
            self.pc += 1
            return

        # 13. connect <expr>
        if cmd == "connect":
            # Simulate successful connection to bastion (result = 2)
            self.mock_session.state = "BASTION"
            self.vars["result"] = 2
            self.pc += 1
            return

        # 13b. testlink
        if cmd == "testlink":
            self.vars["result"] = 0 if self.mock_session.state == "DISCONNECTED" else 2
            self.pc += 1
            return

        # 13c. getdir / setdir
        if cmd == "getdir":
            self.vars[parts[1]] = self.workspace_dir
            self.pc += 1
            return

        if cmd == "setdir":
            self.pc += 1
            return

        # 14. disconnect
        if cmd == "disconnect":
            self.mock_session.receive_input("exit\n")
            self.pc += 1
            return

        # 15. timeout = X or timeout X
        if cmd == "timeout":
            self.vars["timeout"] = int(self._eval_expr(parts[-1]))
            self.pc += 1
            return

        # 15b. flushrecv
        if cmd == "flushrecv":
            self.stream_buffer = ""
            self.pc += 1
            return

        # 16. wait str1 str2 ...
        if cmd == "wait":
            wait_args = [str(self._eval_token(tok)) for tok in re.findall(r"'[^']*'|\"[^\"]*\"|\S+", code[5:].strip())]
            matched_idx = 0
            best_end = None
            # Match whichever pattern completes earliest in stream_buffer (real Tera Term stream order)
            for idx, pat in enumerate(wait_args, start=1):
                if not pat:
                    continue
                p_pos = self.stream_buffer.find(pat)
                if p_pos != -1:
                    end_pos = p_pos + len(pat)
                    if best_end is None or end_pos < best_end:
                        best_end = end_pos
                        matched_idx = idx
            if matched_idx > 0 and best_end is not None:
                self.stream_buffer = self.stream_buffer[best_end:]
            self.vars["result"] = matched_idx
            self.pc += 1
            return

        # 16b. waitln str1 str2 ...
        if cmd == "waitln":
            wait_args = [str(self._eval_token(tok)) for tok in re.findall(r"'[^']*'|\"[^\"]*\"|\S+", code[7:].strip())]
            matched_idx = 0
            lines = self.stream_buffer.splitlines(keepends=True)
            consumed_len = 0
            for line in lines:
                # Real Tera Term waitln requires a line break (CR/LF) at the end of the line
                if not line.endswith(("\n", "\r")):
                    break
                consumed_len += len(line)
                line_clean = line.rstrip("\r\n")
                if not line_clean:
                    continue
                for idx, pat in enumerate(wait_args, start=1):
                    if pat and pat in line_clean:
                        matched_idx = idx
                        self.vars["inputstr"] = line_clean
                        break
                if matched_idx > 0:
                    self.stream_buffer = self.stream_buffer[consumed_len:]
                    break
            self.vars["result"] = matched_idx
            self.pc += 1
            return

        # 17. sendln / send
        if cmd in ("sendln", "send"):
            arg_str = code[len(cmd):].strip()
            val = self._eval_expr(arg_str)
            text = str(val) + ("\n" if cmd == "sendln" else "")
            response = self.mock_session.receive_input(text)
            self.stream_buffer += response
            if self.active_log and response:
                self.active_log.write(response)
                self.active_log.flush()
            self.pc += 1
            return

        # 18. fileopen <fd_var> <filename> <append_flag> [<readonly_flag>]
        if cmd == "fileopen":
            fd_var = parts[1]
            fname = str(self._eval_token(parts[2]))
            append_flag = int(parts[3]) if len(parts) > 3 else 0
            readonly_flag = int(parts[4]) if len(parts) > 4 else 0

            # Normalize Windows backslashes in paths for cross-platform simulation
            fname_clean = os.path.normpath(fname.replace("\\", os.sep).replace("/", os.sep))
            filepath = os.path.join(self.workspace_dir, fname_clean) if not os.path.isabs(fname_clean) else fname_clean
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            try:
                if readonly_flag != 0:
                    py_mode = "r"
                elif append_flag != 0:
                    py_mode = "a+"
                else:
                    py_mode = "w+"
                fh = open(filepath, py_mode, encoding="utf-8")
                fd = self.next_fd
                self.next_fd += 1
                self.open_files[fd] = fh
                self.vars[fd_var] = fd
            except Exception:
                self.vars[fd_var] = -1
            self.pc += 1
            return

        # 19. filereadln <fd_var> <line_var>
        if cmd == "filereadln":
            fd = self.vars.get(parts[1], -1)
            line_var = parts[2]
            fh = self.open_files.get(fd)
            if fh:
                line = fh.readline()
                if not line:
                    self.vars["result"] = 1  # EOF
                    self.vars[line_var] = ""
                else:
                    # Accurately emulate real Tera Term filereadln:
                    # If the last line has no trailing CR/LF, Tera Term reads the line
                    # into <line_var> AND simultaneously sets result = 1 (EOF)!
                    if not line.endswith("\n") and not line.endswith("\r"):
                        self.vars["result"] = 1
                    else:
                        self.vars["result"] = 0
                    val = line.rstrip("\r\n")
                    # Simulate Tera Term 4.x raw-byte UTF-8 BOM (EF BB BF)
                    if val.startswith("\ufeff"):
                        val = "\xef\xbb\xbf" + val[1:]
                    self.vars[line_var] = val
            else:
                self.vars["result"] = 1
            self.pc += 1
            return

        # 20. filewriteln <fd_var> <val>
        if cmd == "filewriteln":
            fd = self.vars.get(parts[1], -1)
            val = self._eval_expr(code[len("filewriteln"):].strip()[len(parts[1]):].strip())
            fh = self.open_files.get(fd)
            if fh:
                fh.write(str(val) + "\n")
                fh.flush()
            self.pc += 1
            return

        # 21. fileclose <fd_var>
        if cmd == "fileclose":
            fd = self.vars.get(parts[1], -1)
            if fd in self.open_files:
                self.open_files[fd].close()
                del self.open_files[fd]
            self.pc += 1
            return

        # 21b. filedelete <filename>
        if cmd == "filedelete":
            fname = str(self._eval_expr(code[len(cmd):].strip()))
            fname_clean = os.path.normpath(fname.replace("\\", os.sep).replace("/", os.sep))
            filepath = os.path.join(self.workspace_dir, fname_clean) if not os.path.isabs(fname_clean) else fname_clean
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
                self.vars["result"] = 0
            except Exception:
                self.vars["result"] = -1
            self.pc += 1
            return

        # 22. logopen <fname> <bin> <append>
        if cmd == "logopen":
            fname = str(self._eval_token(parts[1]))
            fname_clean = os.path.normpath(fname.replace("\\", os.sep).replace("/", os.sep))
            filepath = os.path.join(self.workspace_dir, fname_clean) if not os.path.isabs(fname_clean) else fname_clean
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            try:
                self.active_log = open(filepath, "w", encoding="utf-8")
                self.vars["result"] = 0
            except Exception:
                self.vars["result"] = 1
            self.pc += 1
            return

        # 23. logwrite <val>
        if cmd == "logwrite":
            val = self._eval_expr(code[len("logwrite"):].strip())
            if self.active_log:
                self.active_log.write(str(val))
                self.active_log.flush()
            self.pc += 1
            return

        # 24. logclose
        if cmd == "logclose":
            if self.active_log:
                self.active_log.close()
                self.active_log = None
            self.pc += 1
            return

        # 25. getdate <var> [<format>]
        if cmd == "getdate":
            m = re.match(r"^getdate\s+(\S+)(?:\s+('[^']*'|\"[^\"]*\"))?", code, re.IGNORECASE)
            if m:
                var = m.group(1)
                fmt = m.group(2)[1:-1] if m.group(2) else "%Y%m%d_%H%M%S"
                now = datetime.datetime.now()
                py_fmt = fmt.replace("%Y", "%Y").replace("%m", "%m").replace("%d", "%d").replace("%H", "%H").replace("%M", "%M").replace("%S", "%S")
                self.vars[var] = now.strftime(py_fmt)
            self.pc += 1
            return

        # 26. pause / mpause / exec
        if cmd in ("pause", "mpause", "exec"):
            self.pc += 1
            return

        # 26b. include <filepath> (Tera Term 4.66+ / 5.x, max depth 9, file-local labels)
        if cmd == "include":
            if len(self.include_stack) >= 9:
                raise RuntimeError("TTL include nesting exceeds maximum depth of 9")
            inc_raw = str(self._eval_expr(code[len(cmd):].strip()))
            inc_clean = os.path.normpath(inc_raw.replace("\\", os.sep).replace("/", os.sep))
            inc_path = os.path.join(self.workspace_dir, inc_clean) if not os.path.isabs(inc_clean) else inc_clean
            if not os.path.exists(inc_path):
                raise FileNotFoundError(f"Line {line_num}: include file not found: {inc_path}")
            self.include_stack.append((self.instructions, self.labels, self.pc + 1, self.call_stack, self.if_stack))
            self.call_stack = []
            self.if_stack = []
            self.load_script(inc_path)
            self.pc = 0
            return

        # 26c. exit (returns from included file, or terminates main script)
        if cmd == "exit":
            if self.include_stack:
                self.instructions, self.labels, self.pc, self.call_stack, self.if_stack = self.include_stack.pop()
            else:
                self.pc = len(self.instructions)
            return

        # 27. end (terminates entire macro even inside included file)
        if cmd == "end":
            self.include_stack = []
            self.pc = len(self.instructions)
            return

        self.pc += 1

    def _handle_if(self, code):
        # Support:
        # if <cond> goto <label>
        # if <cond> exit / end
        # if <cond> then
        # if <cond> <statement>
        m_then = re.match(r"^if\s+(.*?)\s+then$", code, re.IGNORECASE)
        m_goto = re.match(r"^if\s+(.*?)\s+goto\s+([a-zA-Z0-9_]+)$", code, re.IGNORECASE)
        m_exit = re.match(r"^if\s+(.*?)\s+(exit|end)$", code, re.IGNORECASE)

        if m_goto:
            cond_str = m_goto.group(1)
            target = m_goto.group(2)
            if self._eval_condition(cond_str):
                self.pc = self.labels[target]
            else:
                self.pc += 1
            return

        if m_exit:
            cond_str = m_exit.group(1)
            kw = m_exit.group(2).lower()
            if self._eval_condition(cond_str):
                self._execute_line(0, kw)
            else:
                self.pc += 1
            return

        if m_then:
            cond_str = m_then.group(1)
            if self._eval_condition(cond_str):
                self.if_stack.append(True)
                self.pc += 1
            else:
                self.if_stack.append(False)
                self._skip_to_next_clause()
            return

        # Default Format 1: if <cond> <statement>
        m_stmt = re.match(r"^if\s+(\S.*?)\s+([a-zA-Z_]\S*.*)$", code)
        if m_stmt:
            cond_str = m_stmt.group(1)
            stmt = m_stmt.group(2)
            if self._eval_condition(cond_str):
                self._execute_line(0, stmt)
            else:
                self.pc += 1
            return

        self.pc += 1

    def _skip_to_next_clause(self):
        depth = 1
        self.pc += 1
        while self.pc < len(self.instructions):
            _, code = self.instructions[self.pc]
            if re.match(r"^if\b.*?\bthen\b", code, re.IGNORECASE):
                depth += 1
            elif re.match(r"^endif\b", code, re.IGNORECASE):
                depth -= 1
                if depth == 0:
                    if self.if_stack:
                        self.if_stack.pop()
                    self.pc += 1
                    return
            elif depth == 1:
                if re.match(r"^elseif\b.*?\bthen\b", code, re.IGNORECASE):
                    m = re.match(r"^elseif\s+(.*?)\s+then$", code, re.IGNORECASE)
                    if m and self._eval_condition(m.group(1)):
                        self.if_stack[-1] = True
                        self.pc += 1
                        return
                elif re.match(r"^else\b", code, re.IGNORECASE):
                    self.if_stack[-1] = True
                    self.pc += 1
                    return
            self.pc += 1

    def _skip_to_endif(self):
        depth = 1
        self.pc += 1
        while self.pc < len(self.instructions):
            _, code = self.instructions[self.pc]
            if re.match(r"^if\b.*?\bthen\b", code, re.IGNORECASE):
                depth += 1
            elif re.match(r"^endif\b", code, re.IGNORECASE):
                depth -= 1
                if depth == 0:
                    if self.if_stack:
                        self.if_stack.pop()
                    self.pc += 1
                    return
            self.pc += 1

    def _eval_condition(self, cond_str):
        """
        Accurately evaluate a Tera Term 4.x TTL conditional expression using REAL
        Tera Term operator precedence (NOT Python's logical precedence!):
          1 (highest): parentheses ( ... )
          2: unary not (~)
          5: bitwise and
          7: bitwise or
          8: relational <, >, <=, >= (left-associative, returns 1 or 0)
          9 (lowest): equality =, <> (left-associative, returns 1 or 0)
        This ensures unparenthesized 'A = 0 and B = 0' fails just like real Tera Term!
        """
        token_re = re.compile(
            r"\s*("
            r"&&|\|\||<>|<=|>=|==|!=|[=<>()]|"
            r"'[^']*'|\"[^\"]*\"|"
            r"-?\d+|"
            r"[a-zA-Z_][a-zA-Z0-9_]*"
            r")"
        )
        tokens = []
        pos = 0
        s = cond_str.strip()
        while pos < len(s):
            m = token_re.match(s, pos)
            if not m:
                return False
            tokens.append(m.group(1))
            pos = m.end()

        idx = 0

        def peek():
            return tokens[idx] if idx < len(tokens) else None

        def consume():
            nonlocal idx
            tok = tokens[idx]
            idx += 1
            return tok

        def to_int(val):
            if isinstance(val, int):
                return val
            if isinstance(val, str):
                try:
                    return int(val)
                except ValueError:
                    return 0
            return int(bool(val))

        def parse_log_or():
            left = parse_log_and()
            while peek() == "||":
                consume()
                right = parse_log_and()
                left = 1 if (to_int(left) != 0 or to_int(right) != 0) else 0
            return left

        def parse_log_and():
            left = parse_eq()
            while peek() == "&&":
                consume()
                right = parse_eq()
                left = 1 if (to_int(left) != 0 and to_int(right) != 0) else 0
            return left

        def parse_eq():
            left = parse_rel()
            while peek() in ("=", "==", "<>", "!="):
                op = consume()
                right = parse_rel()
                # Real Tera Term TTL forbids '=' / '<>' on strings ('Type mismatch')!
                if isinstance(left, str) or isinstance(right, str):
                    raise TypeError(f"TTL Type mismatch: cannot compare string with '{op}' in '{cond_str}' (use strcompare)")
                if op in ("=", "=="):
                    left = 1 if left == right else 0
                else:
                    left = 1 if left != right else 0
            return left

        def parse_rel():
            left = parse_or()
            while peek() in ("<", ">", "<=", ">="):
                op = consume()
                right = parse_or()
                lv, rv = to_int(left), to_int(right)
                if op == "<":
                    left = 1 if lv < rv else 0
                elif op == ">":
                    left = 1 if lv > rv else 0
                elif op == "<=":
                    left = 1 if lv <= rv else 0
                else:
                    left = 1 if lv >= rv else 0
            return left

        def parse_or():
            left = parse_and()
            while peek() is not None and peek().lower() == "or":
                consume()
                right = parse_and()
                left = to_int(left) | to_int(right)
            return left

        def parse_and():
            left = parse_unary()
            while peek() is not None and peek().lower() == "and":
                consume()
                right = parse_unary()
                left = to_int(left) & to_int(right)
            return left

        def parse_unary():
            if peek() is not None and peek().lower() == "not":
                consume()
                return ~to_int(parse_unary())
            return parse_primary()

        def parse_primary():
            tok = peek()
            if tok == "(":
                consume()
                val = parse_log_or()
                if peek() == ")":
                    consume()
                return val
            consume()
            if tok is None:
                return 0
            if (tok.startswith("'") and tok.endswith("'")) or (tok.startswith('"') and tok.endswith('"')):
                return tok[1:-1]
            if tok.lstrip("-").isdigit():
                return int(tok)
            if tok in self.vars:
                val = self.vars[tok]
                if isinstance(val, int):
                    return val
                if isinstance(val, str) and (val.isdigit() or (val.startswith("-") and val[1:].isdigit())):
                    return int(val)
                return val
            return 0

        try:
            res = parse_log_or()
            return to_int(res) != 0
        except TypeError:
            raise
        except Exception:
            return False

    def _eval_expr(self, expr_str):
        expr_str = expr_str.strip()
        if expr_str.startswith("#"):
            return self._eval_token(expr_str)
        if (expr_str.startswith("'") and expr_str.endswith("'")) or (expr_str.startswith('"') and expr_str.endswith('"')):
            return expr_str[1:-1]
        m_arr = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\[([^\]]+)\]$", expr_str)
        if m_arr:
            arr = self.vars.get(m_arr.group(1), [])
            idx = int(self._eval_expr(m_arr.group(2)))
            return arr[idx] if isinstance(arr, list) and 0 <= idx < len(arr) else ""
        if expr_str in self.vars:
            return self.vars[expr_str]
        # Basic arithmetic: a + b, a - b
        m_op = re.match(r"^(\S+)\s*([\+\-])\s*(\S+)$", expr_str)
        if m_op:
            v1 = int(self._eval_token(m_op.group(1)))
            v2 = int(self._eval_token(m_op.group(3)))
            return v1 + v2 if m_op.group(2) == "+" else v1 - v2
        try:
            return int(expr_str)
        except ValueError:
            return expr_str

    def _eval_token(self, token):
        token = token.strip()
        if (token.startswith("'") and token.endswith("'")) or (token.startswith('"') and token.endswith('"')):
            return token[1:-1]
        if token.startswith("#"):
            # ASCII character literal sequence e.g. #13#10 or #3
            m_chars = re.findall(r"#(\d+)", token)
            if m_chars:
                return "".join(chr(int(c)) for c in m_chars)
        if token in self.vars:
            return self.vars[token]
        m_arr = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\[([^\]]+)\]$", token)
        if m_arr:
            arr = self.vars.get(m_arr.group(1), [])
            idx = int(self._eval_expr(m_arr.group(2)))
            return arr[idx] if isinstance(arr, list) and 0 <= idx < len(arr) else ""
        try:
            return int(token)
        except ValueError:
            return token
