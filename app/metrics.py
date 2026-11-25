"""Prometheus metrics. NudgeBee (and any Prometheus stack) scrapes /metrics."""
from prometheus_client import Counter, Gauge, Histogram

SARVAM_LATENCY = Histogram(
    "milan_sarvam_request_seconds",
    "Latency of Sarvam API calls",
    ["api"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 20),
)
SARVAM_ERRORS = Counter("milan_sarvam_errors_total", "Failed Sarvam API calls", ["api"])
SARVAM_QUEUE = Gauge("milan_sarvam_inflight", "Sarvam calls waiting or in flight on this pod")

PIPELINE_LATENCY = Histogram(
    "milan_utterance_seconds",
    "End-to-end time from utterance received to all translations published",
    buckets=(0.25, 0.5, 1, 2, 3, 5, 8, 13, 20, 30),
)
UTTERANCES = Counter("milan_utterances_total", "Utterances processed", ["kind"])
WS_CONNECTIONS = Gauge("milan_ws_connections", "Open listener WebSocket connections on this pod")
DEGRADED = Gauge("milan_rush_mode", "1 when rush mode (captions only) is active")
