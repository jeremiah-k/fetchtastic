# Testing Guide for Fetchtastic

This guide covers testing patterns and best practices for the Fetchtastic project.

## Environment Setup

### 1. Create Virtual Environment

If a virtual environment doesn't exist, create one:

```bash
# Create virtual environment (python3 recommended)
python3 -m venv venv

# Alternative: create venv directory
python3 -m venv .venv
```

### 2. Activate Virtual Environment

**Linux/macOS:**

```bash
source venv/bin/activate
# or
. venv/bin/activate
```

**Windows:**

```bash
venv\Scripts\activate
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

**Note:** Ensure your virtual environment is activated before running tests (see Environment Setup section above).

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

# Run specific test class
python -m pytest tests/test_exceptions.py::TestFetchtasticError
```

### Running Tests by Markers

The project uses pytest markers to categorize tests:

| Marker           | Description                             |
| ---------------- | --------------------------------------- |
| `unit`           | Unit tests (fast, isolated)             |
| `integration`    | Integration tests (may require network) |
| `slow`           | Slow-running tests                      |
| `performance`    | Performance benchmarks                  |
| `core_downloads` | Core download functionality tests       |
| `user_interface` | CLI and menu system tests               |
| `configuration`  | Setup and configuration tests           |
| `infrastructure` | Logging, constants, and utilities tests |

**Example usage:**

```bash
# Run only unit tests
python -m pytest tests/ -m unit

# Run only core download tests
python -m pytest tests/ -m core_downloads

# Run user interface tests
python -m pytest tests/ -m user_interface

# Skip slow tests
python -m pytest tests/ -m "not slow"

# Run multiple markers
python -m pytest tests/ -m "unit and core_downloads"
```

### Coverage Reports

The project is configured to generate coverage reports automatically when running pytest. Coverage reports will be generated in:

- Terminal output (summary)
- `coverage.xml` (XML format)
- `htmlcov/` directory (HTML format)

```bash
# Run tests with coverage
python -m pytest tests/ --cov

# Generate HTML coverage report
python -m pytest tests/ --cov --cov-report=html
```

## Test Organization

### Test File Structure

Tests are organized in the `tests/` directory with the following conventions:

- Test files should start with `test_`
- Test functions should start with `test_`
- Test classes should start with `Test`

### Pytest Markers

New test files should include appropriate pytest markers. Use module-level markers when all tests in a file share the same category:

```python
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]

from fetchtastic.download.base import BaseDownloader
```

Or use function-level markers for individual tests:

```python
import pytest

@pytest.mark.unit
@pytest.mark.core_downloads
def test_download_file():
    """Test file download functionality."""
    pass

@pytest.mark.integration
def test_api_integration():
    """Test API integration."""
    pass
```

### Test Class Organization

When using test classes, apply markers at the class level:

```python
import pytest

@pytest.mark.unit
class TestDeviceHardwareManagerExceptionHandlers:
    """Test exception handlers."""

    def test_fetch_from_api_type_error(self):
        """Test handling of TypeError."""
        pass
```

## Best Practices

### 1. Descriptive Test Names

- Use descriptive test method names that explain the scenario
- Include expected behavior in the name

```python
def test_download_file_with_invalid_url_returns_false():
    """Test that download returns False for invalid URLs."""
    pass
```

### 2. Arrange-Act-Assert Pattern

Structure tests using the AAA pattern:

```python
def test_download_success():
    """Test successful file download."""
    # Arrange
    config = {}
    downloader = ConcreteDownloader(config)

    # Act
    with patch("fetchtastic.utils.download_file_with_retry") as mock_download:
        mock_download.return_value = True
        result = downloader.download("https://example.com/file.txt", target_path)

    # Assert
    assert result is True
```

### 3. Mock at the Right Level

- Mock external dependencies, not internal logic
- Mock at the boundary of your system under test

```python
# Good: Mock external HTTP requests
@patch("fetchtastic.utils.requests.get")
def test_api_call(mock_get):
    mock_get.return_value.json.return_value = {"version": "2.5.0"}
    result = fetch_version()
    assert result == "2.5.0"
```

