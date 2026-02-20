# Status Page Tracker

Event-driven service status tracker built with **FastAPI**, **asyncio**, and **httpx**.

## Quick Start

```bash
# Install dependencies
pip install -e .

# Run the application
python -m app.main
```

Open http://localhost:8000 for the dashboard.

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Health dashboard |
| `GET /status` | Current status summary (JSON) |
| `GET /incidents` | Active incidents (JSON) |
| `GET /events` | SSE real-time stream |
| `GET /docs` | OpenAPI docs |

## SSE Stream

```bash
curl -N http://localhost:8000/events
```

```javascript
const es = new EventSource("http://localhost:8000/events");
es.onmessage = (e) => console.log(JSON.parse(e.data));
```

## Docker

```bash
docker compose up --build
```

## Adding Providers

Edit `app/config.py`:

```python
DEFAULT_PROVIDERS = [
    ProviderConfig("OpenAI", "https://status.openai.com/api/v2"),
    ProviderConfig("GitHub", "https://www.githubstatus.com/api/v2"),
]
```

## Architecture

- **Conditional HTTP** — ETag/Last-Modified + content hashing for minimal bandwidth
- **Async Event Bus** — in-process pub/sub with per-subscriber backpressure queues
- **asyncio Tasks** — one coroutine per provider, scales to 100+ with zero threads
