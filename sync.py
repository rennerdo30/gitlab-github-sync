#!/usr/bin/env python3
"""Main entry point for GitLab-GitHub sync tool."""

import argparse
import fnmatch
import logging
import sys
import yaml
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import cli_wrapper
from sync_engine import SyncEngine
from state_manager import StateManager

# Configure logging with tqdm support
class TqdmLoggingHandler(logging.Handler):
    """Logging handler that uses tqdm.write() to avoid interfering with progress bars."""
    def __init__(self):
        super().__init__()
        self.tqdm_instance = None
    
    def set_tqdm(self, tqdm_instance):
        """Set the tqdm instance to use for writing."""
        self.tqdm_instance = tqdm_instance
    
    def emit(self, record):
        try:
            msg = self.format(record)
            if self.tqdm_instance:
                # Use tqdm.write() to avoid interfering with progress bar
                self.tqdm_instance.write(msg)
            else:
                # Fallback to regular print if no tqdm instance
                print(msg)
        except Exception:
            self.handleError(record)

# Set up logging
logger = logging.getLogger(__name__)
tqdm_handler = TqdmLoggingHandler()
tqdm_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
                                            datefmt='%Y-%m-%d %H:%M:%S'))
logger.addHandler(tqdm_handler)
logger.setLevel(logging.INFO)

# Also configure root logger for other modules
root_logger = logging.getLogger()
root_logger.addHandler(tqdm_handler)
root_logger.setLevel(logging.INFO)


def load_config(config_path: str) -> dict:
    """
    Load configuration from YAML file.
    
    Args:
        config_path: Path to the configuration file
        
    Returns:
        Configuration dictionary
        
    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If config file is invalid
    """
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
    
    if not config:
        raise ValueError("Configuration file is empty or invalid")
    
    return config


def is_blacklisted(repo_path: str, blacklist: Optional[Dict[str, List[str]]] = None) -> bool:
    """Check if a repository is blacklisted.
    
    Args:
        repo_path: Repository path (e.g., "group/repo" or "owner/repo")
        blacklist: Blacklist configuration with 'gitlab' and 'github' keys
        
    Returns:
        True if repository is blacklisted
    """
    if not blacklist:
        return False
    
    # Check both gitlab and github blacklists
    for platform, patterns in blacklist.items():
        if not patterns:
            continue
        for pattern in patterns:
            if fnmatch.fnmatch(repo_path, pattern):
                return True
    return False


