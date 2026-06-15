.PHONY: all format lint typecheck check fix test test-coverage test-verbose clean clean-pyc clean-all install install-dev config help validate venv run run-help code-quality deps-check deps-update

# Virtual environment configuration
VENV_DIR = venv
VENV_PYTHON = $(VENV_DIR)/bin/python
VENV_PIP = $(VENV_DIR)/bin/pip

# Project configuration
LINE_LENGTH = 120
SOURCE_DIR = src/openaudible_to_audiobookshelf
TESTS_DIR = tests
MAIN_SCRIPT = src/openaudible_to_audiobookshelf/pipeline.py

# Always use venv python for development commands
BLACK_CMD = $(VENV_PYTHON) -m black --line-length=$(LINE_LENGTH)
ISORT_CMD = $(VENV_PYTHON) -m isort --line-length=$(LINE_LENGTH)
FLAKE8_CMD = $(VENV_PYTHON) -m flake8 --max-line-length=$(LINE_LENGTH)
MYPY_CMD = $(VENV_PYTHON) -m mypy
PYTEST_CMD = $(VENV_PYTHON) -m pytest

# Use venv python if it exists, otherwise system python
PYTHON = $(shell if [ -f "$(VENV_PYTHON)" ]; then echo "$(VENV_PYTHON)"; else echo "python3"; fi)

# Default target
all: format lint typecheck test

# Format code with black and isort
format: install-dev
	@echo "📝 Formatting Python code with black and isort ($(LINE_LENGTH) char line length)..."
	$(BLACK_CMD) $(SOURCE_DIR) $(TESTS_DIR) *.py
	$(ISORT_CMD) $(SOURCE_DIR) $(TESTS_DIR) *.py
	@echo "✅ Code formatting completed"

# Lint code with flake8  
lint: install-dev
	@echo "🔍 Linting Python code with flake8 ($(LINE_LENGTH) char line length)..."
	$(FLAKE8_CMD) $(SOURCE_DIR) $(TESTS_DIR) *.py
	@echo "✅ Code linting completed"

# Check code formatting without changes
check: install-dev
	@echo "🔎 Checking code formatting with black and isort ($(LINE_LENGTH) char line length)..."
	$(BLACK_CMD) --check $(SOURCE_DIR) $(TESTS_DIR) *.py
	$(ISORT_CMD) --check-only $(SOURCE_DIR) $(TESTS_DIR) *.py
	@echo "✅ Code formatting check passed"

# Type check with mypy
typecheck: install-dev
	@echo "🔧 Type checking Python code with mypy..."
	$(MYPY_CMD) $(SOURCE_DIR) *.py
	@echo "✅ Type checking completed"

# Run formatter, linter, and type checker
fix: install-dev
	@echo "🔧 AUTO-FIXING CODE ISSUES..."
	@echo "================================="
	@echo "1️⃣ Fixing code formatting (black)..."
	$(BLACK_CMD) $(SOURCE_DIR) $(TESTS_DIR) *.py
	@echo "2️⃣ Fixing import order (isort)..."
	$(ISORT_CMD) $(SOURCE_DIR) $(TESTS_DIR) *.py
	@echo ""
	@echo "🔍 CHECKING FOR MANUAL FIXES NEEDED..."
	@echo "======================================"
	@echo "3️⃣ Checking linting issues (flake8)..."
	-$(FLAKE8_CMD) $(SOURCE_DIR) $(TESTS_DIR) *.py || echo "❌ Linting errors found - need manual fixes"
	@echo "4️⃣ Running type checking (mypy)..."
	-$(MYPY_CMD) $(SOURCE_DIR) *.py || echo "❌ Type checking errors found - need manual fixes"
	@echo "✅ AUTO-FIXES APPLIED ✅"
	@echo "📋 Check output above for any manual fixes needed"

# Run tests
test: install-dev
	@echo "🧪 Running tests with pytest..."
	$(PYTEST_CMD) $(TESTS_DIR)/ -v
	@echo "✅ All tests completed"

