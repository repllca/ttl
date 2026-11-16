"""
Mock network environment simulating Bastion host and multi-vendor target devices:
- Bastion Linux shell
- Cisco IOS (User exec, enable escalation, --More-- paging)
- Yamaha router (User exec, enable rejection, administrator escalation)
- Juniper Junos (% shell)
- Linux server ($ prompt)
- Legacy Telnet switch
"""

import re

class MockSession:
    def __init__(self):
        self.state = "BASTION"
        self.current_device = None
        self.buffer = ""
        self.output_queue = []
        self.device_state = {}

    def initial_bastion_prompt(self):
        return "bastion_user@bastion:~$ "

    def receive_input(self, text):
        """Process input sent from TTL macro (sendln / send)."""
        lines = text.split("\n")
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
                return "bastion_user@bastion:~$ "

            # SSH command
            m_ssh = re.match(r"^ssh\s+(?:-o\s+\S+\s+)?(?:(\S+)@)?(\S+)", cmd)
            if m_ssh:
                user = m_ssh.group(1) or "bastion_user"
                ip = m_ssh.group(2)
                return self._start_device_session(ip, user, "ssh")

            # Telnet command
            m_telnet = re.match(r"^telnet\s+(\S+)", cmd)
            if m_telnet:
                ip = m_telnet.group(1)
                return self._start_device_session(ip, "", "telnet")

            if cmd == "exit":
                self.state = "DISCONNECTED"
                return "logout\r\nConnection to bastion closed.\r\n"

            return f"bash: {cmd}: command not found\r\nbastion_user@bastion:~$ "

        # 2. Inside device session
        device = self.device_state
        dtype = device.get("type")
        dstate = device.get("state")

        # Handling login prompts
        if dstate == "LOGIN_USER":
            device["state"] = "LOGIN_PASS"
            return "Password: "

        if dstate == "LOGIN_PASS":
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
                if device.get("mode") == "privileged":
                    # Drop back to unprivileged
                    device["mode"] = "user"
                    return "\r\n" + self._get_prompt(device)
                else:
                    # Return to bastion
                    self.state = "BASTION"
                    return "\r\nConnection to device closed.\r\nbastion_user@bastion:~$ "

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
                return "\r\n" + self._get_prompt(device)

            # show / execution commands
            if "show version" in cmd or "uname" in cmd or "cat" in cmd or "show" in cmd or "get" in cmd or "df" in cmd:
                prompt = self._get_prompt(device)
                if dtype == "cisco" and "show version" in cmd and not device.get("paging_disabled"):
                    # Trigger --More-- to test paging handler
                    device["paging"] = True
                    return f"\r\nCisco IOS Software Version 15.2\r\n--More--"
                return f"\r\nOutput for [{cmd}] on {dtype}\r\n{prompt}"

            # empty line
            if not cmd:
                return "\r\n" + self._get_prompt(device)

            return f"\r\nCommand: {cmd}\r\n" + self._get_prompt(device)

        return ""

    def _start_device_session(self, ip, user, proto):
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
            "paging": False
        }

        if proto == "ssh":
            return f"\r\nPassword: "
        else:
            return f"\r\nlogin: "

    def _finish_login(self, device):
        device["state"] = "READY"
        dtype = device["type"]
        if dtype == "cisco":
            return "\r\nRouter> "
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
            return "Router# " if mode == "privileged" else "Router> "
        elif dtype == "yamaha":
            return "Yamaha# " if mode == "privileged" else "Yamaha> "
        elif dtype == "linux":
            return "user@linux:~$ "
        elif dtype == "juniper":
            return "user@juniper% "
        elif dtype == "telnet_switch":
            return "Switch# "
        return "Node> "
