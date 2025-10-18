# OpenAudible-To-AudioBookShelf Makefile
# Process and manage audiobook data from OpenAudible/Libation to AudioBookShelf

.PHONY: help install install-dev test test-coverage test-verbose lint fix format type-check
.PHONY: clean run run-help code-quality deps-check deps-update

# Variables
PYTHON := venv/bin/python
PIP := venv/bin/pip
PYTHON_VERSION := python3.13
MAIN_SCRIPT := openaudible_to_ab.py
LINE_LENGTH := 120

# Default target
help:
	@echo "📚 OpenAudible-To-AudioBookShelf"
	@echo "================================="
	@echo ""
	@echo "Available targets:"
	@echo ""
	@echo "🏗️  Setup & Installation:"
	@echo "  install       - Install production dependencies"
	@echo "  install-dev   - Install development environment"
	@echo "  clean         - Clean build artifacts and cache"
	@echo ""
	@echo "🚀 Running:"
	@echo "  run           - Run the main script (openaudible_to_ab.py)"
	@echo "  run-help      - Show command-line options for main script"
	@echo ""
	@echo "🧪 Testing:"
	@echo "  test          - Run all tests"
	@echo "  test-coverage - Run tests with coverage report"
	@echo "  test-verbose  - Run tests with verbose output"
	@echo ""
	@echo "🔧 Code Quality:"
	@echo "  fix           - Auto-fix issues + check for manual fixes (⭐ RECOMMENDED)"
	@echo "  lint          - Run all linting checks (black, isort, flake8)"
	@echo "  format        - Auto-fix formatting only (black + isort)"
	@echo "  type-check    - Run type checking only (mypy)"
	@echo "  code-quality  - Run all quality checks (lint)"
	@echo ""
	@echo "📦 Project Management:"
	@echo "  deps-check    - Check for outdated dependencies"
	@echo "  deps-update   - Update dependencies (interactive)"

# Setup virtual environment
venv:
	@echo "🐍 Setting up Python virtual environment..."
	$(PYTHON_VERSION) -m venv venv
	$(PIP) install --upgrade pip
	@echo "✅ Virtual environment created"

# Install production dependencies
install: venv
	@echo "📦 Installing production dependencies..."
	$(PIP) install -r requirements.txt
	$(PIP) install -e .
	@echo "✅ Production installation complete"

# Install development environment
install-dev: venv
	@echo "🛠️  Installing development environment..."
	$(PIP) install -r requirements.txt
	$(PIP) install -r tests/requirements.txt
	@echo "✅ Development environment ready"

# Testing targets
test: venv
	@echo "🧪 Running all tests..."
	$(PYTHON) -m pytest tests/ -v --tb=short

test-verbose: venv
	@echo "🧪 Running all tests (verbose)..."
	$(PYTHON) -m pytest tests/ -vv

test-coverage: venv
	@echo "📊 Running tests with coverage..."
	$(PYTHON) -m pytest tests/ --cov=modules --cov-report=html --cov-report=term --cov-report=xml
	@echo "📁 Coverage report: htmlcov/index.html"

# Code quality targets
lint: venv
	@echo "🔍 Running linting checks..."
	$(PYTHON) -m black --check --line-length=$(LINE_LENGTH) modules/ tests/ *.py
	$(PYTHON) -m isort --check-only --line-length=$(LINE_LENGTH) modules/ tests/ *.py
	$(PYTHON) -m flake8 --max-line-length=$(LINE_LENGTH) modules/ tests/ *.py

fix: venv
	@echo "🔧 AUTO-FIXING CODE ISSUES..."
	@echo "================================="
	@echo "1️⃣ Fixing code formatting (black)..."
	$(PYTHON) -m black --line-length=$(LINE_LENGTH) modules/ tests/ *.py
	@echo "2️⃣ Fixing import order (isort)..."
	$(PYTHON) -m isort --line-length=$(LINE_LENGTH) modules/ tests/ *.py
	@echo ""
	@echo "🔍 CHECKING FOR MANUAL FIXES NEEDED..."
	@echo "======================================"
	@echo "3️⃣ Checking linting issues (flake8)..."
	-$(PYTHON) -m flake8 --max-line-length=$(LINE_LENGTH) modules/ tests/ *.py || echo "❌ Linting errors found - need manual fixes"
	@echo "✅ AUTO-FIXES APPLIED ✅"
	@echo "📋 Check output above for any manual fixes needed"

format: venv
	@echo "🎨 Formatting code (auto-fix only)..."
	$(PYTHON) -m black --line-length=$(LINE_LENGTH) modules/ tests/ *.py
	$(PYTHON) -m isort --line-length=$(LINE_LENGTH) modules/ tests/ *.py
	@echo "✅ Code formatting completed"

type-check: venv
	@echo "🔍 Running type checking..."
	$(PYTHON) -m mypy modules/ *.py
	@echo "✅ Type checking completed"

code-quality: lint
	@echo "✅ All code quality checks completed"

# Running the application
run: venv
	@echo "🚀 Running OpenAudible-To-AudioBookShelf..."
	$(PYTHON) $(MAIN_SCRIPT)

run-help: venv
	@echo "📖 Showing command-line options..."
	$(PYTHON) $(MAIN_SCRIPT) --help

# Dependency management
deps-check: venv
	@echo "🔍 Checking for outdated dependencies..."
	$(PIP) list --outdated

deps-update: venv
	@echo "⬆️  Updating dependencies..."
	@echo "⚠️  This will update packages interactively"
	$(PIP) install --upgrade pip
	$(PIP) list --outdated --format=json | $(PYTHON) -c "import json, sys; packages = json.load(sys.stdin); [print(p['name']) for p in packages]" | xargs -n1 $(PIP) install -U

# Cleanup
clean:
	@echo "🧹 Cleaning build artifacts and cache..."
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .mypy_cache/
	rm -rf venv/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	@echo "✅ Cleanup completed"