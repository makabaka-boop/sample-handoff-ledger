.PHONY: up down logs migrate reset test test-backend test-frontend e2e

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api frontend db

migrate:
	docker compose run --rm --entrypoint alembic api upgrade head

reset:
	docker compose down --volumes

test: test-backend test-frontend

test-backend:
	docker build -t sample-handoff-api-test backend
	docker run --rm --user 0 --entrypoint sh -v "$(CURDIR)/backend:/src" -w /src -e DATABASE_URL=sqlite+pysqlite:////tmp/test.db -e HANDOFF_SIGNING_KEY=test-signing-key-that-is-longer-than-thirty-two-bytes sample-handoff-api-test -c "pip install --no-cache-dir '.[dev]' && pytest && ruff check app tests migrations"

test-frontend:
	docker run --rm -v "$(CURDIR)/frontend:/app" -w /app node:22-alpine sh -c "npm install && npm test && npm run build"

e2e:
	cd frontend && E2E_BASE_URL=http://localhost:4173 npm run e2e
