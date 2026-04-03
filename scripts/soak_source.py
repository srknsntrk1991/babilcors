import argparse
import asyncio


async def source_task(host, port, mountpoint, password, rate_bps, secs):
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(f"SOURCE {password} /{mountpoint}\r\n".encode()); await writer.drain()
    await reader.readline()  # expect ICY 200 OK
    end = asyncio.get_event_loop().time() + secs
    chunk = b"0" * 512
    delay = len(chunk) / max(rate_bps, 1)
    sent = 0
    while asyncio.get_event_loop().time() < end:
        writer.write(chunk); await writer.drain(); sent += len(chunk)
        await asyncio.sleep(delay)
    writer.close(); await writer.wait_closed()
    print(f"sent_bytes={sent}")


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=2101)
    p.add_argument("--mountpoint", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--rate-bps", type=int, default=20000)
    p.add_argument("--secs", type=int, default=60)
    args = p.parse_args()
    await source_task(args.host, args.port, args.mountpoint, args.password, args.rate_bps, args.secs)


if __name__ == "__main__":
    asyncio.run(main())

