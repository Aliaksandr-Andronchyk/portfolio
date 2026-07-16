# Sasha Andronchyk — Python Backend Developer (FastAPI)

I build backend services end-to-end: API design → async data layer → tests → Docker → CI. I come from shipping full products solo (iOS + backend + infra), so I own features across the whole stack and deploy what I write.

## Demo projects (code on GitHub, green CI)

- **[lesson-booking-api](https://github.com/SashaAndronchyk/lesson-booking-api)** — booking REST API for tutors: FastAPI, async SQLAlchemy 2.0, PostgreSQL (asyncpg), Alembic migrations, business rules (time-slot overlap detection, status lifecycle), limit/offset pagination and filters. pytest suite runs against both in-memory SQLite and real PostgreSQL 16 in GitHub Actions; Docker Compose one-command start.
- **[faststream-notify](https://github.com/SashaAndronchyk/faststream-notify)** — event-driven notification microservice: FastStream + RabbitMQ, pydantic v2 validation, pure formatting layer, handlers integration-tested with in-memory TestRabbitBroker.

## Production experience (own projects)

- **Trading backends.** Real-exchange integration over REST + WebSocket (HMAC-signed orders), live tick feeds, paper-trading engine, signal pipeline (Python + Swift/Vapor). Deployed and operated on Linux VPS.
- **JourCheff engine.** Node.js service that turns a dictated dish into a committed supermarket cart via deep store-API integration: session/CSRF management, proactive re-login, request batching for sub-90s end-to-end flow.
- **Infra/DevOps.** GCP VMs, nginx, systemd/launchd services, Telegram bots, headless CI pipelines (build → sign → upload) I built and run daily.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · PostgreSQL · Alembic · pydantic v2 · FastStream / RabbitMQ · pytest · Docker & Docker Compose · GitHub Actions · Linux · Git · Node.js

## Contact

GitHub [@SashaAndronchyk](https://github.com/SashaAndronchyk) · Andronchyki@icloud.com