# Run tests with verbose output
test-verbose: install-dev
	@echo "🧪 Running all tests (verbose)..."
	$(PYTEST_CMD) $(TESTS_DIR)/ -vv
	@echo "✅ All tests completed"

# Run tests with coverage reporting
test-coverage: install-dev
	@echo "🧪 Running tests with coverage reporting..."
	$(PYTEST_CMD) $(TESTS_DIR)/ -v --cov=$(SOURCE_DIR) --cov-report=term-missing --cov-report=html --cov-report=xml
	@echo "📁 Coverage report: htmlcov/index.html"
	@echo "✅ Tests with coverage completed"

# Validate project structure and imports
validate: venv
	@echo "🔍 Validating project structure and imports..."
	@if [ -d "$(SOURCE_DIR)" ]; then \
		echo "✅ Source directory exists: $(SOURCE_DIR)"; \
	else \
		echo "❌ Source directory missing: $(SOURCE_DIR)"; \
		exit 1; \
	fi
	@if [ -d "$(TESTS_DIR)" ]; then \
		echo "✅ Tests directory exists: $(TESTS_DIR)"; \
	else \
		echo "❌ Tests directory missing: $(TESTS_DIR)"; \
		exit 1; \
	fi
	@if [ -f "requirements.txt" ]; then \
		echo "✅ Requirements file exists: requirements.txt"; \
	else \
		echo "❌ Requirements file missing: requirements.txt"; \
		exit 1; \
	fi
	@if [ -f "pyproject.toml" ]; then \
		echo "✅ Project config exists: pyproject.toml"; \
	else \
		echo "❌ Project config missing: pyproject.toml"; \
		exit 1; \
	fi
	@$(PYTHON) -m py_compile $(SOURCE_DIR)/config.py
	@echo "✅ Project validation completed"

# Clean temporary files (preserves virtual environment)
clean:
	@echo "🧹 Cleaning temporary files..."
	@find . -name "*.pyc" -delete
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@rm -rf htmlcov/ .coverage .pytest_cache/ .mypy_cache/
	@rm -rf openaudible_to_audiobookshelf.egg-info/ build/ dist/
	@echo "✅ Cleanup completed (virtual environment preserved)"

# Clean Python compiled files only
clean-pyc:
	@echo "🧹 Cleaning Python compiled files..."
	@find . -name "*.pyc" -delete
	@find . -name "*.pyo" -delete
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "✅ Python compiled files cleaned"

# Clean everything including virtual environment
clean-all:
	@echo "🧹 Cleaning all temporary files and virtual environment..."
	@find . -name "*.pyc" -delete
	@find . -name "*.pyo" -delete
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@rm -rf htmlcov/ .coverage .pytest_cache/ .mypy_cache/
	@rm -rf openaudible_to_audiobookshelf.egg-info/ build/ dist/
	@rm -rf $(VENV_DIR)
	@echo "✅ Complete cleanup finished (including virtual environment)"

# Create virtual environment (idempotent - only creates if doesn't exist)
venv:
	@if [ ! -d "$(VENV_DIR)" ]; then \
		echo "📦 Creating virtual environment..."; \
		python3 -m venv $(VENV_DIR); \
		echo "📥 Upgrading pip..."; \
		$(VENV_PIP) install --upgrade pip; \
		echo "✅ Virtual environment created (run 'make install-dev' to install dependencies)"; \
	else \
		echo "✅ Virtual environment already exists"; \
	fi

# Install development dependencies  
install-dev: venv
	@echo "📥 Installing production dependencies..."
	$(VENV_PIP) install -r requirements.txt
	@echo "📥 Installing development dependencies..."
	$(VENV_PIP) install -r $(TESTS_DIR)/requirements.txt
	@echo "📥 Installing project in editable mode..."
	$(VENV_PIP) install -e .
	@echo "✅ Development dependencies installed"

# Install production dependencies only
install: venv
	@echo "📥 Installing production dependencies..."
	$(VENV_PIP) install -r requirements.txt
	@echo "📥 Installing project in editable mode..."
	$(VENV_PIP) install -e .
	@echo "✅ Production dependencies installed"

