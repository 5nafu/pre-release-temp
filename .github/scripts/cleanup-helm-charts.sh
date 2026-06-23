#!/bin/bash

# GitHub Packages Helm Chart Cleanup Script
# This script removes Helm chart versions older than a specified number of days from GitHub Container Registry

set -euo pipefail

# Configuration
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
GITHUB_OWNER="${GITHUB_OWNER:-cloudeteer}"
CHART_NAME="${1:-}"
DAYS_TO_KEEP="${2:-60}"
DRY_RUN="${DRY_RUN:-false}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Usage function
usage() {
    cat << EOF
Usage: $0 <chart-name> [days-to-keep]

Examples:
  $0 cert-manager 60
  $0 prometheus 30
  DRY_RUN=true $0 external-secrets 45

Environment Variables:
  GITHUB_TOKEN    - GitHub token with packages:delete scope (required)
  GITHUB_OWNER    - GitHub organization/user (default: cloudeteer)
  RUN             - Set to 'true' to actually delete (default: false)

Arguments:
  chart-name      - Name of the Helm chart to clean up (required)
  days-to-keep    - Number of days to keep versions (default: 60)
EOF
}

# Validate inputs
validate_inputs() {
    if [[ -z "$GITHUB_TOKEN" ]]; then
        log_error "GITHUB_TOKEN environment variable is required"
        exit 1
    fi

    if [[ -z "$CHART_NAME" ]]; then
        log_error "Chart name is required"
        usage
        exit 1
    fi

    if ! [[ "$DAYS_TO_KEEP" =~ ^[0-9]+$ ]] || [[ "$DAYS_TO_KEEP" -lt 1 ]]; then
        log_error "Days to keep must be a positive integer"
        exit 1
    fi

    log_info "Configuration:"
    log_info "  Chart: $CHART_NAME"
    log_info "  Owner: $GITHUB_OWNER"
    log_info "  Days to keep: $DAYS_TO_KEEP"
    log_info "  delete run: $RUN"
}

# Get package versions from GitHub API
get_package_versions() {
    local chart_name="$1"
    local api_url="https://api.github.com/orgs/${GITHUB_OWNER}/packages/container/${chart_name}/versions"
    
    log_info "Fetching package versions for $chart_name..."
    
    curl -s \
        -H "Authorization: Bearer $GITHUB_TOKEN" \
        -H "Accept: application/vnd.github.v3+json" \
        "$api_url" || {
        log_error "Failed to fetch package versions. Check if chart exists and token has correct permissions."
        return 1
    }
}

# Calculate cutoff date
get_cutoff_date() {
    local days_ago="$1"
    date -d "$days_ago days ago" -u +"%Y-%m-%dT%H:%M:%SZ"
}

# Delete a specific package version
delete_package_version() {
    local chart_name="$1"
    local version_id="$2"
    local version_tag="$3"
    local api_url="https://api.github.com/orgs/${GITHUB_OWNER}/packages/container/${chart_name}/versions/${version_id}"
    
    if [[ "$RUN" != "true" ]]; then
        log_warning "DRY RUN: Would delete version $version_tag (ID: $version_id)"
        return 0
    fi
    
    log_info "Deleting version $version_tag (ID: $version_id)..."
    
    local response
    response=$(curl -s -w "%{http_code}" \
        -X DELETE \
        -H "Authorization: Bearer $GITHUB_TOKEN" \
        -H "Accept: application/vnd.github.v3+json" \
        "$api_url")
    
    local http_code="${response: -3}"
    
    if [[ "$http_code" -eq 204 ]]; then
        log_success "Successfully deleted version $version_tag"
        return 0
    else
        log_error "Failed to delete version $version_tag (HTTP: $http_code)"
        return 1
    fi
}

# Main cleanup function
cleanup_old_versions() {
    local chart_name="$1"
    local days_to_keep="$2"
    
    # Get cutoff date
    local cutoff_date
    cutoff_date=$(get_cutoff_date "$days_to_keep")
    log_info "Cutoff date: $cutoff_date"
    
    # Get package versions
    local versions_json
    versions_json=$(get_package_versions "$chart_name") || return 1
    
    # Check if we got valid JSON
    if ! echo "$versions_json" | jq . >/dev/null 2>&1; then
        log_error "Invalid JSON response from GitHub API"
        echo "$versions_json"
        return 1
    fi
    
    # Count total versions
    local total_versions
    total_versions=$(echo "$versions_json" | jq '. | length')
    log_info "Found $total_versions total versions"
    
    # Filter versions older than cutoff date
    local old_versions
    old_versions=$(echo "$versions_json" | jq --arg cutoff "$cutoff_date" '
        map(select(.created_at < $cutoff)) | 
        sort_by(.created_at)
    ')
    
    local old_versions_count
    old_versions_count=$(echo "$old_versions" | jq '. | length')
    
    if [[ "$old_versions_count" -eq 0 ]]; then
        log_success "No versions older than $days_to_keep days found"
        return 0
    fi
    
    log_info "Found $old_versions_count versions older than $days_to_keep days"
    
    # List versions to be deleted
    echo "$old_versions" | jq -r '.[] | "\(.metadata.container.tags[0] // "untagged") - \(.created_at) (ID: \(.id))"' | while read -r version_info; do
        log_warning "Will delete: $version_info"
    done
    
    # Confirm deletion (unless in CI or dry run)
    if [[ "$DRY_RUN" != "true" && -t 0 && -t 1 ]]; then
        echo
        read -p "Do you want to proceed with deletion? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Deletion cancelled by user"
            return 0
        fi
    fi
    
    # Delete old versions
    local deleted_count=0
    local failed_count=0
    
    echo "$old_versions" | jq -r '.[] | "\(.id)|\(.metadata.container.tags[0] // "untagged")"' | while IFS='|' read -r version_id version_tag; do
        if delete_package_version "$chart_name" "$version_id" "$version_tag"; then
            ((deleted_count++))
        else
            ((failed_count++))
        fi
        
        # Rate limiting - wait between deletions
        sleep 1
    done
    
    log_success "Cleanup completed for $chart_name"
    if [[ "$DRY_RUN" != "true" ]]; then
        log_info "Deleted: $deleted_count versions"
        if [[ $failed_count -gt 0 ]]; then
            log_warning "Failed: $failed_count versions"
        fi
    fi
}

# Check dependencies
check_dependencies() {
    local missing_deps=()
    
    if ! command -v curl >/dev/null 2>&1; then
        missing_deps+=("curl")
    fi
    
    if ! command -v jq >/dev/null 2>&1; then
        missing_deps+=("jq")
    fi
    
    if [[ ${#missing_deps[@]} -gt 0 ]]; then
        log_error "Missing required dependencies: ${missing_deps[*]}"
        log_error "Please install them and try again"
        exit 1
    fi
}

# Main execution
main() {
    log_info "GitHub Packages Helm Chart Cleanup Script"
    log_info "=========================================="
    
    check_dependencies
    validate_inputs
    
    echo
    cleanup_old_versions "$CHART_NAME" "$DAYS_TO_KEEP"
}

# Execute main function
main "$@"