def discover_repositories(gitlab_owner: str = None, github_owner: str = None, 
                          create_missing: bool = False,
                          blacklist: Optional[Dict[str, List[str]]] = None) -> List[Dict[str, str]]:
    """
    Discover repositories from GitLab and GitHub and create mappings.
    
    Args:
        gitlab_owner: GitLab group/namespace (optional, defaults to user's repos)
        github_owner: GitHub username or organization (optional, defaults to user's repos)
        create_missing: If True, automatically create missing repositories
        
    Returns:
        List of repository mappings: [{"gitlab": "group/repo", "github": "owner/repo"}, ...]
    """
    logger.info("Discovering repositories...")
    
    # Get repositories from GitLab
    try:
        logger.info(f"Fetching GitLab repositories{' from ' + gitlab_owner if gitlab_owner else ''}...")
        # Try as group first, if that fails, try as user
        gitlab_repos = []
        if gitlab_owner:
            try:
                gitlab_repos = cli_wrapper.glab_repo_list(group=gitlab_owner, mine=False)
            except cli_wrapper.CLIError as e:
                if "No group matching" in str(e) or "group" in str(e).lower():
                    # Not a group, try as user
                    logger.debug(f"'{gitlab_owner}' is not a group, trying as user...")
                    gitlab_repos = cli_wrapper.glab_repo_list(user=gitlab_owner, mine=False)
                else:
                    raise
        else:
            gitlab_repos = cli_wrapper.glab_repo_list(mine=True)
        logger.info(f"Found {len(gitlab_repos)} GitLab repositories")
    except Exception as e:
        logger.error(f"Failed to fetch GitLab repositories: {e}")
        return []
    
    # Get repositories from GitHub
    try:
        logger.info(f"Fetching GitHub repositories{' from ' + github_owner if github_owner else ''}...")
        github_repos = cli_wrapper.gh_repo_list(owner=github_owner, limit=1000)
        logger.info(f"Found {len(github_repos)} GitHub repositories")
    except Exception as e:
        logger.error(f"Failed to fetch GitHub repositories: {e}")
        return []
    
    # Create mappings by matching repository names
    mappings = []
    gitlab_by_name = {}
    
    # Build GitLab repo lookup by name (without namespace)
    for repo in gitlab_repos:
        # GitLab uses "path" for repo name, "path_with_namespace" for full path
        repo_name = repo.get("path") or repo.get("name") or ""
        repo_path = (repo.get("path_with_namespace") or 
                    repo.get("full_path") or 
                    repo.get("path") or "")
        
        if repo_name and repo_path:
            # Check blacklist
            if blacklist and is_blacklisted(repo_path, blacklist):
                logger.debug(f"Skipping blacklisted GitLab repo: {repo_path}")
                continue
            # Store by name for matching
            if repo_name not in gitlab_by_name:
                gitlab_by_name[repo_name] = []
            gitlab_by_name[repo_name].append(repo_path)
            logger.debug(f"GitLab repo: name={repo_name}, path={repo_path}")
    
    # Log blacklist filtering summary
    if blacklist:
        gitlab_blacklisted = sum(1 for r in gitlab_repos 
                                if is_blacklisted(r.get("path_with_namespace") or r.get("path", ""), blacklist))
        if gitlab_blacklisted > 0:
            logger.info(f"Filtered {gitlab_blacklisted} blacklisted GitLab repositories")
    
    # Match GitHub repos with GitLab repos by name
    matched_github = set()
    
    # Try to import tqdm for progress bar
    try:
        from tqdm import tqdm
        use_progress = True
    except ImportError:
        use_progress = False
        # Create a dummy tqdm class that does nothing
        class tqdm:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def set_description(self, *args, **kwargs):
                pass
            def update(self, *args, **kwargs):
                pass
    
    # Filter out blacklisted repos first to get accurate count
    repos_to_process = [r for r in github_repos 
                       if r.get("name") and r.get("nameWithOwner") and
                       not (blacklist and is_blacklisted(r.get("nameWithOwner", ""), blacklist))]
    
    with tqdm(total=len(repos_to_process), desc="Matching repositories", unit="repo",
              disable=not use_progress or len(repos_to_process) < 10) as pbar:
        for repo in github_repos:
            repo_name = repo.get("name", "")
            repo_owner = repo.get("nameWithOwner", "")
            
            if not repo_name or not repo_owner:
                continue
            
            # Check blacklist
            if blacklist and is_blacklisted(repo_owner, blacklist):
                logger.debug(f"Skipping blacklisted GitHub repo: {repo_owner}")
                continue
            
            logger.debug(f"GitHub repo: name={repo_name}, owner={repo_owner}")
            
            # Update progress bar
            if use_progress and len(repos_to_process) >= 10:
                pbar.set_description(f"Matching: {repo_name[:30]}")
            
            # Try to find matching GitLab repo by name
            if repo_name in gitlab_by_name:
                # Use the first matching GitLab repo
                gitlab_path = gitlab_by_name[repo_name][0]
                mappings.append({
                    "gitlab": gitlab_path,
                    "github": repo_owner
                })
                matched_github.add(repo_owner)
                logger.info(f"✓ Matched: {gitlab_path} <-> {repo_owner}")
            else:
                # No GitLab match found - create one if create_missing and gitlab_owner are provided
                if create_missing and gitlab_owner:
                    gitlab_path = f"{gitlab_owner}/{repo_name}"
                    logger.info(f"GitLab repo doesn't exist: {gitlab_path}. Creating it...")
                    try:
                        # Get GitHub repo info to copy description/homepage
                        github_repo_data = cli_wrapper.gh_repo_view(repo_owner)
                        description = ""
                        homepage = ""
                        private = False
                        if github_repo_data:
                            description = github_repo_data.get("description", "") or ""
                            homepage = github_repo_data.get("homepageUrl", "") or ""
                            # GitHub doesn't expose visibility in repo view, assume private for safety
                            private = True
                        
                        # glab repo create accepts full path like "user/repo" or "group/repo"
                        created_repo = cli_wrapper.glab_repo_create(
                            gitlab_path,  # Full path: "rennerdo30/repo-name"
                            description=description[:500] if description else None,
                            group=None,  # Path already includes namespace
                            private=private
                        )
                        
                        if created_repo:
                            logger.info(f"✓ Created GitLab repo: {gitlab_path}")
                            mappings.append({
                                "gitlab": gitlab_path,
                                "github": repo_owner
                            })
                            matched_github.add(repo_owner)
                        else:
                            logger.warning(f"Failed to create GitLab repo: {gitlab_path} (check if you have permissions)")
                            # Still create mapping so it can be retried later
                            mappings.append({
                                "gitlab": gitlab_path,
                                "github": repo_owner
                            })
                            matched_github.add(repo_owner)
                            logger.info(f"→ Mapping created anyway: {gitlab_path} <-> {repo_owner} (repo creation failed, will skip during sync)")
                    except Exception as e:
                        logger.warning(f"Failed to create GitLab repo {gitlab_path}: {e}")
                        # Still create mapping so it can be retried later
                        mappings.append({
                            "gitlab": gitlab_path,
                            "github": repo_owner
                        })
                        matched_github.add(repo_owner)
                        logger.info(f"→ Mapping created anyway: {gitlab_path} <-> {repo_owner} (will skip during sync)")
                else:
                    logger.debug(f"  No GitLab match found for '{repo_name}'")
            
            # Update progress bar
            if use_progress and len(repos_to_process) >= 10:
                pbar.update(1)
    
    # Log blacklist filtering summary for GitHub
    if blacklist:
        github_blacklisted = sum(1 for r in github_repos 
                                if is_blacklisted(r.get("nameWithOwner", ""), blacklist))
        if github_blacklisted > 0:
            logger.info(f"Filtered {github_blacklisted} blacklisted GitHub repositories")
    
    # Also include GitLab repos that don't have GitHub matches
    # (if github_owner is provided, create mappings even if repo doesn't exist yet)
    for repo_name, gitlab_paths in gitlab_by_name.items():
        for gitlab_path in gitlab_paths:
            # Check if this GitLab repo was already matched
            already_matched = any(m["gitlab"] == gitlab_path for m in mappings)
            if not already_matched:
                # Try to construct GitHub repo name
                if github_owner:
                    github_repo = f"{github_owner}/{repo_name}"
                    # Check if repo exists
                    repo_exists = False
                    try:
                        github_repo_data = cli_wrapper.gh_repo_view(github_repo)
                        if github_repo_data:
                            repo_exists = True
                            logger.info(f"✓ Matched: {gitlab_path} <-> {github_repo}")
                    except cli_wrapper.CLIError as e:
                        error_msg = str(e).lower()
                        if "not found" in error_msg or "404" in error_msg or "could not resolve" in error_msg:
                            repo_exists = False
                        else:
                            # Re-raise if it's a different error
                            raise
                    
                    # If repo doesn't exist, try to create it (if create_missing is enabled)
                    if not repo_exists and create_missing:
                        logger.info(f"GitHub repo doesn't exist: {github_repo}. Creating it...")
                        try:
                            # Get GitLab repo info to copy description/homepage
                            gitlab_repo_data = cli_wrapper.glab_repo_view(gitlab_path)
                            description = gitlab_repo_data.get("description", "") if gitlab_repo_data else ""
                            homepage = gitlab_repo_data.get("web_url", "") if gitlab_repo_data else ""
                            visibility = gitlab_repo_data.get("visibility", "private") if gitlab_repo_data else "private"
                            
                            created_repo = cli_wrapper.gh_repo_create(
                                github_repo,
                                description=description[:500] if description else None,  # GitHub has 500 char limit
                                homepage=homepage if homepage else None,
                                private=(visibility == "private")
                            )
                            
                            if created_repo:
                                logger.info(f"✓ Created GitHub repo: {github_repo}")
                                repo_exists = True
                            else:
                                logger.warning(f"Failed to create GitHub repo: {github_repo}")
                        except Exception as e:
                            logger.warning(f"Failed to create GitHub repo {github_repo}: {e}")
                    elif not repo_exists:
                        logger.info(f"→ Mapping created: {gitlab_path} <-> {github_repo} (GitHub repo doesn't exist yet, use --create-missing to auto-create)")
                    
                    # Create mapping
                    mappings.append({
                        "gitlab": gitlab_path,
                        "github": github_repo
                    })
                    
                    if not repo_exists:
                        logger.info(f"→ Mapping created: {gitlab_path} <-> {github_repo} (repo will be created or synced)")
    
    logger.info(f"Created {len(mappings)} repository mappings")
    
    # Show detailed summary
    matched_gitlab = set(m["gitlab"] for m in mappings)
    matched_github = set(m["github"] for m in mappings)
    
    # Get all GitLab repos (including blacklisted for reporting)
    all_gitlab_repos = [r.get("path_with_namespace") or r.get("path", "") for r in gitlab_repos]
    unmatched_gitlab = [repo for repo in all_gitlab_repos if repo not in matched_gitlab]
    
    # Get all GitHub repos (including blacklisted for reporting)
    all_github_repos = [r.get("nameWithOwner", "") for r in github_repos]
    unmatched_github = [repo for repo in all_github_repos if repo not in matched_github]
    
    if len(mappings) == 0:
        logger.warning("No repository mappings created!")
        
        # Check if all repos were blacklisted
        gitlab_blacklisted_count = sum(1 for r in gitlab_repos 
                                      if blacklist and is_blacklisted(
                                          r.get("path_with_namespace") or r.get("path", ""), blacklist))
        github_blacklisted_count = sum(1 for r in github_repos 
                                      if blacklist and is_blacklisted(
                                          r.get("nameWithOwner", ""), blacklist))
        
        if gitlab_blacklisted_count == len(gitlab_repos) and len(gitlab_repos) > 0:
            logger.warning(f"All {len(gitlab_repos)} GitLab repositories are blacklisted!")
            logger.info("Tip: Remove repos from blacklist or set gitlab_owner to create GitLab repos for GitHub repos")
        
        if github_blacklisted_count == len(github_repos) and len(github_repos) > 0:
            logger.warning(f"All {len(github_repos)} GitHub repositories are blacklisted!")
        
        gitlab_names = [r.get("path") or r.get("name", "") for r in gitlab_repos[:5] 
                       if not (blacklist and is_blacklisted(
                           r.get("path_with_namespace") or r.get("path", ""), blacklist))]
        github_names = [r.get("name", "") for r in github_repos[:5]
                       if not (blacklist and is_blacklisted(
                           r.get("nameWithOwner", ""), blacklist))]
        
        if gitlab_names:
            logger.info(f"Sample GitLab repos (not blacklisted): {', '.join(gitlab_names)}")
        if github_names:
            logger.info(f"Sample GitHub repos (not blacklisted): {', '.join(github_names)}")
        
        if not gitlab_owner and not github_owner:
            logger.info("Tip: Set github_owner or gitlab_owner in config.yaml to create mappings")
        elif not create_missing:
            logger.info("Tip: Enable create_missing in config.yaml to auto-create missing repositories")
    else:
        logger.info(f"\nSummary:")
        logger.info(f"  Matched: {len(mappings)} repository pairs")
        if unmatched_gitlab:
            logger.info(f"  Unmatched GitLab repos: {len(unmatched_gitlab)}")
            if len(unmatched_gitlab) <= 10:
                for repo in unmatched_gitlab:
                    logger.info(f"    - {repo}")
            else:
                for repo in unmatched_gitlab[:5]:
                    logger.info(f"    - {repo}")
                logger.info(f"    ... and {len(unmatched_gitlab) - 5} more")
        if unmatched_github:
            logger.info(f"  Unmatched GitHub repos: {len(unmatched_github)}")
            if len(unmatched_github) <= 10:
                for repo in unmatched_github[:10]:
                    logger.info(f"    - {repo}")
            else:
                for repo in unmatched_github[:5]:
                    logger.info(f"    - {repo}")
                logger.info(f"    ... and {len(unmatched_github) - 5} more")
        
        if unmatched_gitlab and not github_owner:
            logger.info(f"\nTip: Use --github-owner to create mappings for {len(unmatched_gitlab)} unmatched GitLab repos")
        if unmatched_github:
            logger.info(f"Tip: Create GitLab repos with matching names to sync {len(unmatched_github)} GitHub repos")
    
    return mappings