# Running the application
run: venv
	@echo "🚀 Running OpenAudible-To-AudioBookShelf..."
	$(VENV_PYTHON) $(MAIN_SCRIPT)

run-help: venv
	@echo "📖 Showing command-line options..."
	$(VENV_PYTHON) $(MAIN_SCRIPT) --help

# Code quality alias
code-quality: lint
	@echo "✅ All code quality checks completed"

# Dependency management
deps-check: venv
	@echo "🔍 Checking for outdated dependencies..."
	$(VENV_PIP) list --outdated

deps-update: venv
	@echo "⬆️  Updating dependencies..."
	@echo "⚠️  This will update packages interactively"
	$(VENV_PIP) install --upgrade pip
	$(VENV_PIP) list --outdated --format=json | $(VENV_PYTHON) -c "import json, sys; packages = json.load(sys.stdin); [print(p['name']) for p in packages]" | xargs -n1 $(VENV_PIP) install -U

# Show current configuration
config:
	@echo "📋 Current configuration:"
	@echo "  Python: $$($(PYTHON) --version)"
	@echo "  Virtual environment: $$(if [ -d "$(VENV_DIR)" ]; then echo 'Active ($(VENV_DIR))'; else echo 'Not created'; fi)"
	@echo "  Black: $$($(VENV_PYTHON) -m black --version 2>/dev/null || echo 'Not installed (run make install-dev)')"
	@echo "  Isort: $$($(VENV_PYTHON) -m isort --version 2>/dev/null || echo 'Not installed (run make install-dev)')"
	@echo "  Flake8: $$($(VENV_PYTHON) -m flake8 --version 2>/dev/null | head -1 || echo 'Not installed (run make install-dev)')"
	@echo "  Mypy: $$($(VENV_PYTHON) -m mypy --version 2>/dev/null || echo 'Not installed (run make install-dev)')"
	@echo "  Pytest: $$($(VENV_PYTHON) -m pytest --version 2>/dev/null | head -1 || echo 'Not installed (run make install-dev)')"
	@echo "  Line length: $(LINE_LENGTH)"
	@echo "  Source package: $(SOURCE_DIR)"
	@echo "  Tests directory: $(TESTS_DIR)"
	@echo "  Main script (CLI entry): $(MAIN_SCRIPT)"

# Show help
help:
	@echo "📚 OpenAudible-To-AudioBookShelf"
	@echo "================================="
	@echo ""
	@echo "Available targets:"
	@echo ""
	@echo "🏗️  Setup & Installation:"
	@echo "  install       - Install production dependencies"
	@echo "  install-dev  - Install development environment"
	@echo "  venv         - Create virtual environment"
	@echo "  clean        - Remove temporary files (preserves venv)"
	@echo "  clean-pyc    - Remove only Python compiled files"
	@echo "  clean-all    - Remove everything including virtual environment"
	@echo ""
	@echo "🚀 Running:"
	@echo "  run          - Run the pipeline (openaudible-to-abs or python -m openaudible_to_audiobookshelf)"
	@echo "  run-help     - Show command-line options for main script"
	@echo ""
	@echo "🧪 Testing:"
	@echo "  test         - Run all tests"
	@echo "  test-verbose - Run tests with verbose output"
	@echo "  test-coverage - Run tests with coverage report"
	@echo ""
	@echo "🔧 Code Quality:"
	@echo "  all          - Run format, lint, typecheck, and test"
	@echo "  fix          - Auto-fix issues + check for manual fixes (⭐ RECOMMENDED)"
	@echo "  format       - Auto-fix formatting only (black + isort)"
	@echo "  lint         - Run all linting checks (black, isort, flake8)"
	@echo "  check        - Check formatting without changes"
	@echo "  typecheck    - Run type checking only (mypy)"
	@echo "  code-quality - Run all quality checks (lint)"
	@echo ""
	@echo "📦 Project Management:"
	@echo "  validate     - Validate project structure and imports"
	@echo "  config       - Show current configuration"
	@echo "  deps-check   - Check for outdated dependencies"
	@echo "  deps-update  - Update dependencies (interactive)"
	@echo "  help         - Show this help message"
