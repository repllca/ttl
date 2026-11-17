"""
Mock network environment simulating Bastion host and multi-vendor target devices:
- Bastion Linux shell (supports '; echo "__BAS""TION_RETURN__"' command chaining)
- Pre-authentication banners containing '#', '>', '$', ']' to test banner resilience
- Cisco IOS (User exec, enable escalation, --More-- paging, '#' comments in config)
- Yamaha router (User exec, enable rejection, administrator escalation)
- Juniper Junos (% shell)
- Linux server ($ prompt, '#101-Ubuntu' in uname -a, '<LOOPBACK>' in ip addr)
- Legacy Telnet switch
- Error scenarios:
  - 192.168.10.254 : Connection refused / unreachable
  - 192.168.10.253 : SSH KexAlgorithms / HostKeyAlgorithms mismatch
  - Password 'WRONG_PASS' : Authentication failure (Permission denied)
"""

import re


class MockSession:
    def __init__(self):
        self.state = "BASTION"
        self.current_device = None
        self.buffer = ""
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
        if "\x03" in text:
            # Ctrl+C sent: abort current device connection and return to bastion
            self.state = "BASTION"
            self.pending_bastion_marker = False
            return "^C\r\n[bastion_user@bastion ~]$ "

        if text.endswith("\n"):
            lines = text[:-1].split("\n")
        else:
            lines = [text]

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

            # Check forstandalone echo marker
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
                i = 0
                while i < len(tokens):
                    if tokens[i] == "-o":
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
                return self._start_device_session(ip, user, "ssh")

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

        # Handling login prompts
        if dstate == "LOGIN_USER":
            device["state"] = "LOGIN_PASS"
            return "Password: "

        if dstate == "LOGIN_PASS":
            if cmd == "WRONG_PASS":
                device["fail_count"] = device.get("fail_count", 0) + 1
                if device["fail_count"] >= 3:
                    self.state = "BASTION"
                    return "\r\nPermission denied (publickey,password).\r\n" + self._bastion_return_str()
                return "\r\nPermission denied, please try again.\r\nPassword: "
            return self._finish_login(device)

        if dstate == "ENABLE_PASS":
            if dtype == "cisco":
                device["mode"] = "privileged"
                device["state"] = "READY"
                return "\r\nRT-CISCO-01# "
            elif dtype == "yamaha":
                device["mode"] = "privileged"
                device["state"] = "READY"
                return "\r\nYamaha# "

        # Command handling in READY state
        if dstate == "READY":
            # Paging check
            if device.get("paging"):
                if cmd == " " or cmd == "":
                    device["paging"] = False
                    prompt = self._get_prompt(device)
                    return f"\r\nAdditional output page 2\r\n{prompt}"

            # exit / quit / logout
            if cmd in ("exit", "quit", "logout"):
                if device.get("mode") == "privileged" and dtype in ("cisco", "yamaha") and cmd == "exit":
                    # Drop back to unprivileged
                    device["mode"] = "user"
                    return "\r\n" + self._get_prompt(device)
                else:
                    # Return to bastion
                    self.state = "BASTION"
                    return "\r\nConnection to device closed.\r\n" + self._bastion_return_str()

            # enable command
            if cmd == "enable":
                if dtype == "cisco":
                    device["state"] = "ENABLE_PASS"
                    return "Password: "
                elif dtype == "yamaha":
                    return "% Unknown command 'enable'\r\nYamaha> "
                else:
                    return f"{cmd}: command not found\r\n" + self._get_prompt(device)

            # administrator command (Yamaha)
            if cmd == "administrator":
                if dtype == "yamaha":
                    device["state"] = "ENABLE_PASS"
                    return "Password: "
                else:
                    return f"{cmd}: command not found\r\n" + self._get_prompt(device)

            # pager disable commands
            if cmd in ("terminal length 0", "set cli screen-length 0", "screen-length 0 temporary",
                       "console lines infinity", "config paging disable"):
                device["paging_disabled"] = True
                return "\r\n" + self._get_prompt(device)

            # Realistic outputs containing '#', '>', '$', ']' to verify prompt matching resilience
            prompt = self._get_prompt(device)
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

            # empty line
            if not cmd:
                return "\r\n" + prompt

            return f"\r\nCommand: {cmd}\r\n" + prompt

        return ""

    def _start_device_session(self, ip, user, proto):
        # Simulated unreachable IP
        if ip == "192.168.10.254":
            self.state = "BASTION"
            return (
                f"\r\nssh: connect to host {ip} port 22: Connection refused\r\n"
                + self._bastion_return_str()
            )

        # Simulated SSH legacy algorithm mismatch IP
        if ip == "192.168.10.253":
            self.state = "BASTION"
            return (
                f"\r\nUnable to negotiate with {ip} port 22: no matching key exchange method found. "
                f"Their offer: diffie-hellman-group1-sha1\r\n"
                + self._bastion_return_str()
            )

        self.state = "DEVICE"
        # Determine device type by IP
        if ip == "192.168.10.1":
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
            "mode": "user",
            "state": "LOGIN_PASS" if proto == "ssh" else "LOGIN_USER",
            "paging": False,
            "paging_disabled": False,
            "fail_count": 0
        }

        # Include realistic pre-login banner containing '#', '>', '$', ']'
        banner = (
            "\r\n#################################################\r\n"
            "# WARNING: Authorized Access Only [Node > $]    #\r\n"
            "#################################################\r\n"
        )
        if proto == "ssh":
            return banner + "Password: "
        else:
            return banner + "login: "

    def _finish_login(self, device):
        device["state"] = "READY"
        dtype = device["type"]
        if dtype == "cisco":
            return "\r\nRT-CISCO-01> "
        elif dtype == "yamaha":
            return "\r\nYamaha> "
        elif dtype == "linux":
            return "\r\nuser@linux:~$ "
        elif dtype == "juniper":
            return "\r\nuser@juniper% "
        elif dtype == "telnet_switch":
            device["mode"] = "privileged"
            return "\r\nSwitch# "
        return "\r\nNode> "

    def _get_prompt(self, device):
        dtype = device.get("type")
        mode = device.get("mode")
        if dtype == "cisco":
            return "RT-CISCO-01# " if mode == "privileged" else "RT-CISCO-01> "
        elif dtype == "yamaha":
            return "Yamaha# " if mode == "privileged" else "Yamaha> "
        elif dtype == "linux":
            return "user@linux:~$ "
        elif dtype == "juniper":
            return "user@juniper% "
        elif dtype == "telnet_switch":
            return "Switch# "
        return "Node> "
