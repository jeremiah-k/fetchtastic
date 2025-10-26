# Testing Instructions for Fetchtastic

This document provides instructions for setting up the development environment and running tests for the Fetchtastic project.

## Environment Setup

### 1. Create Virtual Environment

If a virtual environment doesn't exist, create one using the patterns from `.gitignore`:

```bash
# Create virtual environment (python3 recommended)
python3 -m venv .venv

# Alternative: create venv directory
python3 -m venv venv
```

### 2. Activate Virtual Environment

**Linux/macOS:**

```bash
source .venv/bin/activate
# or
. .venv/bin/activate
```

**Windows:**

```bash
.venv\Scripts\activate
```

### 3. Install Dependencies

Install required packages from requirements files:

```bash
# Install basic dependencies
pip install -r requirements.txt

# Install development dependencies (includes testing tools)
pip install -r requirements-dev.txt
```

## Running Tests

### Basic Test Execution

```bash
# Run all tests
python -m pytest tests/

# Run tests with verbose output
python -m pytest tests/ -v

# Run tests with short traceback on errors
python -m pytest tests/ --tb=short
```

### Running Specific Tests

```bash
# Run specific test file
python -m pytest tests/test_cli.py

# Run specific test function
python -m pytest tests/test_cli.py::test_function_name

# Run recently modified tests (example)
python -m pytest tests/test_setup_config.py tests/test_log_utils_level.py tests/test_downloader.py
```

### Coverage Reports

The project is configured to generate coverage reports automatically when running pytest. Coverage reports will be generated in:

- Terminal output (summary)
- `coverage.xml` (XML format)
- `htmlcov/` directory (HTML format)

### Test Markers

The project uses pytest markers to categorize tests:

- `unit` - Unit tests
- `integration` - Integration tests
- `slow` - Slow-running tests
- `performance` - Performance benchmarks
- `core_downloads` - Core download functionality tests
- `user_interface` - CLI and menu system tests
- `configuration` - Setup and configuration tests
- `infrastructure` - Logging, constants, and utilities tests

**Example usage:**

```bash
# Run only unit tests
python -m pytest tests/ -m unit

# Skip slow tests
python -m pytest tests/ -m "not slow"
```

## Troubleshooting

### Common Issues

1. **"externally-managed-environment" error**
   - Solution: Activate the virtual environment before installing packages
   - Use `source .venv/bin/activate` (Linux/macOS) or `.venv\Scripts\activate` (Windows)

2. **ModuleNotFoundError**
   - Solution: Ensure virtual environment is activated
   - Install dependencies with `pip install -r requirements.txt`

3. **Coverage-related errors**
   - Solution: Install pytest-cov with `pip install pytest-cov`
   - This is typically included in requirements-dev.txt

### Verification Commands

```bash
# Check if virtual environment is active (should show venv path)
which python

# Verify pytest installation
python -m pytest --version

# Test basic imports
python -c "import src.fetchtastic; print('Imports successful')"
```

## Development Workflow

1. Activate virtual environment
2. Install/update dependencies
3. Make code changes
4. Run tests to verify functionality
5. Check coverage reports if needed
6. Deactivate virtual environment when done (`deactivate`)

## Notes

- The project uses Python 3.12+ (check current version with `python --version`)
- Test configuration is in `pyproject.toml`
- Coverage configuration is also in `pyproject.toml`
- All tests should pass before committing changes
- The virtual environment directories (`.venv/`, `venv/`) are ignored by git per `.gitignore`
