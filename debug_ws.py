#!/usr/bin/env python3
"""
WebSocket diagnostic for Tibber live feed.
Run this and paste the output so we can see exactly what the server sends.

Usage:
    $env:TIBBER_TOKEN="your_token"
    python debug_ws.py
"""
import asyncio
import json
import os
import sys
import websockets

TIBBER_TOKEN = os.getenv("TIBBER_TOKEN", "")
WS_URL = "wss://api.tibber.com/v1-beta/gql/subscriptions"

# Try fetching home ID first via HTTP to confirm token works
import urllib.request

def get_home_id():
    query = '{"query":"{ viewer { homes { id } } }"}'
    req = urllib.request.Request(
        "https://api.tibber.com/v1-beta/gql",
        data=query.encode(),
        headers={
            "Authorization": f"Bearer {TIBBER_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data["data"]["viewer"]["homes"][0]["id"]


async def test_connection(subprotocol, init_payload):
    label = f"subprotocol={subprotocol!r}  payload={init_payload}"
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"{'='*60}")
    try:
        kwargs = {"ping_interval": None}
        if subprotocol:
            kwargs["subprotocols"] = [subprotocol]

        async with websockets.connect(WS_URL, **kwargs) as ws:
            print(f"  Connected. Negotiated subprotocol: {ws.subprotocol!r}")

            await ws.send(json.dumps({"type": "connection_init", "payload": init_payload}))
            print(f"  Sent connection_init")

            for _ in range(10):
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    print(f"  << {raw}")
                    msg = json.loads(raw)
                    if msg.get("type") == "connection_ack":
                        print("  >>> connection_ack received! Sending subscribe...")
                        return True   # success
                except asyncio.TimeoutError:
                    print("  (5 s timeout, no message)")
                    break

    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
    return False


async def main():
    if not TIBBER_TOKEN:
        print("Error: TIBBER_TOKEN is not set.")
        sys.exit(1)

    print(f"Token: {TIBBER_TOKEN[:6]}...{TIBBER_TOKEN[-4:]}")

    print("\nFetching home ID via HTTP...")
    try:
        home_id = get_home_id()
        print(f"Home ID: {home_id}")
    except Exception as e:
        print(f"HTTP error: {e}")
        sys.exit(1)

    # Try all combinations
    combos = [
        ("graphql-ws",            {"token": TIBBER_TOKEN}),
        ("graphql-ws",            {"Authorization": f"Bearer {TIBBER_TOKEN}"}),
        ("graphql-transport-ws",  {"token": TIBBER_TOKEN}),
        ("graphql-transport-ws",  {}),
        (None,                    {"token": TIBBER_TOKEN}),
    ]
    for subproto, payload in combos:
        ok = await test_connection(subproto, payload)
        if ok:
            print(f"\n>>> WORKING COMBO: subprotocol={subproto!r}, payload={payload}")
            break
    else:
        print("\nNone of the combinations got a connection_ack.")

asyncio.run(main())