### 4. Test Error Conditions

- Test both success and failure scenarios
- Test exception handling and edge cases

```python
def test_download_handles_network_error():
    """Test download handles network errors gracefully."""
    with patch("requests.get") as mock_get:
        mock_get.side_effect = requests.RequestException("Network error")
        result = download_file(url)
        assert result is False
```

### 5. Avoid Test Interdependence

- Each test should be independent
- Use fixtures for common initialization

```python
@pytest.fixture
def temp_dir():
    """Provide a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp

def test_file_operations(temp_dir):
    """Test file operations using temp directory."""
    file_path = Path(temp_dir) / "test.txt"
    # Test operations...
```

## Common Patterns

### File System Mocking

```python
@patch("builtins.open", new_callable=mock_open, read_data="test data")
@patch("os.path.exists", return_value=True)
def test_file_operations(self, mock_exists, mock_file):
    # Test file operations
    pass
```

### Environment Variable Mocking

```python
@patch.dict(os.environ, {"TEST_VAR": "test_value"})
def test_environment_dependent_code(self):
    # Test code that depends on environment variables
    pass
```

### Exception Testing

```python
def test_invalid_version_raises_error():
    """Test that invalid version raises VersionError."""
    with pytest.raises(VersionError) as exc_info:
        parse_version("invalid")
    assert "Invalid version" in str(exc_info.value)
```

### Network Mocking

```python
@patch("fetchtastic.menu_firmware.make_github_api_request")
def test_fetch_firmware_assets_network_error(mock_req):
    """Test handling of network errors."""
    mock_req.side_effect = requests.RequestException("Connection failed")
    result = menu_firmware.fetch_firmware_assets()
    assert result == []
```

## Code Quality

### Primary Linter (Trunk)

We primarily use **Trunk** for linting and code quality checks:

```bash
# Run trunk check to lint all files
.trunk/trunk check

# Fix auto-fixable issues
.trunk/trunk check --fix --all

# Check specific files
.trunk/trunk check src/fetchtastic/exceptions.py
```

Trunk is configured via `.trunk/trunk.yaml` and automatically runs all configured linters including ruff, black, and others.

### Manual Type Checking

For additional type checking beyond what trunk provides, you can run:

```bash
# Run pyright type checking
pyright src/

# Run mypy with strict mode
python -m mypy src/ --strict
```

These manual checks can catch additional type-related issues that may not be caught by trunk's default configuration.

## Troubleshooting

### Common Issues

1. **"externally-managed-environment" error**
   - Solution: Activate the virtual environment before installing packages
   - Use `source venv/bin/activate` (Linux/macOS) or `venv\Scripts\activate` (Windows)

2. **ModuleNotFoundError**
   - Solution: Ensure virtual environment is activated
   - Install dependencies with `pip install -r requirements.txt`

3. **Coverage-related errors**
   - Solution: Install pytest-cov with `pip install pytest-cov`
   - This is typically included in requirements-dev.txt

### Verification Commands

```bash
# Check if virtual environment is active (should show venv path)
which python  # Use 'where.exe python' on Windows

# Verify pytest installation
python -m pytest --version

# Test basic imports
python -c "import fetchtastic; print('Imports successful')"
```

## Development Workflow

1. Activate virtual environment
2. Install/update dependencies
3. Make code changes
4. Run tests to verify functionality
5. Check coverage reports if needed
6. Run trunk linter (primary): `.trunk/trunk check --fix --all`
7. Optionally run manual type checkers: `pyright src/` or `mypy src/ --strict`
8. Deactivate virtual environment when done (`deactivate`)

## Notes

- The project uses Python 3.10+ (check current version with `python --version`)
- Test configuration is in `pyproject.toml`
- Coverage configuration is also in `pyproject.toml`
- All tests should pass before committing changes
- The virtual environment directories (`venv/`, `.venv/`) are ignored by git per `.gitignore`
- Use `# noqa: BLE001` comments for intentional broad exception catches (see existing code for examples)
