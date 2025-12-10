#!/usr/bin/env python3
"""
Legacy Downloader Migration Script

This script systematically migrates all imports and references from the legacy
monolithic downloader to the new modular architecture.
"""

import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def analyze_file_dependencies(file_path: str) -> Dict[str, List[str]]:
    """
    Analyze a file for dependencies on the legacy downloader.

    Args:
        file_path: Path to the file to analyze

    Returns:
        Dictionary mapping import types to lists of specific imports
    """
    dependencies = {
        "module_imports": [],
        "function_imports": [],
        "function_calls": [],
        "class_imports": [],
    }

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            lines = content.split("\n")

            for i, line in enumerate(lines, 1):
                # Module imports
                if re.search(r"from fetchtastic import downloader", line):
                    dependencies["module_imports"].append(f"Line {i}: {line.strip()}")

                # Function imports
                if re.search(r"from fetchtastic\.downloader import (\w+)", line):
                    match = re.search(
                        r"from fetchtastic\.downloader import (\w+)", line
                    )
                    if match:
                        dependencies["function_imports"].append(
                            f"Line {i}: {match.group(1)}"
                        )

                # Function calls
                if re.search(r"downloader\.(\w+)\(", line):
                    match = re.search(r"downloader\.(\w+)\(", line)
                    if match:
                        dependencies["function_calls"].append(
                            f"Line {i}: {match.group(1)}"
                        )

                # Class imports
                if re.search(
                    r"from fetchtastic\.downloader import (\w+)(?=\s*[,\n])", line
                ):
                    match = re.search(
                        r"from fetchtastic\.downloader import (\w+)(?=\s*[,\n])", line
                    )
                    if match:
                        dependencies["class_imports"].append(
                            f"Line {i}: {match.group(1)}"
                        )

    except Exception as e:
        print(f"Error analyzing {file_path}: {e}")

    return dependencies


def create_migration_mapping() -> Dict[str, str]:
    """
    Create a mapping from legacy downloader functions to new architecture.

    Returns:
        Dictionary mapping legacy function names to new import paths
    """
    return {
        # Core functions
        "main": "from fetchtastic.download.cli_integration import DownloadCLIIntegration",
        "check_and_download": "from fetchtastic.download.orchestrator import DownloadOrchestrator",
        # Version functions
        "_normalize_version": "from fetchtastic.download.version import VersionManager",
        "compare_versions": "from fetchtastic.download.version import VersionManager",
        "_get_release_tuple": "from fetchtastic.download.version import VersionManager",
        # File operations
        "_atomic_write": "from fetchtastic.download.files import FileOperations",
        "_atomic_write_json": "from fetchtastic.download.files import FileOperations",
        "_atomic_write_text": "from fetchtastic.download.files import FileOperations",
        "safe_extract_path": "from fetchtastic.download.files import FileOperations",
        "compare_file_hashes": "from fetchtastic.download.files import FileOperations",
        # Cache functions
        "_ensure_cache_dir": "from fetchtastic.download.cache import CacheManager",
        "_load_json_cache_with_expiry": "from fetchtastic.download.cache import CacheManager",
        # Security functions
        "_safe_rmtree": "from fetchtastic.download.files import FileOperations",
        "_sanitize_path_component": "from fetchtastic.download.files import FileOperations",
        "_is_within_base": "from fetchtastic.download.files import FileOperations",
        # Prerelease functions
        "_get_prerelease_patterns": "from fetchtastic.download.android import MeshtasticAndroidAppDownloader",
        "_cleanup_apk_prereleases": "from fetchtastic.download.android import MeshtasticAndroidAppDownloader",
        "cleanup_superseded_prereleases": "from fetchtastic.download.firmware import FirmwareReleaseDownloader",
        # Utility functions
        "_format_api_summary": "from fetchtastic.download.orchestrator import DownloadOrchestrator",
        "_get_json_release_basename": "from fetchtastic.download.orchestrator import DownloadOrchestrator",
        "matches_extract_patterns": "from fetchtastic.download.firmware import FirmwareReleaseDownloader",
    }


