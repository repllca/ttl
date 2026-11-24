#!/usr/bin/env python3
"""
Real SSH2 + TCP Telnet Lab Server for GitHub Actions CI (with real Tera Term 5.4.1 ttpmacro.exe).

Architecture:
  Real Tera Term (ttpmacro.exe / ttermpro.exe)
    --[Real SSH2]--> Bastion SSH2 Server (127.0.0.1:2222)
                       |--[Real paramiko SSH2]--> Target SSH2 Server (127.0.0.1:2223)
                       '--[Real TCP Socket]-----> Target Telnet Server (127.0.0.1:2323)
"""

import argparse
import os
import re
import socket
import sys
import threading
import time

import paramiko

HOST_KEY = paramiko.RSAKey.generate(2048)

VALID_USERS = {
    "admin": "Pass1234",
    "user1": "Pass1234",
}
ENABLE_PASS = "EnablePass123"


def run_device_shell(ip, user, send_fn, recv_line_fn, proto="ssh"):
    """Interactive Cisco / Linux device session over real SSH2 or TCP Telnet channel."""
    is_linux = (ip == "192.168.10.3")
    if is_linux:
        prompt = f"[{user}@srv-linux ~]$ "
        send_fn(b"\r\nWelcome to Ubuntu 22.04 LTS\r\n" + prompt.encode())
        while True:
            cmd = recv_line_fn()
            if cmd is None or cmd.strip() in ("exit", "logout", "quit"):
                send_fn(b"\r\nlogout\r\n")
                return
            cmd = cmd.strip()
            if not cmd:
                send_fn(b"\r\n" + prompt.encode())
            elif cmd == "uname -a":
                send_fn(b"\r\nLinux srv-linux 5.15.0-101-generic #101-Ubuntu SMP x86_64\r\n" + prompt.encode())
            else:
                send_fn(f"\r\n{cmd}: ok\r\n{prompt}".encode())
    else:
        hostname = "TELNET-CISCO" if proto == "telnet" else "RT-CISCO-01"
        priv = False
        prompt = f"{hostname}>"
        send_fn(b"\r\n" + prompt.encode())
        while True:
            cmd = recv_line_fn()
            if cmd is None:
                return
            cmd = cmd.strip()
            if cmd in ("exit", "logout", "quit"):
                send_fn(b"\r\n")
                return
            if not cmd:
                send_fn(b"\r\n" + prompt.encode())
                continue
            if cmd == "enable" and not priv:
                send_fn(b"\r\nPassword: ")
                pw = recv_line_fn()
                if pw is not None and pw.strip() == ENABLE_PASS:
                    priv = True
                    prompt = f"{hostname}#"
                    send_fn(b"\r\n" + prompt.encode())
                else:
                    send_fn(b"\r\n% Bad secrets\r\n" + prompt.encode())
                continue
            if cmd == "terminal length 0":
                send_fn(b"\r\n" + prompt.encode())
            elif cmd.startswith(("set cli ", "screen-length ", "console ")):
                send_fn(b"\r\n% Invalid input detected at '^' marker.\r\n" + prompt.encode())
            elif cmd == "show version":
                send_fn(b"\r\nCisco IOS Software, Version 15.9(3)M\r\n" + prompt.encode())
            elif cmd == "show ip interface brief":
                send_fn(b"\r\nGigabitEthernet0/0 192.168.10.1 YES manual up up\r\n" + prompt.encode())
            else:
                send_fn(f"\r\nCommand: {cmd}\r\n{prompt}".encode())


class TargetSSHServer(paramiko.ServerInterface):
    def __init__(self):
        self.event = threading.Event()
        self.username = ""
        self.ip = "192.168.10.1"

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_auth_password(self, username, password):
        if VALID_USERS.get(username) == password:
            self.username = username
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        return True

    def check_channel_shell_request(self, channel):
        self.event.set()
        return True


def handle_target_ssh_conn(client_sock):
    transport = paramiko.Transport(client_sock)
    transport.add_server_key(HOST_KEY)
    server = TargetSSHServer()
    try:
        transport.start_server(server=server)
        chan = transport.accept(20)
        if chan is None:
            return
        server.event.wait(10)
        # First line sent internally or default Cisco
        buf = b""

        def send_fn(data):
            chan.sendall(data)

        def recv_line_fn():
            nonlocal buf
            while True:
                if b"\n" in buf or b"\r" in buf:
                    idx_n = buf.find(b"\n")
                    idx_r = buf.find(b"\r")
                    candidates = [i for i in (idx_n, idx_r) if i != -1]
                    idx = min(candidates)
                    line = buf[:idx]
                    rest = buf[idx + 1:]
                    if buf[idx:idx + 1] == b"\r" and rest.startswith(b"\n"):
                        rest = rest[1:]
                    buf = rest
                    return line.decode("utf-8", errors="replace")
                chunk = chan.recv(1024)
                if not chunk:
                    return None
                buf += chunk

        run_device_shell(server.ip, server.username, send_fn, recv_line_fn, proto="ssh")
        chan.close()
    except Exception:
        pass
    finally:
        transport.close()


