#!/usr/bin/env python3
"""
Tibber Energy Monitor
Displays real-time energy prices (15-min resolution), live power consumption/production,
and meter readings in a continuously refreshed terminal dashboard.

Usage:
    export TIBBER_TOKEN=your_token_here
    python tibber_monitor.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime

import websockets
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TIBBER_TOKEN = os.getenv("TIBBER_TOKEN", "")
API_URL = "https://api.tibber.com/v1-beta/gql"
WS_URL = "wss://api.tibber.com/v1-beta/gql/subscriptions"
HEADERS = {"Authorization": f"Bearer {TIBBER_TOKEN}"}
PRICE_REFRESH_INTERVAL = 15 * 60  # 15 minutes in seconds

# ---------------------------------------------------------------------------
# Shared state (written by async tasks, read by display builder)
# ---------------------------------------------------------------------------
state: dict = {
    "price": None,
    "live": None,
    "last_price_update": None,
    "last_live_update": None,
    "live_status": "Connecting...",
    "price_status": "Loading...",
}

console = Console()


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
def _fmt(value, unit: str = "", decimals: int = 3, scale: float = 1.0) -> str:
    """Format a numeric value or return 'N/A'."""
    if value is None:
        return "[dim]N/A[/dim]"
    return f"{value * scale:,.{decimals}f} {unit}".strip()


def build_display() -> Table:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    root = Table.grid(padding=(0, 1))
    root.add_column()

    # ── Header ──────────────────────────────────────────────────────────────
    root.add_row(Panel(
        f"[bold cyan]TIBBER ENERGY MONITOR[/bold cyan]   [dim]{now}[/dim]",
        border_style="cyan",
    ))

    # ── Price (top row) ─────────────────────────────────────────────────────
    price_data = state.get("price")
    if price_data:
        p = price_data.get("current") or {}
        currency = p.get("currency", "")
        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_column("k", style="dim", min_width=22)
        t.add_column("v", style="bold green")
        t.add_row("Total", f"{p.get('total', 'N/A')} {currency}/kWh")
        t.add_row("Energy component", f"{p.get('energy', 'N/A')} {currency}/kWh")
        t.add_row("Tax",  f"{p.get('tax', 'N/A')} {currency}/kWh")
        t.add_row("Price level", f"[yellow]{p.get('level', 'N/A')}[/yellow]")
        t.add_row("Valid from", str(p.get("startsAt", "N/A")))
        upd = state.get("last_price_update")
        t.add_row("Last refreshed", upd.strftime("%H:%M:%S") if upd else "—")
        root.add_row(Panel(t, title="[bold]Current 15-min Price[/bold]", border_style="green"))
    else:
        root.add_row(Panel(
            f"[dim]{state['price_status']}[/dim]",
            title="Current 15-min Price",
            border_style="green",
        ))

    # ── Live measurements ────────────────────────────────────────────────────
    live = state.get("live")
    if live:
        t2 = Table(show_header=False, box=None, padding=(0, 2))
        t2.add_column("k", style="dim", min_width=32)
        t2.add_column("v", style="bold yellow")

        # Instant power
        t2.add_row("[bold]Instant Power[/bold]", "")
        t2.add_row("  Current", _fmt(live.get("power"), "W", 0))
        t2.add_row("  Min today", _fmt(live.get("minPower"), "W", 0))
        t2.add_row("  Average today", _fmt(live.get("averagePower"), "W", 0))
        t2.add_row("  Max today", _fmt(live.get("maxPower"), "W", 0))

        # Accumulated today
        currency = live.get("currency", "")
        t2.add_row("", "")
        t2.add_row("[bold]Accumulated Today[/bold]", "")
        t2.add_row("  Consumption", _fmt(live.get("accumulatedConsumption"), "kWh"))
        t2.add_row("  Cost", _fmt(live.get("accumulatedCost"), currency, 4))

        upd = state.get("last_live_update")
        t2.add_row("", "")
        t2.add_row("Timestamp", str(live.get("timestamp", "N/A")))
        t2.add_row("Last received", upd.strftime("%H:%M:%S") if upd else "—")

        root.add_row(Panel(t2, title="[bold]Live Measurements[/bold]", border_style="yellow"))
    else:
        root.add_row(Panel(
            f"[dim]{state['live_status']}[/dim]",
            title="Live Measurements",
            border_style="yellow",
        ))

    root.add_row("[dim]  Press Ctrl+C to quit[/dim]")
    return root


# ---------------------------------------------------------------------------
# GraphQL queries / subscription
# ---------------------------------------------------------------------------
PRICE_QUERY = gql("""
{
  viewer {
    homes {
      currentSubscription {
        priceInfo {
          current {
            startsAt
            level
            total
            energy
            tax
            currency
          }
        }
      }
    }
  }
}
""")

HOME_ID_QUERY = gql("{ viewer { homes { id } } }")

LIVE_SUBSCRIPTION_QUERY = """
subscription {
  liveMeasurement(homeId: "%s") {
    timestamp
    power
    accumulatedConsumption
    accumulatedCost
    currency
    minPower
    averagePower
    maxPower
  }
}
"""


# ---------------------------------------------------------------------------
# Async tasks
# ---------------------------------------------------------------------------
async def get_home_id() -> str:
    transport = AIOHTTPTransport(url=API_URL, headers=HEADERS)
    async with Client(transport=transport, fetch_schema_from_transport=False) as session:
        result = await session.execute(HOME_ID_QUERY)
    homes = result["viewer"]["homes"]
    if not homes:
        raise RuntimeError("No homes found in your Tibber account.")
    return homes[0]["id"]


async def price_task(live_display: Live) -> None:
    """Fetch current price once, then refresh every 15 minutes."""
    while True:
        try:
            state["price_status"] = "Fetching..."
            transport = AIOHTTPTransport(url=API_URL, headers=HEADERS)
            async with Client(transport=transport, fetch_schema_from_transport=False) as session:
                result = await session.execute(PRICE_QUERY)
            homes = result["viewer"]["homes"]
            if homes and homes[0].get("currentSubscription"):
                state["price"] = homes[0]["currentSubscription"]["priceInfo"]
                state["last_price_update"] = datetime.now()
                state["price_status"] = "OK"
        except Exception as exc:
            state["price_status"] = f"Error: {exc}"
        live_display.update(build_display())
        await asyncio.sleep(PRICE_REFRESH_INTERVAL)


async def live_task(home_id: str, live_display: Live) -> None:
    """Subscribe to live measurements via raw WebSocket with full ping/pong handling."""
    query = LIVE_SUBSCRIPTION_QUERY % home_id

    while True:
        state["live_status"] = "Connecting to live feed..."
        live_display.update(build_display())
        try:
            # Tibber uses the older Apollo graphql-ws protocol (not graphql-transport-ws)
            async with websockets.connect(
                WS_URL,
                subprotocols=["graphql-ws"],
                ping_interval=None,   # app-layer keep-alive ("ka") handles this
            ) as ws:
                # 1. connection_init — token in payload (no "Bearer " prefix)
                await ws.send(json.dumps({
                    "type": "connection_init",
                    "payload": {"token": TIBBER_TOKEN},
                }))

                # 2. Wait for connection_ack (ka messages may arrive first)
                try:
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        msg = json.loads(raw)
                        if msg["type"] == "connection_ack":
                            break
                        if msg["type"] == "ka":
                            pass  # keep-alive, no reply needed in graphql-ws
                        if msg["type"] == "connection_error":
                            raise RuntimeError(f"Auth error: {msg.get('payload')}")
                except asyncio.TimeoutError:
                    raise RuntimeError("Timed out waiting for connection_ack — check token")

                # 3. Start subscription (old protocol uses "start", not "subscribe")
                await ws.send(json.dumps({
                    "id": "1",
                    "type": "start",
                    "payload": {"query": query},
                }))

                state["live_status"] = "Connected"
                live_display.update(build_display())

                # 4. Receive messages indefinitely
                async for raw in ws:
                    msg = json.loads(raw)
                    t = msg.get("type")

                    if t == "data":   # old protocol calls it "data", not "next"
                        live_data = (
                            msg.get("payload", {})
                               .get("data", {})
                               .get("liveMeasurement") or {}
                        )
                        state["live"] = live_data
                        state["last_live_update"] = datetime.now()
                        live_display.update(build_display())

                    elif t == "ka":
                        pass  # keep-alive heartbeat — no reply needed

                    elif t == "error":
                        raise RuntimeError(f"Subscription error: {msg.get('payload')}")

                    elif t == "complete":
                        state["live_status"] = "Subscription ended by server"
                        break

        except Exception as exc:
            state["live"] = None
            state["live_status"] = f"Disconnected ({exc}) — retrying in 10 s..."
            live_display.update(build_display())
            await asyncio.sleep(10)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    if not TIBBER_TOKEN:
        console.print("[red bold]Error:[/red bold] TIBBER_TOKEN environment variable is not set.")
        console.print("  Linux/macOS : [cyan]export TIBBER_TOKEN=your_token_here[/cyan]")
        console.print("  Windows CMD : [cyan]set TIBBER_TOKEN=your_token_here[/cyan]")
        console.print("  Windows PS  : [cyan]$env:TIBBER_TOKEN=\"your_token_here\"[/cyan]")
        sys.exit(1)

    console.print("Connecting to Tibber API…")
    home_id = await get_home_id()
    console.print(f"Home ID: [cyan]{home_id}[/cyan]")

    with Live(build_display(), console=console, refresh_per_second=2, screen=True) as live:
        await asyncio.gather(
            price_task(live),
            live_task(home_id, live),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[dim]Tibber Monitor stopped.[/dim]")