def migrate_file(file_path: str, dry_run: bool = True) -> Tuple[int, int]:
    """
    Migrate a single file from legacy downloader to new architecture.

    Args:
        file_path: Path to the file to migrate
        dry_run: If True, only show what would be changed

    Returns:
        Tuple of (changes_made, total_dependencies)
    """
    print(f"\n🔍 Analyzing {file_path}")
    dependencies = analyze_file_dependencies(file_path)
    mapping = create_migration_mapping()

    total_dependencies = sum(len(deps) for deps in dependencies.values())
    if total_dependencies == 0:
        print(f"✅ No legacy downloader dependencies found in {file_path}")
        return 0, 0

    print(f"📋 Found {total_dependencies} dependencies:")
    for dep_type, deps in dependencies.items():
        if deps:
            print(f"  {dep_type}: {len(deps)} items")

    if dry_run:
        print("🔧 Dry run - showing what would be changed:")
        for dep_type, deps in dependencies.items():
            if deps:
                print(f"  {dep_type}:")
                for dep in deps:
                    print(f"    {dep}")
        return 0, total_dependencies

    # Read file content
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    changes_made = 0

    # Apply migrations
    for dep_type, deps in dependencies.items():
        if deps:
            print(f"🔧 Migrating {dep_type}...")

            for dep in deps:
                # Extract function name from line description
                parts = dep.split(":")
                if len(parts) >= 2:
                    line_desc = parts[1].strip()
                    func_match = re.search(r"downloader\.(\w+)", line_desc)
                    if func_match:
                        func_name = func_match.group(1)
                        if func_name in mapping:
                            new_import = mapping[func_name]
                            # Replace the import or call
                            if dep_type == "module_imports":
                                # Replace module import
                                content = re.sub(
                                    r"from fetchtastic import downloader",
                                    new_import,
                                    content,
                                )
                            elif dep_type == "function_imports":
                                # Replace specific function import
                                content = re.sub(
                                    rf"from fetchtastic\.downloader import {func_name}",
                                    new_import,
                                    content,
                                )
                            elif dep_type == "function_calls":
                                # Replace function calls
                                content = re.sub(
                                    rf"downloader\.{func_name}\(",
                                    f"{func_name}(",
                                    content,
                                )

                            changes_made += 1
                            print(f"    ✅ Migrated {func_name}")

    if changes_made > 0:
        if dry_run:
            print(f"📝 Would make {changes_made} changes to {file_path}")
        else:
            # Write changes back
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"📝 Made {changes_made} changes to {file_path}")
    else:
        print(f"❌ No changes made to {file_path}")

    return changes_made, total_dependencies


def migrate_all_files(dry_run: bool = True) -> None:
    """
    Migrate all files that depend on the legacy downloader.

    Args:
        dry_run: If True, only show what would be changed
    """
    print("🚀 Starting legacy downloader migration...")
    print("=" * 50)

    # Files to migrate
    files_to_migrate = [
        "src/fetchtastic/cli.py",
        "src/fetchtastic/setup_config.py",
        "src/fetchtastic/download/migration.py",
    ]

    total_changes = 0
    total_dependencies = 0

    for file_path in files_to_migrate:
        if os.path.exists(file_path):
            changes, deps = migrate_file(file_path, dry_run)
            total_changes += changes
            total_dependencies += deps
        else:
            print(f"⚠️  File not found: {file_path}")

    print("\n" + "=" * 50)
    print("📊 Migration Summary:")
    print(f"   Files analyzed: {len(files_to_migrate)}")
    print(f"   Total dependencies found: {total_dependencies}")
    print(f"   Changes made: {total_changes}")
    print(
        f"   Mode: {'DRY RUN (no changes written)' if dry_run else 'ACTUAL MIGRATION'}"
    )

    if dry_run and total_dependencies > 0:
        print("\n💡 To perform actual migration, run:")
        print("   python scripts/migrate_legacy_downloader.py --execute")


def create_migration_report() -> None:
    """
    Create a detailed migration report showing all dependencies.
    """
    print("📋 Creating Migration Report...")
    print("=" * 50)

    # Files to analyze
    files_to_analyze = [
        "src/fetchtastic/cli.py",
        "src/fetchtastic/setup_config.py",
        "src/fetchtastic/download/migration.py",
        "tests/test_cli.py",
        "tests/test_setup_config.py",
        "tests/test_download_core.py",
        "tests/test_notifications.py",
        "tests/test_utils.py",
        "tests/test_security_paths.py",
        "tests/test_coverage_fix.py",
        "tests/test_repo_downloader.py",
        "tests/test_versions.py",
        "tests/test_prereleases.py",
        "tests/test_extraction.py",
        "tests/test_token_warning_fix.py",
    ]

    report = {}

    for file_path in files_to_analyze:
        if os.path.exists(file_path):
            deps = analyze_file_dependencies(file_path)
            total_deps = sum(len(deps) for deps in deps.values())
            if total_deps > 0:
                report[file_path] = deps

    print(f"📊 Found {len(report)} files with legacy dependencies:")
    for file_path, deps in report.items():
        print(f"\n📁 {file_path}:")
        for dep_type, items in deps.items():
            if items:
                print(f"  {dep_type} ({len(items)}):")
                for item in items[:5]:  # Show first 5 to avoid clutter
                    print(f"    {item}")
                if len(items) > 5:
                    print(f"    ... and {len(items) - 5} more")

    print(f"\n📝 Full report available in migration documentation")


if __name__ == "__main__":
    dry_run = True
    if len(sys.argv) > 1 and sys.argv[1] == "--execute":
        dry_run = False
        print("🚀 EXECUTION MODE: Changes will be written to files!")
    elif len(sys.argv) > 1 and sys.argv[1] == "--report":
        create_migration_report()
        sys.exit(0)

    migrate_all_files(dry_run=dry_run)
