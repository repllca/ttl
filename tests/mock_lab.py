"""
Mock network environment simulating Bastion host and multi-vendor target devices:
- Bastion Linux shell (supports '; echo "__BAS""TION_RETURN__"' & '__BASTION_SYNC__')
- Proper input buffering for split 'send' + 'sendln' sequences
- Pre-authentication banners containing '#', '>', '$', ']' to test banner resilience
- Cisco IOS (User exec, enable escalation, '% Invalid input' on unknown paging cmds, --More-- paging, '#' comments in config)
- Yamaha router (User exec, enable rejection, administrator escalation)
- Juniper Junos (> prompt)
- Linux server ($ prompt, '#101-Ubuntu' in uname -a, '<LOOPBACK>' in ip addr)
- Legacy Telnet switch (192.168.10.5)
- Telnet-only Cisco switch (192.168.10.252: SSH port 22 refused, Telnet port 23 prompts 'Username: ' and requires Ctrl+] + quit on auth fail)
- Error scenarios:
  - 192.168.10.254 : Connection refused / unreachable (both SSH and Telnet)
  - 192.168.10.253 : SSH KexAlgorithms / HostKeyAlgorithms mismatch
  - 192.168.10.250 : Drops connection during enable password
  - Password 'WRONG_PASS' / user 'wrong_user' : Authentication failure
"""

import re


