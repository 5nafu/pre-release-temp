#!/usr/bin/env python3
"""
Generate UPDATES.md with version changes from helmRelease.yaml files.
This script can be called in two modes:
1. Normal mode (via semantic-release): Updates UPDATES.md with the latest release
2. Backfill mode (--backfill): Regenerates UPDATES.md with all historical releases
"""
import os
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple

def extract_versions() -> Dict[str, str]:
    """Extract current versions from all helmRelease.yaml files."""
    versions = {}
    templates_dir = Path("cdt-software/templates")
    
    # Find all helmRelease.yaml files
    for helm_file in templates_dir.glob("*/helmRelease.yaml"):
        try:
            with open(helm_file, 'r') as f:
                content = f.read()
                
            # Use more specific regex that looks for chart and version in the spec.chart.spec structure
            # Pattern: lines with indentation, chart: <name>, followed later by version: <version>
            # Look for the nested structure within "chart:" (the HelmRelease spec.chart)
            
            # Find the spec.chart.spec block - extract from first chart: to the sourceRef
            chart_spec_match = re.search(
                r'chart:\s*\n\s+spec:\s*\n(.*?)(?:sourceRef:|valuesFrom:)',
                content,
                re.DOTALL
            )
            
            if chart_spec_match:
                spec_content = chart_spec_match.group(1)
                # Now extract chart name and version from this block
                chart_match = re.search(r'^\s+chart:\s+(\S+)', spec_content, re.MULTILINE)
                version_match = re.search(r'^\s+version:\s+([^\s]+)', spec_content, re.MULTILINE)
                
                if chart_match and version_match:
                    chart_name = chart_match.group(1)
                    version = version_match.group(1)
                    versions[chart_name] = version
        except Exception as e:
            print(f"Warning: Could not process {helm_file}: {e}")
    
    return versions

def get_previous_tag() -> str:
    """Get the previous tag/release (before current HEAD)."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "HEAD~1"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except Exception:
        return None

def get_versions_at_commit(commit: str) -> Dict[str, str]:
    """Get versions from a specific git commit."""
    if not commit:
        return {}
    
    versions = {}
    templates_dir = Path("cdt-software/templates")
    
    try:
        import subprocess
        for helm_file in templates_dir.glob("*/helmRelease.yaml"):
            try:
                result = subprocess.run(
                    ["git", "show", f"{commit}:{helm_file}"],
                    capture_output=True,
                    text=True,
                    check=True
                )
                content = result.stdout
                
                # Use same improved regex pattern
                chart_spec_match = re.search(
                    r'chart:\s*\n\s+spec:\s*\n(.*?)(?:sourceRef:|valuesFrom:)',
                    content,
                    re.DOTALL
                )
                
                if chart_spec_match:
                    spec_content = chart_spec_match.group(1)
                    chart_match = re.search(r'^\s+chart:\s+(\S+)', spec_content, re.MULTILINE)
                    version_match = re.search(r'^\s+version:\s+([^\s]+)', spec_content, re.MULTILINE)
                    
                    if chart_match and version_match:
                        chart_name = chart_match.group(1)
                        version = version_match.group(1)
                        versions[chart_name] = version
            except subprocess.CalledProcessError:
                # File didn't exist in that commit
                pass
    except Exception as e:
        print(f"Warning: Could not get previous versions: {e}")
    
    return versions

def generate_update_entry(version: str, changes: Dict[str, Tuple[str, str]], tag_date: str = None) -> str:
    """Generate a markdown entry for a release."""
    if not changes:
        return ""
    
    if tag_date is None:
        tag_date = datetime.now().strftime("%Y-%m-%d")
    
    entry = f"## [v{version}] - {tag_date}\n\n"
    entry += "### Updated Charts\n\n"
    
    for chart_name in sorted(changes.keys()):
        old_ver, new_ver = changes[chart_name]
        if old_ver:
            entry += f"- **{chart_name}**: `{old_ver}` → `{new_ver}`\n"
        else:
            entry += f"- **{chart_name}**: `{new_ver}` (new)\n"
    
    entry += "\n"
    return entry

def get_all_tags() -> List[str]:
    """Get all git tags sorted by commit date (newest first)."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "tag", "-l", "--sort=-version:refname"],
            capture_output=True,
            text=True,
            check=True
        )
        tags = result.stdout.strip().split('\n')
        return [t for t in tags if t]
    except Exception as e:
        print(f"Warning: Could not get git tags: {e}")
        return []

def get_tag_date(tag: str) -> str:
    """Get the date of a specific tag."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ai", tag],
            capture_output=True,
            text=True,
            check=True
        )
        # Extract just the date part (YYYY-MM-DD)
        date_str = result.stdout.strip().split()[0]
        return date_str
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")

def get_versions_at_tag(tag: str) -> Dict[str, str]:
    """Get versions from a specific git tag."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-list", "-n", "1", tag],
            capture_output=True,
            text=True,
            check=True
        )
        commit = result.stdout.strip()
        return get_versions_at_commit(commit)
    except Exception:
        return {}

