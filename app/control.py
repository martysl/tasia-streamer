from __future__ import annotations

import socket


def liquidsoap_command(command: str, host: str = "127.0.0.1", port: int = 1234) -> str:
    data = b""
    with socket.create_connection((host, port), timeout=3) as sock:
        sock.settimeout(2)
        sock.sendall((command.strip() + "\n").encode("utf-8"))
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"END\n" in data or b"END\r\n" in data:
                    break
        except socket.timeout:
            pass
    return data.decode("utf-8", errors="replace")
