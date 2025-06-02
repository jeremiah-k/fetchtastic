# Publishing Guide for Fetchtastic

## Automated Publishing (Preferred)

The repository is configured with GitHub Actions to automatically publish to PyPI when a release is created.

### Workflow File

- `.github/workflows/pypi-publish.yml`
- Triggers on: `release.types: [created]`
- Uses trusted publishing with PyPI

### If Automated Publishing Fails

Check the GitHub Actions workflow runs:

1. Go to the repository's Actions tab
2. Look for "Upload Python Package to PyPI" workflow
3. Check for any errors in the workflow run

Common issues:

- PyPI trusted publishing not configured
- Release environment not set up
- Build failures
- Version conflicts

## Manual Publishing (Fallback)

If the automated workflow fails, you can publish manually:

### Prerequisites

```bash
pip install build twine
```

### Steps

1. **Ensure you're on the correct branch/tag:**

   ```bash
   git checkout main
   git pull
   git checkout v0.6.0  # or the appropriate tag
   ```

2. **Clean previous builds:**

   ```bash
   rm -rf dist/ build/ *.egg-info/
   ```

3. **Build the package:**

   ```bash
   python -m build
   ```

4. **Check the build:**

   ```bash
   twine check dist/*
   ```

5. **Upload to PyPI:**
   ```bash
   twine upload dist/*
   ```

### Version Verification

After publishing, verify the version is available:

```bash
# Check PyPI directly
curl -s https://pypi.org/pypi/fetchtastic/json | jq -r '.info.version'

# Test installation
pipx install fetchtastic --force
fetchtastic version
```

## Troubleshooting

### Version 0.6.0 Not Available on PyPI

If users report that `pipx upgrade fetchtastic` shows "already at latest version 0.5.0":

1. **Check if 0.6.0 was published:**

   - Visit https://pypi.org/project/fetchtastic/
   - Check the version history

2. **If not published, run manual publishing steps above**

3. **If published but pipx cache is stale:**

   ```powershell
   # Windows
   pipx install fetchtastic[win] --force

   # Linux/macOS
   pipx install fetchtastic --force
   ```

### PyPI Trusted Publishing Setup

If automated publishing fails due to authentication:

1. Go to PyPI project settings
2. Configure trusted publishing for the repository
3. Set up the release environment in GitHub repository settings

## Release Checklist

- [ ] Version updated in `setup.cfg`
- [ ] Release notes prepared
- [ ] GitHub release created with proper tag
- [ ] Automated workflow completed successfully
- [ ] Version available on PyPI
- [ ] Installation scripts tested
- [ ] Upgrade process verified

## Emergency Hotfix

For critical issues requiring immediate release:

1. Create hotfix branch from main
2. Make minimal necessary changes
3. Update version in setup.cfg (patch version)
4. Create release immediately
5. If automated publishing fails, use manual publishing
6. Test installation/upgrade process
7. Notify users of the hotfix availability
