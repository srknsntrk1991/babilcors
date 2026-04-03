import argparse
import asyncio
import base64


async def rover_task(host, port, mountpoint, user, password, secs):
    reader, writer = await asyncio.open_connection(host, port)
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = f"GET /{mountpoint} HTTP/1.0\r\nAuthorization: Basic {auth}\r\n\r\n"
    writer.write(req.encode()); await writer.drain()
    await reader.readline()  # status line
    # drain headers
    while True:
        ln = await reader.readline()
        if not ln or ln in (b"\r\n", b"\n"):
            break
    end = asyncio.get_event_loop().time() + secs
    total = 0
    while asyncio.get_event_loop().time() < end:
        chunk = await reader.read(4096)
        if not chunk:
            break
        total += len(chunk)
    writer.close(); await writer.wait_closed()
    return total


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=2101)
    p.add_argument("--mountpoint", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--conns", type=int, default=10)
    p.add_argument("--secs", type=int, default=60)
    args = p.parse_args()
    tasks = [rover_task(args.host, args.port, args.mountpoint, args.user, args.password, args.secs) for _ in range(args.conns)]
    res = await asyncio.gather(*tasks, return_exceptions=True)
    ok = sum(x for x in res if isinstance(x, int))
    print(f"received_bytes={ok}")


if __name__ == "__main__":
    asyncio.run(main())