def backfill_updates():
    """Regenerate UPDATES.md with all historical releases."""
    print("Backfilling UPDATES.md with all historical releases...")
    
    tags = get_all_tags()
    if not tags:
        print("No tags found")
        return
    
    print(f"Found {len(tags)} tags")
    
    # Generate entries for each tag
    all_entries = []
    
    for i, tag in enumerate(tags):
        # Remove 'v' prefix if present
        version = tag.lstrip("v")
        
        # Get versions at this tag
        current_versions = get_versions_at_tag(tag)
        
        if not current_versions:
            print(f"  Skipping {tag} - could not extract versions")
            continue
        
        # Get versions at previous tag (or empty if first tag)
        previous_versions = {}
        if i + 1 < len(tags):
            previous_versions = get_versions_at_tag(tags[i + 1])
        
        # Calculate changes
        changes = {}
        for chart, new_ver in sorted(current_versions.items()):
            old_ver = previous_versions.get(chart, "")
            if old_ver != new_ver:
                changes[chart] = (old_ver, new_ver)
        
        if changes:
            tag_date = get_tag_date(tag)
            entry = generate_update_entry(version, changes, tag_date)
            all_entries.append(entry)
            print(f"  Generated entry for {tag}")
        else:
            print(f"  No changes in {tag}")
    
    # Read intro text from existing file
    updates_file = Path("UPDATES.md")
    intro_text = ""
    
    if updates_file.exists():
        with open(updates_file, 'r') as f:
            existing_content = f.read()
        
        lines = existing_content.split('\n')
        intro_lines = []
        
        for i, line in enumerate(lines):
            if line.startswith("## [v"):
                break
            elif i > 0 and line and not line.startswith("# "):
                intro_lines.append(line)
        
        if intro_lines:
            intro_text = "\n".join(intro_lines).strip() + "\n\n"
    
    # Write updated file
    with open(updates_file, 'w') as f:
        f.write("# Chart Updates\n\n")
        if intro_text:
            f.write(intro_text)
        f.write("".join(all_entries))
    
    print(f"\nBackfill complete! Updated UPDATES.md with {len(all_entries)} releases")



def main():
    # Check for backfill mode
    if "--backfill" in sys.argv:
        backfill_updates()
        return
    
    # Get the release version
    # Can come from command line args (passed by exec plugin) or environment variables
    release_version = None
    
    # Command line argument: script.py [version]
    if len(sys.argv) > 1 and sys.argv[1] != "--backfill":
        release_version = sys.argv[1]
    
    # Fall back to environment variable if not provided as arguments
    if not release_version:
        release_version = os.environ.get("RELEASE_VERSION", "").lstrip("v")
    
    debug = os.environ.get("DEBUG", "").lower() == "true"
    
    if not release_version:
        # Try to get from git describe
        try:
            import subprocess
            result = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                capture_output=True,
                text=True,
                check=True
            )
            release_version = result.stdout.strip().lstrip("v")
        except Exception:
            print("Warning: Could not determine release version, skipping UPDATES.md generation")
            return
    
    # Get current versions
    current_versions = extract_versions()
    if debug:
        print(f"Current versions found: {len(current_versions)}")
    
    # Get previous tag instead of previous commit
    prev_tag = get_previous_tag()
    if debug:
        print(f"Previous tag: {prev_tag or 'None'}")
    
    previous_versions = get_versions_at_tag(prev_tag) if prev_tag else {}
    if debug:
        print(f"Previous versions found: {len(previous_versions)}")
    
    # Calculate changes
    changes = {}
    for chart, new_ver in sorted(current_versions.items()):
        old_ver = previous_versions.get(chart, "")
        if old_ver != new_ver:
            changes[chart] = (old_ver, new_ver)
            if debug:
                print(f"  Change detected: {chart}")
    
    if not changes:
        print("No version changes detected")
        return
    
    # Generate new entry
    new_entry = generate_update_entry(release_version, changes)
    
    # Read existing UPDATES.md
    updates_file = Path("UPDATES.md")
    existing_content = ""
    intro_text = ""
    
    if updates_file.exists():
        with open(updates_file, 'r') as f:
            existing_content = f.read()
    
    # Extract intro text (lines after header until first version entry)
    lines = existing_content.split('\n')
    intro_lines = []
    entry_start_idx = 0
    
    for i, line in enumerate(lines):
        if line.startswith("## [v"):
            # Found start of first version entry
            entry_start_idx = i
            break
        elif i > 0 and line and not line.startswith("# "):
            # This is intro text (non-header, non-empty lines after header)
            intro_lines.append(line)
    
    if intro_lines:
        intro_text = "\n".join(intro_lines).strip() + "\n\n"
    
    # Reconstruct file: header + intro + new entries + old entries
    remaining_entries = "\n".join(lines[entry_start_idx:]).strip() if entry_start_idx > 0 else ""
    
    with open(updates_file, 'w') as f:
        f.write("# Chart Updates\n\n")
        if intro_text:
            f.write(intro_text)
        f.write(new_entry)
        if remaining_entries:
            f.write(remaining_entries + "\n")
    
    print(f"Updated UPDATES.md with version {release_version}")
    
    # Print changes for visibility
    print("\nVersion Changes:")
    for chart, (old_ver, new_ver) in sorted(changes.items()):
        print(f"  {chart}: {old_ver or 'new'} → {new_ver}")

if __name__ == "__main__":
    main()