class MockSession:
    def __init__(self):
        self.state = "BASTION"
        self.current_device = None
        self.input_buffer = ""
        self.output_queue = []
        self.device_state = {}
        self.pending_bastion_marker = False

    def initial_bastion_prompt(self):
        return "[bastion_user@bastion ~]$ "

    def _bastion_return_str(self):
        marker = ""
        if self.pending_bastion_marker:
            marker = "__BASTION_RETURN__\r\n"
            self.pending_bastion_marker = False
        return f"{marker}[bastion_user@bastion ~]$ "

    def receive_input(self, text):
        """Process input sent from TTL macro (sendln / send)."""
        # Control character: Ctrl+] (\x1d) -> Telnet escape prompt
        if "\x1d" in text:
            self.input_buffer = ""
            if self.state == "DEVICE" and self.device_state.get("proto") == "telnet":
                self.device_state["state"] = "TELNET_ESCAPE"
                return "\r\ntelnet> "
            return ""

        # Control character: Ctrl+C (\x03)
        if "\x03" in text:
            self.input_buffer = ""
            if self.state == "DEVICE":
                proto = self.device_state.get("proto")
                dstate = self.device_state.get("state")
                if proto == "telnet":
                    # Realistic Linux telnet: Ctrl+C does NOT exit telnet client!
                    if dstate == "ENABLE_PASS":
                        self.device_state["state"] = "READY"
                        return "\r\n" + self._get_prompt(self.device_state)
                    return "^C\r\n"
                if dstate == "ENABLE_PASS":
                    # Inside an authenticated SSH session, Ctrl+C cancels enable password prompt
                    self.device_state["state"] = "READY"
                    return "\r\n" + self._get_prompt(self.device_state)
                # SSH client during auth: Ctrl+C aborts SSH back to bastion
                self.state = "BASTION"
                self.pending_bastion_marker = False
                return "^C\r\n[bastion_user@bastion ~]$ "
            return "^C\r\n[bastion_user@bastion ~]$ "

        # Single space during paging (--More--) triggers immediately without newline
        if (
            self.state == "DEVICE"
            and self.device_state.get("state") == "READY"
            and self.device_state.get("paging")
            and text == " "
        ):
            self.device_state["paging"] = False
            prompt = self._get_prompt(self.device_state)
            return f"\r\nAdditional output page 2\r\n{prompt}"

        # Buffer characters until newline (supports split send + sendln commands)
        self.input_buffer += text
        if "\n" not in self.input_buffer:
            return ""

        parts = self.input_buffer.split("\n")
        self.input_buffer = parts[-1]
        lines = parts[:-1]

        response = ""
        for line in lines:
            line_clean = line.strip("\r")
            resp = self._handle_line(line_clean)
            if resp:
                response += resp
        return response

    def _handle_line(self, cmd):
        # 1. Bastion state
        if self.state == "BASTION":
            if not cmd:
                return "\r\n[bastion_user@bastion ~]$ "

            # Standalone sync / return markers
            if 'echo "__BAS""TION_SYNC__"' in cmd and not cmd.startswith(("ssh ", "telnet ")):
                return "__BASTION_SYNC__\r\n[bastion_user@bastion ~]$ "
            if 'echo "__BAS""TION_RETURN__"' in cmd and not cmd.startswith(("ssh ", "telnet ")):
                return "__BASTION_RETURN__\r\n[bastion_user@bastion ~]$ "

            # Strip chained '; echo "__BAS""TION_RETURN__"' if present
            if '; echo "__BAS""TION_RETURN__"' in cmd:
                self.pending_bastion_marker = True
                cmd = cmd.split('; echo "__BAS""TION_RETURN__"', 1)[0].strip()

            # SSH command (with arbitrary -o options)
            if cmd.startswith("ssh "):
                tokens = cmd.split()[1:]
                target = ""
                num_pw_prompts = 3
                i = 0
                while i < len(tokens):
                    if tokens[i] == "-o":
                        if i + 1 < len(tokens) and tokens[i + 1].startswith("NumberOfPasswordPrompts="):
                            try:
                                num_pw_prompts = int(tokens[i + 1].split("=", 1)[1])
                            except ValueError:
                                pass
                        i += 2
                        continue
                    if tokens[i].startswith("-"):
                        i += 1
                        continue
                    target = tokens[i]
                    break
                if "@" in target:
                    user, ip = target.split("@", 1)
                else:
                    user, ip = "bastion_user", target
                return self._start_device_session(ip, user, "ssh", num_pw_prompts=num_pw_prompts)

            # Telnet command
            m_telnet = re.match(r"^telnet\s+(\S+)", cmd)
            if m_telnet:
                ip = m_telnet.group(1)
                return self._start_device_session(ip, "", "telnet")

            if cmd == "exit":
                self.state = "DISCONNECTED"
                return "logout\r\nConnection to bastion closed.\r\n"

            return f"bash: {cmd}: command not found\r\n[bastion_user@bastion ~]$ "

        # 2. Inside device session
        device = self.device_state
        dtype = device.get("type")
        dstate = device.get("state")

        # Telnet client escape prompt (after Ctrl+])
        if dstate == "TELNET_ESCAPE":
            if cmd in ("quit", "q", "close"):
                self.state = "BASTION"
                return "Connection closed.\r\n" + self._bastion_return_str()
            return "telnet> "

        # Handling login prompts
        if dstate == "LOGIN_USER":
            device["user"] = cmd
            if cmd in ("wrong_user", "bad_user"):
                device["bad_user"] = True
            else:
                device["bad_user"] = False
            device["state"] = "LOGIN_PASS"
            return "Password: "

        if dstate == "LOGIN_PASS":
            if cmd.startswith("WRONG") or cmd.startswith("bad_") or device.get("bad_user"):
                device["fail_count"] = device.get("fail_count", 0) + 1
                max_tries = device.get("num_pw_prompts", 3) if device.get("proto") == "ssh" else 3
                if device.get("proto") == "ssh" and device["fail_count"] >= max_tries:
                    self.state = "BASTION"
                    return "\r\nPermission denied (publickey,password).\r\n" + self._bastion_return_str()
                if dtype == "cisco_telnet_only":
                    device["state"] = "LOGIN_USER"
                    return "\r\n% Authentication failed\r\n\r\nUsername: "
                if dtype in ("cisco", "cisco_drop_enable"):
                    return "\r\n% Login invalid\r\n\r\nPassword: "
                return "\r\nPermission denied, please try again.\r\nPassword: "
            return self._finish_login(device)

        if dstate == "ENABLE_PASS":
            if dtype == "cisco_drop_enable":
                self.state = "BASTION"
                return "\r\nConnection to 192.168.10.250 closed by foreign host.\r\n" + self._bastion_return_str()
            if cmd.startswith("WRONG"):
                return "\r\nPassword: "
            if dtype in ("cisco", "cisco_telnet_only"):
                device["mode"] = "privileged"
                device["state"] = "READY"
                return "\r\n" + self._get_prompt(device)
            elif dtype == "yamaha":
                device["mode"] = "privileged"
                device["state"] = "READY"
                return "\r\nYamaha# "

        # Command handling in READY state
        if dstate == "READY":
            prompt = self._get_prompt(device)

            # Paging check
            if device.get("paging"):
                if cmd == " " or cmd == "":
                    device["paging"] = False
                    return f"\r\nAdditional output page 2\r\n{prompt}"

            # exit / quit / logout
            if cmd in ("exit", "quit", "logout"):
                if device.get("mode") == "privileged" and dtype in ("cisco", "cisco_telnet_only", "yamaha") and cmd == "exit":
                    # Drop back to unprivileged
                    device["mode"] = "user"
                    return "\r\n" + self._get_prompt(device)
                else:
                    # Return to bastion
                    self.state = "BASTION"
                    return "\r\nConnection to device closed.\r\n" + self._bastion_return_str()

            # enable command
            if cmd == "enable":
                if dtype in ("cisco", "cisco_telnet_only", "cisco_drop_enable"):
                    device["state"] = "ENABLE_PASS"
                    return "Password: "
                elif dtype == "yamaha":
                    return "% Unknown command 'enable'\r\nYamaha> "
                else:
                    return f"{cmd}: command not found\r\n" + prompt

            # administrator command (Yamaha)
            if cmd == "administrator":
                if dtype == "yamaha":
                    device["state"] = "ENABLE_PASS"
                    return "Password: "
                else:
                    return f"{cmd}: command not found\r\n" + prompt

            # Pager disable commands: simulate REALISTIC Cisco '% Invalid input detected' on non-Cisco cmds!
            if cmd == "terminal length 0":
                if dtype in ("cisco", "cisco_telnet_only", "telnet_switch"):
                    device["paging_disabled"] = True
                    return "\r\n" + prompt
                return f"\r\n% Unknown command\r\n{prompt}"

            if cmd in ("set cli screen-length 0", "screen-length 0 temporary", "console lines infinity"):
                if dtype in ("cisco", "cisco_telnet_only"):
                    return (
                        f"\r\n{cmd}\r\n"
                        "        ^\r\n"
                        "% Invalid input detected at '^' marker.\r\n\r\n"
                        + prompt
                    )
                device["paging_disabled"] = True
                return "\r\n" + prompt

            # Realistic outputs containing '#', '>', '$', ']' to verify prompt matching resilience
            if cmd == "uname -a":
                return (
                    "\r\nLinux srv-linux-01 5.15.0-91-generic #101-Ubuntu SMP "
                    "Tue Nov 14 13:30:08 UTC 2023 x86_64 GNU/Linux\r\n" + prompt
                )
            if cmd == "ip addr":
                return (
                    "\r\n1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN\r\n"
                    "    inet 127.0.0.1/8 scope host lo\r\n"
                    "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP\r\n" + prompt
                )
            if cmd == "show running-config":
                return (
                    "\r\nBuilding configuration...\r\n"
                    "! # Comment with special chars > $ % ]\r\n"
                    "hostname RT-CISCO-01\r\n"
                    "end\r\n" + prompt
                )

            if "show version" in cmd or "cat" in cmd or "show" in cmd or "get" in cmd or "df" in cmd or "free" in cmd or "ifconfig" in cmd:
                if dtype == "cisco" and "show version" in cmd and not device.get("paging_disabled"):
                    device["paging"] = True
                    return "\r\nCisco IOS Software Version 15.2\r\n--More--"
                return f"\r\nOutput for [{cmd}] on {dtype}\r\n{prompt}"

            # Empty Enter: echo CRLF + prompt (realistic terminal behavior for 2-Enter waitln!)
            if not cmd:
                return "\r\n" + prompt

            return f"\r\nCommand: {cmd}\r\n" + prompt

        return ""

    def _start_device_session(self, ip, user, proto, num_pw_prompts=3):
        # Simulated unreachable IP (both SSH and Telnet refused)
        if ip == "192.168.10.254":
            self.state = "BASTION"
            if proto == "ssh":
                return (
                    f"\r\nssh: connect to host {ip} port 22: Connection refused\r\n"
                    + self._bastion_return_str()
                )
            return (
                f"\r\ntelnet: Unable to connect to remote host: Connection refused\r\n"
                + self._bastion_return_str()
            )

        # Simulated SSH legacy algorithm mismatch IP
        if ip == "192.168.10.253" and proto == "ssh":
            self.state = "BASTION"
            return (
                f"\r\nUnable to negotiate with {ip} port 22: no matching key exchange method found. "
                f"Their offer: diffie-hellman-group1-sha1\r\n"
                + self._bastion_return_str()
            )

        # Simulated Telnet-only Cisco switch (SSH port 22 refused, Telnet port 23 works!)
        if ip == "192.168.10.252" and proto == "ssh":
            self.state = "BASTION"
            return (
                f"\r\nssh: connect to host {ip} port 22: Connection refused\r\n"
                + self._bastion_return_str()
            )

        # Simulated Telnet-not-accepted host (closes immediately on Telnet before login prompt)
        if ip == "192.168.10.251" and proto == "telnet":
            self.state = "BASTION"
            return (
                f"\r\nTrying {ip}...\r\n"
                f"Connected to {ip}.\r\n"
                f"Connection closed by foreign host.\r\n"
                + self._bastion_return_str()
            )

        self.state = "DEVICE"
        if ip == "192.168.10.250":
            dtype = "cisco_drop_enable"
        elif ip == "192.168.10.252":
            dtype = "cisco_telnet_only"
        elif ip == "192.168.10.1":
            dtype = "cisco"
        elif ip == "192.168.10.2":
            dtype = "yamaha"
        elif ip == "192.168.10.3":
            dtype = "linux"
        elif ip == "192.168.10.4":
            dtype = "juniper"
        elif ip == "192.168.10.5":
            dtype = "telnet_switch"
        else:
            dtype = "cisco"

        self.device_state = {
            "type": dtype,
            "ip": ip,
            "proto": proto,
            "user": user,
            "bad_user": user in ("wrong_user", "bad_user"),
            "mode": "user",
            "state": "LOGIN_PASS" if proto == "ssh" else "LOGIN_USER",
            "paging": False,
            "paging_disabled": False,
            "fail_count": 0,
            "num_pw_prompts": num_pw_prompts,
        }

        banner = (
            "\r\n#################################################\r\n"
            "# WARNING: Authorized Access Only [Node > $]    #\r\n"
            "#################################################\r\n"
        )
        if proto == "ssh":
            return banner + "Password: "
        if dtype == "cisco_telnet_only":
            return banner + "\r\nUser Access Verification\r\n\r\nUsername: "
        return banner + "login: "

    def _finish_login(self, device):
        device["state"] = "READY"
        dtype = device["type"]
        if dtype == "cisco":
            return "\r\nRT-CISCO-01> "
        elif dtype == "cisco_telnet_only":
            return "\r\nTELNET-CISCO> "
        elif dtype == "yamaha":
            return "\r\nYamaha> "
        elif dtype == "linux":
            return (
                "\r\nLast failed login: Fri Nov 20 11:00:00 JST 2026 from 192.168.1.1 on ssh:notty\r\n"
                "Last login: Fri Nov 20 10:55:00 JST 2026 from 192.168.1.1\r\n"
                "user@linux:~$ "
            )
        elif dtype == "juniper":
            return "\r\nuser@juniper> "
        elif dtype == "telnet_switch":
            device["mode"] = "privileged"
            return "\r\nSwitch# "
        return "\r\nNode> "

    def _get_prompt(self, device):
        dtype = device.get("type")
        mode = device.get("mode")
        if dtype == "cisco":
            return "RT-CISCO-01# " if mode == "privileged" else "RT-CISCO-01> "
        elif dtype == "cisco_telnet_only":
            return "TELNET-CISCO# " if mode == "privileged" else "TELNET-CISCO> "
        elif dtype == "yamaha":
            return "Yamaha# " if mode == "privileged" else "Yamaha> "
        elif dtype == "linux":
            return "user@linux:~$ "
        elif dtype == "juniper":
            return "user@juniper> "
        elif dtype == "telnet_switch":
            return "Switch# "
        return "Node> "
