# Tibber Energy Monitor

Terminal dashboard for real-time Tibber energy data:
- Current 15-minute electricity price (auto-refreshed every 15 min)
- Live power consumption and production (WebSocket stream)
- Accumulated today / last hour consumption & production
- Total meter readings (import + export)

## Setup

```bash
pip install -r requirements.txt
```

## Run

Set your Tibber API token (get it from https://developer.tibber.com):

```bash
# Linux / macOS
export TIBBER_TOKEN=your_token_here
python tibber_monitor.py

# Windows CMD
set TIBBER_TOKEN=your_token_here
python tibber_monitor.py

# Windows PowerShell
$env:TIBBER_TOKEN="your_token_here"
python tibber_monitor.py
```

Press **Ctrl+C** to quit.
