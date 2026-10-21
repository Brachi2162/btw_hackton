.PHONY: up down logs dev test lint

up:        ## build and start everything (http://localhost:8000/health)
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api

dev:       ## run the API locally with reload (needs: pip install -r backend/requirements-dev.txt)
	cd backend && DATA_DIR=../data uvicorn vera.api.main:app --reload --port 8000

test:
	cd backend && python -m pytest -q

lint:
	cd backend && black --check . && flake8 .