def handle_telnet_conn(conn):
    try:
        conn.sendall(b"\r\nUser Access Verification\r\n\r\nUsername: ")
        buf = b""

        def recv_line():
            nonlocal buf
            while True:
                if b"\n" in buf or b"\r" in buf:
                    candidates = [i for i in (buf.find(b"\n"), buf.find(b"\r")) if i != -1]
                    idx = min(candidates)
                    line = buf[:idx]
                    rest = buf[idx + 1:]
                    if buf[idx:idx + 1] == b"\r" and rest.startswith(b"\n"):
                        rest = rest[1:]
                    buf = rest
                    return line.decode("utf-8", errors="replace")
                chunk = conn.recv(1024)
                if not chunk:
                    return None
                buf += chunk

        user = recv_line()
        if user is None:
            return
        conn.sendall(b"Password: ")
        pw = recv_line()
        if pw is None:
            return
        if VALID_USERS.get(user.strip()) != pw.strip():
            conn.sendall(b"\r\n% Login invalid\r\nUsername: ")
            # Wait briefly for client disconnect
            try:
                conn.recv(1024)
            except Exception:
                pass
            return

        run_device_shell("192.168.10.252", user.strip(), conn.sendall, recv_line, proto="telnet")
    except Exception:
        pass
    finally:
        conn.close()


class BastionSSHServer(paramiko.ServerInterface):
    def __init__(self):
        self.event = threading.Event()
        self.username = ""

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_auth_password(self, username, password):
        if username == "bastion_user" and password == "mock_pass":
            self.username = username
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        return True

    def check_channel_shell_request(self, channel):
        self.event.set()
        return True


def handle_bastion_conn(client_sock, target_ssh_port, target_telnet_port):
    transport = paramiko.Transport(client_sock)
    transport.add_server_key(HOST_KEY)
    server = BastionSSHServer()
    try:
        transport.start_server(server=server)
        chan = transport.accept(20)
        if chan is None:
            return
        server.event.wait(10)
        prompt = b"[bastion_user@bastion ~]$ "
        chan.sendall(b"\r\n" + prompt)

        buf = b""

        def read_bastion_line():
            nonlocal buf
            while True:
                if b"\n" in buf or b"\r" in buf:
                    candidates = [i for i in (buf.find(b"\n"), buf.find(b"\r")) if i != -1]
                    idx = min(candidates)
                    line = buf[:idx]
                    rest = buf[idx + 1:]
                    if buf[idx:idx + 1] == b"\r" and rest.startswith(b"\n"):
                        rest = rest[1:]
                    buf = rest
                    return line.decode("utf-8", errors="replace")
                chunk = chan.recv(1024)
                if not chunk:
                    return None
                if b"\x03" in chunk:
                    buf = b""
                    chan.sendall(b"^C\r\n" + prompt)
                    continue
                chan.sendall(chunk)
                buf += chunk

        while True:
            cmd = read_bastion_line()
            if cmd is None:
                break
            cmd_s = cmd.strip()
            print(f"[BASTION RECV] {cmd_s!r}", flush=True)
            if not cmd_s:
                chan.sendall(b"\r\n" + prompt)
                continue
            if cmd_s == "exit":
                chan.sendall(b"\r\nlogout\r\n")
                break
            if 'echo "__BAS""TION_SYNC__"' in cmd_s and not cmd_s.startswith(("ssh ", "telnet ")):
                chan.sendall(b"\r\n__BASTION_SYNC__\r\n" + prompt)
                continue

            has_ret = False
            if '; echo "__BAS""TION_RETURN__"' in cmd_s:
                has_ret = True
                cmd_s = cmd_s.split('; echo "__BAS""TION_RETURN__"', 1)[0].strip()

            if cmd_s.startswith("ssh "):
                tokens = cmd_s.split()[1:]
                target = ""
                has_legacy_kex = False
                i = 0
                while i < len(tokens):
                    if tokens[i] == "-o" and i + 1 < len(tokens):
                        if "diffie-hellman-group1-sha1" in tokens[i + 1]:
                            has_legacy_kex = True
                        i += 2
                        continue
                    if tokens[i].startswith("-"):
                        i += 1
                        continue
                    target = tokens[i]
                    break
                user, ip = target.split("@", 1) if "@" in target else ("bastion_user", target)

                if ip in ("192.168.10.254", "192.168.10.252"):
                    chan.sendall(f"\r\nssh: connect to host {ip} port 22: Connection refused\r\n".encode())
                elif ip == "192.168.10.249" and not has_legacy_kex:
                    chan.sendall(
                        f"\r\nUnable to negotiate with {ip} port 22: no matching key exchange method found.\r\n".encode()
                    )
                else:
                    # Prompt password and authenticate against REAL Target SSH2 server!
                    chan.sendall(f"\r\n{user}@{ip}'s password: ".encode())
                    pw = read_bastion_line()
                    if pw is not None:
                        t_sock = socket.create_connection(("127.0.0.1", target_ssh_port), timeout=10)
                        t_trans = paramiko.Transport(t_sock)
                        try:
                            t_trans.connect(username=user, password=pw.strip())
                            t_chan = t_trans.open_session()
                            t_chan.get_pty()
                            t_chan.invoke_shell()

                            # Bridge Bastion SSH channel <-> Target SSH channel
                            def fwd_target_to_bastion():
                                try:
                                    while True:
                                        data = t_chan.recv(4096)
                                        if not data:
                                            break
                                        chan.sendall(data)
                                except Exception:
                                    pass

                            t_thread = threading.Thread(target=fwd_target_to_bastion, daemon=True)
                            t_thread.start()

                            if buf:
                                t_chan.sendall(buf)
                                buf = b""
                            while not t_chan.closed and t_thread.is_alive():
                                if chan.recv_ready():
                                    d = chan.recv(4096)
                                    if not d:
                                        break
                                    t_chan.sendall(d)
                                else:
                                    time.sleep(0.02)
                            t_thread.join(timeout=1.0)
                        except paramiko.AuthenticationException:
                            chan.sendall(b"\r\nPermission denied, please try again.\r\n")
                        finally:
                            t_trans.close()

            elif cmd_s.startswith("telnet "):
                ip = cmd_s.split()[1]
                if ip == "192.168.10.251":
                    chan.sendall(f"\r\nTrying {ip}...\r\nConnection closed by foreign host.\r\n".encode())
                else:
                    chan.sendall(f"\r\nTrying {ip}...\r\nConnected to {ip}.\r\n".encode())
                    tsock = socket.create_connection(("127.0.0.1", target_telnet_port), timeout=10)
                    tsock.settimeout(0.05)
                    try:
                        while True:
                            try:
                                data = tsock.recv(4096)
                                if not data:
                                    break
                                chan.sendall(data)
                            except socket.timeout:
                                pass
                            if chan.recv_ready():
                                d = chan.recv(4096)
                                if not d:
                                    break
                                if b"\x1d" in d:  # Ctrl+]
                                    chan.sendall(b"\r\ntelnet> ")
                                    tcmd = read_bastion_line()
                                    if tcmd and tcmd.strip() == "quit":
                                        chan.sendall(b"Connection closed.\r\n")
                                        break
                                else:
                                    tsock.sendall(d)
                    finally:
                        tsock.close()
            else:
                chan.sendall(f"\r\nbash: {cmd_s}: command not found\r\n".encode())

            if has_ret:
                chan.sendall(b"__BASTION_RETURN__\r\n")
            chan.sendall(prompt)
        chan.close()
    except Exception:
        pass
    finally:
        transport.close()


