"""
Give the dashboard something to show.

    cd example
    vise serve          # in one terminal
    python traffic.py   # in another

Walks the application the way a person would, but faster and without stopping:
reads, writes, searches, queues exports that fail, opens a websocket, and asks
for the route that raises. Runs until interrupted.

The websocket client below is written out by hand rather than pulled from a
package. It is forty lines and it keeps this script runnable with nothing but
the standard library, which matters for a file whose whole job is to be the
first thing somebody runs.

The mix is chosen so no panel stays empty and none is drowned by one kind of
traffic. Nothing here talks to the dashboard; it drives the application, and the
dashboard watches.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import random
import struct
import sys
import urllib.error
import urllib.request

#: How often each kind of call is made, relative to the others. The failing
#: ones are rare on purpose: a panel where every request is a 500 is as
#: uninformative as one where none is.
MIX: list[tuple[str, str, int]] = [
    ("GET", "/", 6),
    ("GET", "/api/v1/documents", 6),
    ("GET", "/api/v1/documents/{id}", 8),
    ("GET", "/api/v1/search", 4),
    ("POST", "/api/v1/documents", 3),
    ("GET", "/api/v1/upstream", 3),
    ("POST", "/api/v1/reindex", 2),
    ("GET", "/api/v1/slow", 2),
    ("POST", "/api/v1/invites", 1),
    ("POST", "/api/v1/workspaces/{id}/export", 1),
    ("GET", "/api/v1/insights", 1),
    ("WS", "/ws/documents/{id}", 2),
]


def call(base: str, method: str, path: str) -> int:
    """Make one request.

    Args:
        base: The server's base URL.
        method: The HTTP method.
        path: The path, with any placeholder already filled.

    Returns:
        The status code, or 0 when the server could not be reached.
    """
    body = b'{"title": "A document from traffic.py", "body": "Written by a robot."}'

    request = urllib.request.Request(
        f"{base}{path}",
        method=method,
        data=body if method == "POST" else None,
        headers={"content-type": "application/json", "user-agent": "traffic.py"},
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        # A 404 or a 500 is traffic too, and the interesting kind.
        return error.code
    except OSError:
        return 0


def choose() -> tuple[str, str]:
    """Pick the next call, weighted by the mix.

    Returns:
        A method and a path, with any placeholder filled.
    """
    method, path, _ = random.choices(MIX, weights=[weight for _, _, weight in MIX])[0]
    return method, path.replace("{id}", str(random.randint(1, 18)))


async def websocket(base: str, path: str) -> str:
    """Connect, say something, listen, and leave.

    A whole websocket client in one function: the opening handshake, one masked
    text frame out, one frame in, then a close. No library, because this script
    should run on a fresh checkout with nothing installed.

    Args:
        base: The server's base URL.
        path: The websocket path.

    Returns:
        What happened, for the log line.
    """
    host = base.split("//", 1)[-1]
    hostname, _, port = host.partition(":")

    try:
        reader, writer = await asyncio.open_connection(hostname, int(port or 80))
    except OSError as error:
        return f"— {error.strerror or error}"

    try:
        key = base64.b64encode(os.urandom(16)).decode()
        writer.write(
            (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        await writer.drain()

        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        if b"101" not in head.split(b"\r\n", 1)[0]:
            return "no upgrade"

        await _send_frame(writer, json.dumps({"hello": "from traffic.py"}))

        # One frame back, then close. Enough for the Real-time panel to count a
        # connection, a message in each direction and the bytes both ways.
        await asyncio.wait_for(_read_frame(reader), timeout=5)

        # Opcode 8 is close, and a client frame is always masked.
        writer.write(b"\x88\x80" + os.urandom(4))
        await writer.drain()
        return "101"
    except (TimeoutError, asyncio.IncompleteReadError, OSError):
        return "— closed early"
    finally:
        writer.close()


async def _send_frame(writer: asyncio.StreamWriter, text: str) -> None:
    """Write one masked text frame.

    Args:
        writer: The open connection.
        text: What to send.
    """
    payload = text.encode()
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

    header = b"\x81"  # final fragment, opcode 1 (text)
    if len(payload) < 126:
        header += bytes([0x80 | len(payload)])
    else:
        header += b"\xfe" + struct.pack("!H", len(payload))

    writer.write(header + mask + masked)
    await writer.drain()


async def _read_frame(reader: asyncio.StreamReader) -> bytes:
    """Read one frame's payload.

    The server never masks, so this only has to handle the two short length
    forms — which is every frame this application sends.

    Args:
        reader: The open connection.

    Returns:
        The payload bytes.
    """
    header = await reader.readexactly(2)
    length = header[1] & 0x7F

    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]

    return await reader.readexactly(length)


async def main() -> int:
    """Drive the application until interrupted.

    Returns:
        The exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Where the server is")
    parser.add_argument("--rate", type=float, default=4.0, help="Requests per second")
    parser.add_argument("--count", type=int, default=0, help="Stop after this many. 0 = forever")
    options = parser.parse_args()

    base = options.url.rstrip("/")
    gap = 1.0 / max(0.1, options.rate)

    if call(base, "GET", "/api/v1/health") == 0:
        print(f"Nothing is answering at {base}. Start it with: vise serve", file=sys.stderr)
        return 1

    print(f"Driving {base} at {options.rate:g}/s. Ctrl-C to stop.")

    made = 0
    try:
        while options.count == 0 or made < options.count:
            method, path = choose()

            if method == "WS":
                status = await websocket(base, path)
            else:
                status = await asyncio.to_thread(call, base, method, path)

            made += 1

            print(f"  {made:>5}  {method:<5} {path:<42} {status or '—'}")
            await asyncio.sleep(gap * random.uniform(0.5, 1.5))
    except KeyboardInterrupt:
        pass

    print(f"\nMade {made} requests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
