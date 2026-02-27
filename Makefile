.PHONY: help install lint format test test-cov dev dev-build down clean logs

# Default target
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ── Setup ───────────────────────────────────────────────────────────────────

install: ## Install all dependencies (including dev)
	uv sync

# ── Code Quality ────────────────────────────────────────────────────────────

lint: ## Run ruff linter + format check
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-format code with ruff
	uv run ruff format .
	uv run ruff check --fix .

cache-clean:
	uv cache clean langcore
# ── Testing ─────────────────────────────────────────────────────────────────

test: ## Run tests
	uv run pytest -v

test-cov: ## Run tests with coverage report
	uv run pytest --cov=app --cov-report=term-missing --cov-report=html -v

# ── Docker ──────────────────────────────────────────────────────────────────

dev: ## Start all dev services (API + Worker + Redis + Flower)
	docker compose up --build

dev-build: ## Rebuild dev images without cache
	docker compose build --no-cache

down: ## Stop all services
	docker compose down

clean: ## Stop all services and remove volumes
	docker compose down -v

logs: ## Tail logs from all services
	docker compose logs -f

# ── Production ──────────────────────────────────────────────────────────────

prod: ## Start production services
	docker compose --profile production up --build -d

prod-down: ## Stop production services
	docker compose --profile production down