def start_servers(bastion_port=2222, target_ssh_port=2223, target_telnet_port=2323):
    def serve_loop(port, handler):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(16)
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=handler, args=(conn,), daemon=True).start()

    threading.Thread(
        target=serve_loop,
        args=(bastion_port, lambda c: handle_bastion_conn(c, target_ssh_port, target_telnet_port)),
        daemon=True,
    ).start()
    threading.Thread(target=serve_loop, args=(target_ssh_port, handle_target_ssh_conn), daemon=True).start()
    threading.Thread(target=serve_loop, args=(target_telnet_port, handle_telnet_conn), daemon=True).start()


def run_self_test():
    start_servers(2222, 2223, 2323)
    time.sleep(0.3)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect("127.0.0.1", port=2222, username="bastion_user", password="mock_pass")
    chan = client.invoke_shell()
    time.sleep(0.2)
    out = chan.recv(4096).decode()
    assert "[bastion_user@bastion ~]$" in out, out
    chan.sendall('ssh -o NumberOfPasswordPrompts=1 admin@192.168.10.1 ; echo "__BAS""TION_RETURN__"\n')
    time.sleep(0.3)
    out = chan.recv(4096).decode()
    assert "password:" in out.lower(), out
    chan.sendall("Pass1234\n")
    time.sleep(0.4)
    out = chan.recv(4096).decode()
    assert "RT-CISCO-01>" in out, out
    chan.sendall("exit\n")
    time.sleep(0.3)
    out = chan.recv(4096).decode()
    assert "__BASTION_RETURN__" in out, out
    client.close()
    print("REAL SSH2 BASTION + TARGET SELF-TEST OK!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--bastion-port", type=int, default=2222)
    parser.add_argument("--target-ssh-port", type=int, default=2223)
    parser.add_argument("--target-telnet-port", type=int, default=2323)
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        sys.exit(0)

    start_servers(args.bastion_port, args.target_ssh_port, args.target_telnet_port)
    print(f"CI Lab running: Bastion SSH={args.bastion_port}, Target SSH={args.target_ssh_port}, Telnet={args.target_telnet_port}", flush=True)
    while True:
        time.sleep(1)
