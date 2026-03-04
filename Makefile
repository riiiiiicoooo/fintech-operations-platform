# Makefile: Fintech Operations Platform

.PHONY: help install test lint format clean run demo db-setup

help:
	@echo "Fintech Operations Platform - Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install          Install dependencies"
	@echo "  make db-setup         Initialize PostgreSQL schema"
	@echo ""
	@echo "Development:"
	@echo "  make run              Run FastAPI server (localhost:8000)"
	@echo "  make demo             Run transaction lifecycle demo"
	@echo ""
	@echo "Testing & Quality:"
	@echo "  make test             Run pytest suite"
	@echo "  make test-cov         Run tests with coverage report"
	@echo "  make lint             Run flake8 linter"
	@echo "  make format           Auto-format with black and isort"
	@echo "  make type-check       Run mypy type checker"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean            Remove __pycache__, .pytest_cache, etc."

install:
	pip install -r requirements.txt

db-setup:
	@echo "Setting up PostgreSQL schema..."
	@echo "Run manually: psql -U fintech -d fintech_ops < schema/schema.sql"

run:
	@echo "Starting FastAPI server at http://localhost:8000"
	@echo "API docs available at http://localhost:8000/docs"
	uvicorn api.app:app --reload --host 0.0.0.0 --port 8000

demo:
	@echo "Running transaction lifecycle demo..."
	python -m demo.transaction_lifecycle

test:
	pytest tests/ -v

test-cov:
	pytest tests/ -v --cov=src --cov-report=html --cov-report=term-missing

lint:
	flake8 src/ api/ tests/ demo/ --max-line-length=100

format:
	black src/ api/ tests/ demo/ --line-length=100
	isort src/ api/ tests/ demo/

type-check:
	mypy src/ api/ --ignore-missing-imports

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .coverage -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -f .coverage

# Docker targets
.PHONY: docker-build docker-up docker-down docker-test

docker-build:
	docker-compose build

docker-up:
	docker-compose up -d
	@echo "Services running:"
	@echo "  API: http://localhost:8000"
	@echo "  PostgreSQL: localhost:5432"
	@echo "  Redis: localhost:6379"

docker-down:
	docker-compose down

docker-test:
	docker-compose run --rm api pytest tests/ -v

# CI targets
.PHONY: ci-test ci-lint ci-all

ci-test:
	pytest tests/ -v --tb=short

ci-lint:
	flake8 src/ api/ tests/ demo/ --count --select=E9,F63,F7,F82 --show-source --statistics

ci-all: ci-lint ci-test
	@echo "All CI checks passed ✓"