def validate_config(config: dict) -> bool:
    """
    Validate configuration structure.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        True if valid, False otherwise
    """
    # If sync_all is enabled, repositories list is optional
    sync_all = config.get("sync_mode", {}).get("sync_all", False)
    
    if not sync_all:
        # For manual mode, repositories are required
        if "repositories" not in config:
            logger.error("Missing required configuration key: repositories (or enable sync_mode.sync_all)")
            return False
        
        if not isinstance(config["repositories"], list):
            logger.error("'repositories' must be a list")
            return False
        
        for repo in config["repositories"]:
            if not isinstance(repo, dict):
                logger.error("Each repository entry must be a dictionary")
                return False
            if "gitlab" not in repo or "github" not in repo:
                logger.error("Each repository must have 'gitlab' and 'github' keys")
                return False
    
    return True


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Sync repositories between GitLab and GitHub",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)"
    )
    parser.add_argument(
        "-d", "--dry-run",
        action="store_true",
        help="Perform a dry run without making changes"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    parser.add_argument(
        "-r", "--repo",
        help="Sync only a specific repository (format: gitlab_repo:github_repo)"
    )
    parser.add_argument(
        "--sync-all",
        action="store_true",
        help="Sync all repositories (auto-discover from GitLab and GitHub)"
    )
    parser.add_argument(
        "--gitlab-owner",
        help="GitLab group/namespace to sync from (for --sync-all mode)"
    )
    parser.add_argument(
        "--github-owner",
        help="GitHub username or organization to sync from (for --sync-all mode)"
    )
    parser.add_argument(
        "--create-missing",
        action="store_true",
        help="Automatically create GitHub repositories if they don't exist (for --sync-all mode)"
    )
    
    args = parser.parse_args()
    
    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Show dry-run message early
    if args.dry_run:
        logger.info("DRY RUN MODE: No changes will be made")
    
    # Load configuration
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)
    except yaml.YAMLError as e:
        logger.error(f"Error parsing configuration file: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        sys.exit(1)
    
    # Handle sync-all mode (from args or config)
    sync_all = args.sync_all or config.get("sync_mode", {}).get("sync_all", False)
    gitlab_owner = args.gitlab_owner or config.get("sync_mode", {}).get("gitlab_owner")
    github_owner = args.github_owner or config.get("sync_mode", {}).get("github_owner")
    create_missing = args.create_missing or config.get("sync_mode", {}).get("create_missing", False)
    
    if sync_all:
        logger.info("SYNC ALL MODE: Auto-discovering repositories...")
        if create_missing:
            if not gitlab_owner and not github_owner:
                logger.error("create_missing requires at least one of gitlab_owner or github_owner in config")
                sys.exit(1)
        
        # Log configuration being used
        if gitlab_owner:
            logger.info(f"GitLab owner: {gitlab_owner}")
        if github_owner:
            logger.info(f"GitHub owner: {github_owner}")
        if create_missing:
            logger.info("Auto-create missing repositories: enabled")
        
        repositories = discover_repositories(
            gitlab_owner=gitlab_owner,
            github_owner=github_owner,
            create_missing=create_missing,
            blacklist=config.get("blacklist", {})
        )
        if not repositories:
            logger.error("No repositories found or failed to discover repositories")
            sys.exit(1)
        logger.info(f"Will sync {len(repositories)} repository pairs")
        # Ensure sync_options exist
        if "sync_options" not in config:
            config["sync_options"] = {
                "metadata": True,
                "code": True,
                "issues": True,
                "mrs": True
            }
    else:
        # Validate configuration
        if not validate_config(config):
            logger.error("Invalid configuration")
            sys.exit(1)
        
        # Get repositories to sync
        repositories = config["repositories"]
        
        if args.repo:
            # Sync specific repository
            parts = args.repo.split(":", 1)
            if len(parts) != 2:
                logger.error("Invalid repository format. Use: gitlab_repo:github_repo")
                sys.exit(1)
            gitlab_repo, github_repo = parts
            repositories = [{"gitlab": gitlab_repo, "github": github_repo}]
    
    # Initialize state manager
    state_file = config.get("state_file", ".sync_state.json")
    state_manager = StateManager(state_file)
    
    # Initialize sync engine
    sync_engine = SyncEngine(config, state_manager, dry_run=args.dry_run)
    
    # Apply blacklist to final repository list
    blacklist_config = config.get("blacklist", {})
    if blacklist_config:
        original_count = len(repositories)
        filtered_repositories = []
        for repo_config in repositories:
            gitlab_repo = repo_config["gitlab"]
            github_repo = repo_config["github"]
            if is_blacklisted(gitlab_repo, blacklist_config) or is_blacklisted(github_repo, blacklist_config):
                logger.info(f"Skipping blacklisted repository: {gitlab_repo} <-> {github_repo}")
                continue
            filtered_repositories.append(repo_config)
        repositories = filtered_repositories
        filtered_count = original_count - len(repositories)
        if filtered_count > 0:
            logger.info(f"Filtered {filtered_count} blacklisted repositories")
    
    # Sync each repository with progress bar
    success_count = 0
    error_count = 0
    skipped_count = 0
    
    # Try to import tqdm for progress bar, fallback to no progress bar if not available
    try:
        from tqdm import tqdm
        use_progress = True
    except ImportError:
        logger.warning("tqdm not installed. Install with: pip install tqdm")
        use_progress = False
        # Create a dummy tqdm class that does nothing
        class tqdm:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def set_description(self, *args, **kwargs):
                pass
            def set_postfix(self, *args, **kwargs):
                pass
            def update(self, *args, **kwargs):
                pass
            def write(self, *args, **kwargs):
                print(*args, **kwargs)
    
    with tqdm(total=len(repositories), desc="Syncing repositories", unit="repo", 
              bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
              disable=not use_progress) as pbar:
        # Connect tqdm to logging handler so log messages use tqdm.write()
        if use_progress:
            tqdm_handler.set_tqdm(pbar)
        
        try:
            for repo_config in repositories:
                gitlab_repo = repo_config["gitlab"]
                github_repo = repo_config["github"]

                # Update progress bar description with current repo
                repo_display = f"{gitlab_repo.split('/')[-1]}"
                if use_progress:
                    pbar.set_description(f"Syncing {repo_display}")
                
                try:
                    result = sync_engine.sync_repository(gitlab_repo, github_repo)
                    if result is True:
                        success_count += 1
                        if use_progress:
                            pbar.set_postfix({"status": "✓", "success": success_count, "errors": error_count})
                    elif result is None:
                        skipped_count += 1
                        if use_progress:
                            pbar.set_postfix({"status": "⊘", "skipped": skipped_count, "errors": error_count})
                    else:
                        error_count += 1
                        if use_progress:
                            pbar.set_postfix({"status": "✗", "success": success_count, "errors": error_count})
                except Exception as e:
                    logger.error(f"Unexpected error syncing {gitlab_repo} <-> {github_repo}: {e}", exc_info=True)
                    error_count += 1
                    if use_progress:
                        pbar.set_postfix({"status": "✗", "success": success_count, "errors": error_count})
                
                pbar.update(1)
        finally:
            # Disconnect tqdm from logging handler
            if use_progress:
                tqdm_handler.set_tqdm(None)
    
    # Summary
    summary_parts = []
    if success_count > 0:
        summary_parts.append(f"{success_count} successful")
    if skipped_count > 0:
        summary_parts.append(f"{skipped_count} skipped")
    if error_count > 0:
        summary_parts.append(f"{error_count} errors")
    
    if summary_parts:
        logger.info(f"Sync completed: {', '.join(summary_parts)}")
    else:
        logger.info("Sync completed: no repositories processed")
    
    if error_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
