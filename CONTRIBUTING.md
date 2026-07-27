# Contributing to next_pass

## Development Setup

### 1. Install dependencies

```bash
# Install all dependencies
conda env create -f environment.yml
conda activate next_pass

# Install package in editable mode
pip install -e .
```

### 2. Setup pre-commit hooks

```bash
# Install pre-commit hooks
pre-commit install

# Run hooks on all files (optional, to check current state)
pre-commit run --all-files
```

The pre-commit hooks will automatically run on every commit and check:
- Code formatting (black)
- Import sorting (isort)
- Linting (flake8)
- Trailing whitespace
- YAML/TOML syntax
- Large file additions
- Debug statements

### 3. Run tests

```bash
pytest tests/
```

### 4. Code formatting

Pre-commit will auto-format code, but you can also run manually:

```bash
# Format all Python files
black .

# Sort imports
isort .

# Check linting
flake8
```

## Pull Request Guidelines

1. Ensure all tests pass
2. Add tests for new features
3. Update documentation as needed
4. Follow existing code style (enforced by pre-commit)
5. Keep commits focused and atomic
6. Write clear commit messages

## Adding New Satellites

When adding support for a new satellite:
1. Create a new module in `utils/` (e.g., `utils/new_satellite_pass.py`)
2. Add corresponding tests in `tests/test_new_satellite_pass.py`
3. Update CLI options in `next_pass.py`
4. Add examples to README.md
5. Include small test fixtures if needed
