# Arizona Lead Tracker — common dev/ops commands.
# Recipes use the inline `target: ; cmd` form so the file works even when
# authored on Windows (no hard-tab requirement).

.PHONY: up down build logs ps migrate makemigration seed test cov fmt lint capture-fb shell-api shell-db

up: ; docker compose up -d --build
down: ; docker compose down
build: ; docker compose build
logs: ; docker compose logs -f
ps: ; docker compose ps

# --- Database ---
migrate: ; docker compose run --rm api alembic upgrade head
makemigration: ; docker compose run --rm api alembic revision --autogenerate -m "$(m)"
seed: ; docker compose run --rm api python -m scripts.seed

# --- Quality ---
test: ; docker compose run --rm api pytest -q
cov: ; docker compose run --rm api pytest --cov=app --cov-report=term-missing
fmt: ; docker compose run --rm api ruff format app tests
lint: ; docker compose run --rm api ruff check app tests

# --- Browser session capture (one-time assisted FB/X login) ---
capture-fb: ; docker compose run --rm -it worker-browser python -m scripts.capture_fb_session

# --- Shells ---
shell-api: ; docker compose exec api bash
shell-db: ; docker compose exec postgres psql -U $${POSTGRES_USER:-alt} -d $${POSTGRES_DB:-arizona_lead_tracker}
