# NapsterTec AI Gateway v2.0

An enterprise-grade, OpenAI-compatible AI Gateway designed as the central backend routing layer for NapsterTec AI. This system standardizes interactions across multiple AI models and providers, featuring automatic failover, global thread pooling, strict payload validation, and extensive administrative metrics.

## Key Features

*   **100% OpenAI API Compatibility:** Drop-in replacement for OpenAI endpoints (`/v1/chat/completions`, `/v1/models`).
*   **Automatic Provider Failover:** Intelligently routes failed or rate-limited requests to healthy fallback providers.
*   **Logical Model Mapping:** Maps frontend aliases (e.g., `coding-agent`, `gemini-3.5-flash`) to specific backend provider models.
*   **Enterprise Resource Management:** Utilizes a global shared `ThreadPoolExecutor` for non-blocking synchronous provider operations.
*   **Strict Pydantic Validation:** Rejects malformed requests and controls context size limits before they reach the provider layer.
*   **Admin Metrics & Telemetry:** Exposes comprehensive health, latency, and request metrics for dashboard monitoring.

## Directory Structure

```text
napstertec-ai-gateway/
├── middleware/          # Auth, CORS, and Rotating Logging
├── providers/           # Base contracts, Ollama integration, Registry, and Failover Manager
├── utils/               # Config, Health tracking, Pydantic schemas, and Error responses
├── ollamafreeapi/       # Internal synchronous provider package (Do Not Modify)
├── .env                 # Environment configuration
├── pyproject.toml       # Project metadata and dependencies
└── server.py            # FastAPI entry point and route definitions