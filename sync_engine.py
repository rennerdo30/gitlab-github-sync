"""Core sync engine for bidirectional synchronization between GitLab and GitHub."""

import os
import subprocess
import logging
import json
from typing import Dict, List, Optional, Any, Union
from pathlib import Path

import cli_wrapper
from state_manager import StateManager

logger = logging.getLogger(__name__)

# GitLab API paths used for code mirroring, relative to projects/<id>/.
# Push mirrors: all tiers. Pull mirrors: Premium and above, different endpoint.
# https://docs.gitlab.com/api/remote_mirrors/
# https://docs.gitlab.com/api/project_pull_mirroring/
PUSH_MIRROR_ENDPOINT = "remote_mirrors"
PULL_MIRROR_ENDPOINT = "mirror/pull"

# Substrings that mean "this push mirror is already configured" rather than a failure.
PUSH_MIRROR_EXISTS_MARKERS = ("already", "exists", "duplicate", "has already been taken")

# Substrings that mean pull mirroring is not available on this GitLab tier/plan.
PULL_MIRROR_UNAVAILABLE_MARKERS = ("404", "not found", "403", "forbidden", "premium", "license")


class SyncEngine:
    """Handles bidirectional synchronization between GitLab and GitHub."""
    
    def __init__(self, config: Dict[str, Any], state_manager: StateManager, dry_run: bool = False):
        """
        Initialize the sync engine.
        
        Args:
            config: Configuration dictionary
            state_manager: StateManager instance
            dry_run: If True, don't make actual changes
        """
        self.config = config
        self.state_manager = state_manager
        self.dry_run = dry_run
        self.work_dir = Path(config.get("work_dir", ".sync_work"))
        self.work_dir.mkdir(exist_ok=True)
    
    def sync_repository(self, gitlab_repo: str, github_repo: str) -> Optional[bool]:
        """
        Sync a repository pair bidirectionally.
        
        Args:
            gitlab_repo: GitLab repository (e.g., "group/project")
            github_repo: GitHub repository (e.g., "owner/repo")
            
        Returns:
            True if sync was successful and at least one component synced,
            None if repository was skipped (doesn't exist),
            False if sync failed
        """
        repo_key = f"{gitlab_repo}:{github_repo}"
        logger.info(f"Starting sync for {repo_key}")
        
        try:
            # First, validate that repositories exist
            try:
                gitlab_data = cli_wrapper.glab_repo_view(gitlab_repo)
                if not gitlab_data:
                    logger.error(f"GitLab repository not found or inaccessible: {gitlab_repo}")
                    return None  # Return None to indicate skip (not error)
            except cli_wrapper.CLIError as e:
                if e.is_not_found:
                    logger.error(f"GitLab repository not found: {gitlab_repo}")
                    return None  # Return None to indicate skip (not error)
                raise
            
            try:
                github_data = cli_wrapper.gh_repo_view(github_repo)
                if not github_data:
                    logger.warning(f"GitHub repository not found or inaccessible: {github_repo}. Skipping sync.")
                    return None  # Return None to indicate skip (not error)
            except cli_wrapper.CLIError as e:
                error_msg = str(e).lower()
                if "not found" in error_msg or "404" in error_msg or "could not resolve" in error_msg:
                    logger.warning(f"GitHub repository not found: {github_repo}. Skipping sync.")
                    return None  # Return None to indicate skip (not error)
                raise
            
            # Track if any sync actually happened
            sync_success = False
            sync_options = self.config.get("sync_options", {})
            
            # Sync metadata (description, URL, topics)
            if sync_options.get("metadata", True):
                if self.sync_metadata(gitlab_repo, github_repo, repo_key):
                    sync_success = True
            
            # Sync code (branches, tags)
            if sync_options.get("code", True):
                if self.sync_code(gitlab_repo, github_repo, repo_key):
                    sync_success = True
            
            # Sync issues
            if sync_options.get("issues", True):
                if self.sync_issues(gitlab_repo, github_repo, repo_key):
                    sync_success = True
            
            # Sync MRs/PRs
            if sync_options.get("mrs", True):
                if self.sync_mrs(gitlab_repo, github_repo, repo_key):
                    sync_success = True
            
            if sync_success:
                self.state_manager.update_last_sync(repo_key, "full")
                logger.info(f"Completed sync for {repo_key}")
            else:
                logger.warning(f"No sync operations completed for {repo_key}")
            
            return sync_success
            
        except Exception as e:
            logger.error(f"Error syncing {repo_key}: {e}", exc_info=True)
            return False
    
    def sync_metadata(self, gitlab_repo: str, github_repo: str, repo_key: str) -> bool:
        """Sync repository metadata (description, URL, topics).
        
        Returns:
            True if metadata was synced, False otherwise
        """
        logger.info(f"Syncing metadata for {repo_key}")
        
        try:
            # Get current state from both platforms
            gitlab_data = cli_wrapper.glab_repo_view(gitlab_repo)
            github_data = cli_wrapper.gh_repo_view(github_repo)
            
            if not gitlab_data or not github_data:
                logger.warning(f"Could not fetch metadata for {repo_key}")
                return False
            
            # Extract topics/tags
            gitlab_topics = gitlab_data.get("topics", []) or []
            github_topics = []
            if github_data.get("repositoryTopics"):
                topics_data = github_data["repositoryTopics"]
                # GitHub API returns repositoryTopics as a list: [{"name": "topic1"}, {"name": "topic2"}]
                if isinstance(topics_data, list):
                    github_topics = [t.get("name") for t in topics_data if t.get("name")]
                elif isinstance(topics_data, dict) and "nodes" in topics_data:
                    # GraphQL format: {"nodes": [{"topic": {"name": "topic1"}}]}
                    github_topics = [t.get("topic", {}).get("name") for t in topics_data["nodes"] if t.get("topic", {}).get("name")]
            
            gitlab_desc = gitlab_data.get("description", "")
            github_desc = github_data.get("description", "")
            gitlab_homepage = gitlab_data.get("webUrl", "")
            github_homepage = github_data.get("homepageUrl", "")
            
            # Sync from GitLab to GitHub
            if not self.dry_run:
                updated = False
                if gitlab_desc != github_desc:
                    cli_wrapper.gh_repo_edit(github_repo, description=gitlab_desc)
                    updated = True
                if gitlab_homepage and gitlab_homepage != github_homepage:
                    cli_wrapper.gh_repo_edit(github_repo, homepage=gitlab_homepage)
                    updated = True
                
                # Sync topics
                topics_to_add = set(gitlab_topics) - set(github_topics)
                if topics_to_add:
                    cli_wrapper.gh_repo_edit(github_repo, add_topic=list(topics_to_add))
                    updated = True
                
                if updated:
                    logger.info(f"Updated GitHub metadata for {github_repo}")
            else:
                logger.info(f"[DRY RUN] Would update GitHub metadata for {github_repo}")
            
            # Sync from GitHub to GitLab
            if not self.dry_run:
                updated = False
                if github_desc and github_desc != gitlab_desc:
                    cli_wrapper.glab_repo_update(gitlab_repo, description=github_desc)
                    updated = True
                if github_homepage and github_homepage != gitlab_homepage:
                    cli_wrapper.glab_repo_update(gitlab_repo, homepage=github_homepage)
                    updated = True
                
                # Sync topics
                topics_to_add = set(github_topics) - set(gitlab_topics)
                if topics_to_add:
                    all_topics = list(set(gitlab_topics + list(topics_to_add)))
                    cli_wrapper.glab_repo_update(gitlab_repo, topics=all_topics)
                    updated = True
                
                if updated:
                    logger.info(f"Updated GitLab metadata for {gitlab_repo}")
            else:
                logger.info(f"[DRY RUN] Would update GitLab metadata for {gitlab_repo}")
            
            self.state_manager.update_last_sync(repo_key, "metadata")
            return True
            
        except Exception as e:
            logger.error(f"Error syncing metadata for {repo_key}: {e}", exc_info=True)
            return False
    
    def sync_code(self, gitlab_repo: str, github_repo: str, repo_key: str) -> bool:
        """Sync code (branches, tags) using GitLab native mirroring.
        
        This method sets up GitLab push/pull mirrors to automatically sync code
        between GitLab and GitHub, eliminating the need for manual git operations.
        
        Returns:
            True if mirrors were set up or already configured, False otherwise
        """
        logger.info(f"Setting up code sync (mirrors) for {repo_key}")
        
        # Check if we should use native mirroring (default: yes)
        use_mirrors = self.config.get("sync_options", {}).get("use_native_mirrors", True)
        
        if not use_mirrors:
            logger.warning(f"Native mirrors disabled in config. Skipping code sync for {repo_key}")
            logger.warning("To enable native mirrors, set sync_options.use_native_mirrors: true in config.yaml")
            return False
        
        try:
            # Get GitHub token for mirror authentication
            github_token = None
            try:
                result = subprocess.run(
                    ["gh", "auth", "token"],
                    capture_output=True,
                    text=True,
                    check=True
                )
                github_token = result.stdout.strip()
            except Exception as e:
                logger.warning(f"Could not get GitHub token: {e}")
                logger.warning("Code sync via mirrors requires GitHub authentication. Run: gh auth login")
                return False
            
            github_url = f"https://{github_token}@github.com/{github_repo}.git"
            
            if self.dry_run:
                logger.info(f"[DRY RUN] Would set up push mirror: {gitlab_repo} -> {github_repo}")
                logger.info(f"[DRY RUN] Would set up pull mirror: {gitlab_repo} <- {github_repo}")
                return True
            
            # Get GitLab project ID first
            gitlab_project_id = None
            try:
                # URL encode the repo path
                repo_path_encoded = gitlab_repo.replace("/", "%2F")
                cmd = ["glab", "api", f"projects/{repo_path_encoded}"]
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                project_data = json.loads(result.stdout)
                gitlab_project_id = project_data.get("id")
                if not gitlab_project_id:
                    logger.warning(f"Could not get project ID for {gitlab_repo}")
                    return False
            except Exception as e:
                logger.warning(f"Failed to get GitLab project ID: {e}")
                return False
            
            # Set up bidirectional mirrors using GitLab API
            mirrors_setup = True
            
            # Push mirror: GitLab -> GitHub
            try:
                logger.info(f"Setting up push mirror: {gitlab_repo} -> {github_repo}")
                # Use GitLab API to create push mirror
                cmd = [
                    "glab", "api", "-X", "POST",
                    f"projects/{gitlab_project_id}/{PUSH_MIRROR_ENDPOINT}",
                    "-f", f"url={github_url}",
                    "-f", "mirror_direction=push",
                    "-f", "enabled=true"
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if result.returncode == 0:
                    logger.info(f"✓ Push mirror configured: {gitlab_repo} -> {github_repo}")
                else:
                    # Check if mirror already exists
                    error_output = result.stderr.lower() + result.stdout.lower()
                    if any(keyword in error_output for keyword in PUSH_MIRROR_EXISTS_MARKERS):
                        logger.info(f"Push mirror already configured: {gitlab_repo} -> {github_repo}")
                    else:
                        logger.warning(f"Failed to set up push mirror: {result.stderr or result.stdout}")
                        mirrors_setup = False
            except Exception as e:
                logger.warning(f"Error setting up push mirror: {e}")
                mirrors_setup = False
            
            # Pull mirror: GitHub -> GitLab
            #
            # Pull mirrors are NOT part of the remote_mirrors API (that endpoint is
            # push-only). They live behind PUT /projects/:id/mirror/pull, which is a
            # Premium/Ultimate feature. The PUT is idempotent, so re-running is safe.
            # See https://docs.gitlab.com/api/project_pull_mirroring/
            try:
                logger.info(f"Setting up pull mirror: {gitlab_repo} <- {github_repo}")
                cmd = [
                    "glab", "api", "-X", "PUT",
                    f"projects/{gitlab_project_id}/{PULL_MIRROR_ENDPOINT}",
                    "-f", f"url={github_url}",
                    "-f", "enabled=true"
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if result.returncode == 0:
                    logger.info(f"✓ Pull mirror configured: {gitlab_repo} <- {github_repo}")
                else:
                    error_output = result.stderr.lower() + result.stdout.lower()
                    if any(keyword in error_output for keyword in PULL_MIRROR_UNAVAILABLE_MARKERS):
                        logger.warning(
                            f"Pull mirroring is unavailable for {gitlab_repo} "
                            "(it requires GitLab Premium or higher). "
                            "Code will only be mirrored GitLab -> GitHub."
                        )
                    else:
                        logger.warning(f"Failed to set up pull mirror: {result.stderr or result.stdout}")
                        mirrors_setup = False
            except Exception as e:
                logger.warning(f"Error setting up pull mirror: {e}")
                mirrors_setup = False
            
            if mirrors_setup:
                self.state_manager.update_last_sync(repo_key, "code")
                logger.info(f"✓ Code sync configured via native mirrors for {repo_key}")
                logger.info("  Note: GitLab will automatically sync code on push/pull. No manual git operations needed.")
                return True
            else:
                logger.warning(f"Some mirrors failed to set up for {repo_key}. Code sync may be incomplete.")
                return False
            
        except Exception as e:
            logger.error(f"Error setting up code sync mirrors for {repo_key}: {e}", exc_info=True)
            return False
    
    def sync_issues(self, gitlab_repo: str, github_repo: str, repo_key: str) -> bool:
        """Sync issues bidirectionally.
        
        Returns:
            True if issues were synced, False otherwise
        """
        logger.info(f"Syncing issues for {repo_key}")
        
        try:
            # Get issues from both platforms
            gitlab_issues = cli_wrapper.glab_issue_list(gitlab_repo, state="all")
            github_issues = cli_wrapper.gh_issue_list(github_repo, state="all")
            
            # Create lookup dictionaries
            gitlab_issues_by_id = {issue.get("iid"): issue for issue in gitlab_issues if issue.get("iid")}
            github_issues_by_number = {issue.get("number"): issue for issue in github_issues if issue.get("number")}
            
            # Sync GitLab issues to GitHub
            for gitlab_issue_id, gitlab_issue in gitlab_issues_by_id.items():
                # Check if already synced
                github_issue_id = self.state_manager.get_issue_mapping(repo_key, gitlab_issue_id=gitlab_issue_id)
                
                if github_issue_id:
                    # Update existing issue
                    github_issue = github_issues_by_number.get(github_issue_id)
                    if github_issue:
                        if not self.dry_run:
                            cli_wrapper.gh_issue_edit(
                                github_repo,
                                github_issue_id,
                                title=gitlab_issue.get("title"),
                                body=gitlab_issue.get("description", ""),
                                state="closed" if gitlab_issue.get("state") == "closed" else "open"
                            )
                        logger.debug(f"Updated GitHub issue #{github_issue_id} from GitLab issue #{gitlab_issue_id}")
                else:
                    # Create new issue
                    if not self.dry_run:
                        result = cli_wrapper.gh_issue_create(
                            github_repo,
                            title=gitlab_issue.get("title", ""),
                            body=gitlab_issue.get("description", ""),
                            labels=gitlab_issue.get("labels", [])
                        )
                        if result and result.get("number"):
                            github_issue_id = result["number"]
                            self.state_manager.map_issue(repo_key, gitlab_issue_id, github_issue_id)
                            logger.info(f"Created GitHub issue #{github_issue_id} from GitLab issue #{gitlab_issue_id}")
                    else:
                        logger.info(f"[DRY RUN] Would create GitHub issue from GitLab issue #{gitlab_issue_id}")
            
            # Sync GitHub issues to GitLab
            for github_issue_number, github_issue in github_issues_by_number.items():
                # Check if already synced
                gitlab_issue_id = self.state_manager.get_issue_mapping(repo_key, github_issue_id=github_issue_number)
                
                if gitlab_issue_id:
                    # Update existing issue
                    gitlab_issue = gitlab_issues_by_id.get(gitlab_issue_id)
                    if gitlab_issue:
                        if not self.dry_run:
                            cli_wrapper.glab_issue_update(
                                gitlab_repo,
                                gitlab_issue_id,
                                title=github_issue.get("title"),
                                description=github_issue.get("body", ""),
                                state="closed" if github_issue.get("state") == "CLOSED" else "opened"
                            )
                        logger.debug(f"Updated GitLab issue #{gitlab_issue_id} from GitHub issue #{github_issue_number}")
                else:
                    # Create new issue
                    if not self.dry_run:
                        result = cli_wrapper.glab_issue_create(
                            gitlab_repo,
                            title=github_issue.get("title", ""),
                            description=github_issue.get("body", ""),
                            labels=[label.get("name") for label in github_issue.get("labels", [])]
                        )
                        if result and result.get("iid"):
                            gitlab_issue_id = result["iid"]
                            self.state_manager.map_issue(repo_key, gitlab_issue_id, github_issue_number)
                            logger.info(f"Created GitLab issue #{gitlab_issue_id} from GitHub issue #{github_issue_number}")
                    else:
                        logger.info(f"[DRY RUN] Would create GitLab issue from GitHub issue #{github_issue_number}")
            
            self.state_manager.update_last_sync(repo_key, "issues")
            logger.info(f"Completed issues sync for {repo_key}")
            return True
            
        except Exception as e:
            logger.error(f"Error syncing issues for {repo_key}: {e}", exc_info=True)
            return False
    
    def sync_mrs(self, gitlab_repo: str, github_repo: str, repo_key: str) -> bool:
        """Sync merge requests/pull requests bidirectionally.
        
        Returns:
            True if MRs/PRs were synced, False otherwise
        """
        logger.info(f"Syncing MRs/PRs for {repo_key}")
        
        try:
            # Get MRs/PRs from both platforms
            gitlab_mrs = cli_wrapper.glab_mr_list(gitlab_repo, state="all")
            github_prs = cli_wrapper.gh_pr_list(github_repo, state="all")
            
            # Create lookup dictionaries
            gitlab_mrs_by_id = {mr.get("iid"): mr for mr in gitlab_mrs if mr.get("iid")}
            github_prs_by_number = {pr.get("number"): pr for pr in github_prs if pr.get("number")}
            
            # Sync GitLab MRs to GitHub
            for gitlab_mr_id, gitlab_mr in gitlab_mrs_by_id.items():
                # Check if already synced
                github_pr_id = self.state_manager.get_mr_mapping(repo_key, gitlab_mr_id=gitlab_mr_id)
                
                if github_pr_id:
                    # Update existing PR
                    github_pr = github_prs_by_number.get(github_pr_id)
                    if github_pr:
                        if not self.dry_run:
                            state = "closed" if gitlab_mr.get("state") == "merged" else "open"
                            cli_wrapper.gh_pr_edit(
                                github_repo,
                                github_pr_id,
                                title=gitlab_mr.get("title"),
                                body=gitlab_mr.get("description", ""),
                                state=state
                            )
                        logger.debug(f"Updated GitHub PR #{github_pr_id} from GitLab MR #{gitlab_mr_id}")
                else:
                    # Create new PR (requires branches to exist)
                    source_branch = gitlab_mr.get("source_branch")
                    target_branch = gitlab_mr.get("target_branch", "main")
                    
                    if source_branch and not self.dry_run:
                        result = cli_wrapper.gh_pr_create(
                            github_repo,
                            title=gitlab_mr.get("title", ""),
                            body=gitlab_mr.get("description", ""),
                            head=source_branch,
                            base=target_branch
                        )
                        if result and result.get("number"):
                            github_pr_id = result["number"]
                            self.state_manager.map_mr(repo_key, gitlab_mr_id, github_pr_id)
                            logger.info(f"Created GitHub PR #{github_pr_id} from GitLab MR #{gitlab_mr_id}")
                    elif self.dry_run:
                        logger.info(f"[DRY RUN] Would create GitHub PR from GitLab MR #{gitlab_mr_id}")
            
            # Sync GitHub PRs to GitLab
            for github_pr_number, github_pr in github_prs_by_number.items():
                # Check if already synced
                gitlab_mr_id = self.state_manager.get_mr_mapping(repo_key, github_pr_id=github_pr_number)
                
                if gitlab_mr_id:
                    # Update existing MR
                    gitlab_mr = gitlab_mrs_by_id.get(gitlab_mr_id)
                    if gitlab_mr:
                        if not self.dry_run:
                            state = "merged" if github_pr.get("state") == "MERGED" else "opened"
                            cli_wrapper.glab_mr_update(
                                gitlab_repo,
                                gitlab_mr_id,
                                title=github_pr.get("title"),
                                description=github_pr.get("body", ""),
                                state=state
                            )
                        logger.debug(f"Updated GitLab MR #{gitlab_mr_id} from GitHub PR #{github_pr_number}")
                else:
                    # Create new MR (requires branches to exist)
                    source_branch = github_pr.get("headRefName")
                    target_branch = github_pr.get("baseRefName", "main")
                    
                    if source_branch and not self.dry_run:
                        result = cli_wrapper.glab_mr_create(
                            gitlab_repo,
                            title=github_pr.get("title", ""),
                            description=github_pr.get("body", ""),
                            source_branch=source_branch,
                            target_branch=target_branch
                        )
                        if result and result.get("iid"):
                            gitlab_mr_id = result["iid"]
                            self.state_manager.map_mr(repo_key, gitlab_mr_id, github_pr_number)
                            logger.info(f"Created GitLab MR #{gitlab_mr_id} from GitHub PR #{github_pr_number}")
                    elif self.dry_run:
                        logger.info(f"[DRY RUN] Would create GitLab MR from GitHub PR #{github_pr_number}")
            
            self.state_manager.update_last_sync(repo_key, "mrs")
            logger.info(f"Completed MRs/PRs sync for {repo_key}")
            return True
            
        except Exception as e:
            logger.error(f"Error syncing MRs/PRs for {repo_key}: {e}", exc_info=True)
            return False