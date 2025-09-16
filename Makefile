.PHONY: all format lint typecheck check fix test test-coverage clean clean-pyc clean-all install install-dev config help validate venv

# Virtual environment configuration
VENV_DIR = venv
VENV_PYTHON = $(VENV_DIR)/bin/python
VENV_PIP = $(VENV_DIR)/bin/pip

# Project configuration
LINE_LENGTH = 120
SOURCE_DIR = modules
TESTS_DIR = tests

# Always use venv python for development commands
BLACK_CMD = $(VENV_PYTHON) -m black --line-length=$(LINE_LENGTH)
FLAKE8_CMD = $(VENV_PYTHON) -m flake8 --max-line-length=$(LINE_LENGTH)
MYPY_CMD = $(VENV_PYTHON) -m mypy
PYTEST_CMD = $(VENV_PYTHON) -m pytest

# Use venv python if it exists, otherwise system python
PYTHON = $(shell if [ -f "$(VENV_PYTHON)" ]; then echo "$(VENV_PYTHON)"; else echo "python3"; fi)

# Default target
all: format lint typecheck test

# Format code with black
format: install-dev
	@echo "📝 Formatting Python code with black ($(LINE_LENGTH) char line length)..."
	$(BLACK_CMD) $(SOURCE_DIR) $(TESTS_DIR)
	@echo "✅ Code formatting completed"

# Lint code with flake8  
lint: install-dev
	@echo "🔍 Linting Python code with flake8 ($(LINE_LENGTH) char line length)..."
	$(FLAKE8_CMD) $(SOURCE_DIR) $(TESTS_DIR)
	@echo "✅ Code linting completed"

# Check code formatting without changes
check: install-dev
	@echo "🔎 Checking code formatting with black ($(LINE_LENGTH) char line length)..."
	$(BLACK_CMD) --check $(SOURCE_DIR) $(TESTS_DIR)
	@echo "✅ Code formatting check passed"

# Type check with mypy
typecheck: install-dev
	@echo "🔧 Type checking Python code with mypy..."
	$(MYPY_CMD) $(SOURCE_DIR)
	@echo "✅ Type checking completed"

# Run formatter, linter, and type checker
fix: format lint typecheck

# Run tests
test: install-dev
	@echo "🧪 Running tests with pytest..."
	$(PYTEST_CMD) $(TESTS_DIR)/ -v
	@echo "✅ All tests completed"

# Run tests with coverage reporting
test-coverage: install-dev
	@echo "🧪 Running tests with coverage reporting..."
	$(PYTEST_CMD) $(TESTS_DIR)/ -v --cov=$(SOURCE_DIR) --cov-report=term-missing --cov-report=html
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
	@rm -rf htmlcov/ .coverage .pytest_cache/
	@rm -rf modules.egg-info/
	@echo "✅ Cleanup completed (virtual environment preserved)"

# Clean Python compiled files only
clean-pyc:
	@echo "🧹 Cleaning Python compiled files..."
	@find . -name "*.pyc" -delete
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "✅ Python compiled files cleaned"

# Clean everything including virtual environment
clean-all:
	@echo "🧹 Cleaning all temporary files and virtual environment..."
	@find . -name "*.pyc" -delete
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@rm -rf htmlcov/ .coverage .pytest_cache/
	@rm -rf modules.egg-info/
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

# Show current configuration
config:
	@echo "📋 Current configuration:"
	@echo "  Python: $$($(PYTHON) --version)"
	@echo "  Virtual environment: $$(if [ -d "$(VENV_DIR)" ]; then echo 'Active ($(VENV_DIR))'; else echo 'Not created'; fi)"
	@echo "  Black: $$($(VENV_PYTHON) -m black --version 2>/dev/null || echo 'Not installed (run make install-dev)')"
	@echo "  Flake8: $$($(VENV_PYTHON) -m flake8 --version 2>/dev/null | head -1 || echo 'Not installed (run make install-dev)')"
	@echo "  Mypy: $$($(VENV_PYTHON) -m mypy --version 2>/dev/null || echo 'Not installed (run make install-dev)')"
	@echo "  Pytest: $$($(VENV_PYTHON) -m pytest --version 2>/dev/null | head -1 || echo 'Not installed (run make install-dev)')"
	@echo "  Line length: $(LINE_LENGTH)"
	@echo "  Source directory: $(SOURCE_DIR)"
	@echo "  Tests directory: $(TESTS_DIR)"

# Show help
help:
	@echo "📚 Available targets:"
	@echo "  all            - Run format, lint, typecheck, and test"
	@echo "  format         - Format code with black ($(LINE_LENGTH) chars)"
	@echo "  lint           - Lint code with flake8 ($(LINE_LENGTH) chars)"  
	@echo "  typecheck      - Type check code with mypy"
	@echo "  check          - Check formatting without changes"
	@echo "  fix            - Run format, lint, and typecheck"
	@echo "  test           - Run pytest tests"
	@echo "  test-coverage  - Run tests with coverage reporting"
	@echo "  validate       - Validate project structure and imports"
	@echo "  venv           - Create virtual environment"
	@echo "  install        - Install production dependencies"
	@echo "  install-dev    - Install development dependencies"
	@echo "  clean          - Remove temporary files (preserves venv)"
	@echo "  clean-pyc      - Remove only Python compiled files"
	@echo "  clean-all      - Remove everything including virtual environment"
	@echo "  config         - Show current configuration"
	@echo "  help           - Show this help message"