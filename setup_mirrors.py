#!/usr/bin/env python3
"""Setup automatic mirroring between GitLab and GitHub repositories."""

import argparse
import logging
import sys
import subprocess
from typing import List, Dict

import yaml
from pathlib import Path

import cli_wrapper


def load_config(config_path: str) -> dict:
    """Load configuration from YAML file."""
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
    
    if not config:
        raise ValueError("Configuration file is empty or invalid")
    
    return config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def setup_gitlab_push_mirror(gitlab_repo: str, github_repo: str, github_token: str = None) -> bool:
    """
    Set up a GitLab push mirror to GitHub.
    
    Args:
        gitlab_repo: GitLab repository (e.g., "group/project")
        github_repo: GitHub repository (e.g., "owner/repo")
        github_token: GitHub personal access token (optional, will prompt if needed)
        
    Returns:
        True if successful
    """
    # GitHub URL with token for authentication
    if github_token:
        github_url = f"https://{github_token}@github.com/{github_repo}.git"
    else:
        # Try to get token from gh CLI
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                check=True
            )
            github_token = result.stdout.strip()
            github_url = f"https://{github_token}@github.com/{github_repo}.git"
        except Exception as e:
            logger.error(f"Failed to get GitHub token: {e}")
            logger.error("Please provide a GitHub personal access token with repo permissions")
            return False
    
    try:
        logger.info(f"Setting up GitLab push mirror: {gitlab_repo} -> {github_repo}")
        # Use glab CLI to set up push mirror
        cmd = [
            "glab", "repo", "mirror", gitlab_repo,
            "--url", github_url,
            "--direction", "push",
            "--enabled=true"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.info(f"✓ Push mirror configured: {gitlab_repo} -> {github_repo}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to set up push mirror: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"Error setting up push mirror: {e}")
        return False


def setup_gitlab_pull_mirror(gitlab_repo: str, github_repo: str, github_token: str = None) -> bool:
    """
    Set up a GitLab pull mirror from GitHub.
    
    Args:
        gitlab_repo: GitLab repository (e.g., "group/project")
        github_repo: GitHub repository (e.g., "owner/repo")
        github_token: GitHub personal access token (optional)
        
    Returns:
        True if successful
    """
    if github_token:
        github_url = f"https://{github_token}@github.com/{github_repo}.git"
    else:
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                check=True
            )
            github_token = result.stdout.strip()
            github_url = f"https://{github_token}@github.com/{github_repo}.git"
        except Exception as e:
            logger.error(f"Failed to get GitHub token: {e}")
            return False
    
    try:
        logger.info(f"Setting up GitLab pull mirror: {gitlab_repo} <- {github_repo}")
        cmd = [
            "glab", "repo", "mirror", gitlab_repo,
            "--url", github_url,
            "--direction", "pull",
            "--enabled=true"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.info(f"✓ Pull mirror configured: {gitlab_repo} <- {github_repo}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to set up pull mirror: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"Error setting up pull mirror: {e}")
        return False


def create_github_action_workflow(github_repo: str, gitlab_repo: str, gitlab_token: str = None) -> bool:
    """
    Create a GitHub Actions workflow to sync to GitLab.
    
    Note: This creates the workflow file locally. You need to commit and push it.
    
    Args:
        github_repo: GitHub repository (e.g., "owner/repo")
        gitlab_repo: GitLab repository (e.g., "group/project")
        gitlab_token: GitLab personal access token (optional)
        
    Returns:
        True if successful
    """
    logger.info(f"Creating GitHub Actions workflow for {github_repo} -> {gitlab_repo}")
    logger.warning("Note: You'll need to commit and push the workflow file to activate it")
    
    workflow_content = f"""name: Sync to GitLab

on:
  push:
    branches:
      - '**'
    tags:
      - '**'
  workflow_dispatch:

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
      
      - name: Configure Git
        run: |
          git config user.name "GitHub Actions"
          git config user.email "actions@github.com"
      
      - name: Push to GitLab
        env:
          GITLAB_TOKEN: ${{{{ secrets.GITLAB_TOKEN }}}}
        run: |
          git remote add gitlab https://oauth2:${{{{ secrets.GITLAB_TOKEN }}}}@gitlab.com/{gitlab_repo}.git || true
          git push gitlab --all --force
          git push gitlab --tags --force
"""
    
    # Save workflow file
    workflow_path = f".github/workflows/sync-to-gitlab.yml"
    try:
        import os
        os.makedirs(".github/workflows", exist_ok=True)
        with open(workflow_path, "w") as f:
            f.write(workflow_content)
        logger.info(f"✓ Created workflow file: {workflow_path}")
        logger.info("Next steps:")
        logger.info("  1. Add GITLAB_TOKEN to GitHub repository secrets")
        logger.info("  2. Commit and push the workflow file")
        logger.info("  3. The workflow will run automatically on pushes")
        return True
    except Exception as e:
        logger.error(f"Failed to create workflow file: {e}")
        return False


def setup_mirrors_from_config(config_path: str = "config.yaml", 
                              direction: str = "bidirectional",
                              github_token: str = None,
                              gitlab_token: str = None) -> bool:
    """
    Set up mirrors for all repositories in config.
    
    Args:
        config_path: Path to config file
        direction: "push" (GitLab->GitHub), "pull" (GitHub->GitLab), or "bidirectional"
        github_token: GitHub token (optional)
        gitlab_token: GitLab token (optional)
        
    Returns:
        True if all successful
    """
    try:
        config = load_config(config_path)
        repositories = config.get("repositories", [])
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return False
    
    success_count = 0
    error_count = 0
    
    for repo_config in repositories:
        gitlab_repo = repo_config["gitlab"]
        github_repo = repo_config["github"]
        
        logger.info(f"\nSetting up mirrors for {gitlab_repo} <-> {github_repo}")
        
        if direction in ["push", "bidirectional"]:
            if setup_gitlab_push_mirror(gitlab_repo, github_repo, github_token):
                success_count += 1
            else:
                error_count += 1
        
        if direction in ["pull", "bidirectional"]:
            if setup_gitlab_pull_mirror(gitlab_repo, github_repo, github_token):
                success_count += 1
            else:
                error_count += 1
    
    logger.info(f"\nSetup complete: {success_count} successful, {error_count} errors")
    return error_count == 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Setup automatic mirroring between GitLab and GitHub",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="Path to configuration file"
    )
    parser.add_argument(
        "--direction",
        choices=["push", "pull", "bidirectional"],
        default="bidirectional",
        help="Mirror direction: push (GitLab->GitHub), pull (GitHub->GitLab), or bidirectional"
    )
    parser.add_argument(
        "--github-token",
        help="GitHub personal access token (optional, will try to get from gh CLI)"
    )
    parser.add_argument(
        "--gitlab-token",
        help="GitLab personal access token (optional)"
    )
    parser.add_argument(
        "--repo",
        help="Setup mirror for specific repo (format: gitlab_repo:github_repo)"
    )
    parser.add_argument(
        "--github-action",
        action="store_true",
        help="Create GitHub Actions workflow instead of GitLab mirror"
    )
    
    args = parser.parse_args()
    
    if args.repo:
        # Setup for single repository
        parts = args.repo.split(":", 1)
        if len(parts) != 2:
            logger.error("Invalid format. Use: gitlab_repo:github_repo")
            sys.exit(1)
        gitlab_repo, github_repo = parts
        
        if args.github_action:
            create_github_action_workflow(github_repo, gitlab_repo, args.gitlab_token)
        else:
            if args.direction in ["push", "bidirectional"]:
                setup_gitlab_push_mirror(gitlab_repo, github_repo, args.github_token)
            if args.direction in ["pull", "bidirectional"]:
                setup_gitlab_pull_mirror(gitlab_repo, github_repo, args.github_token)
    else:
        # Setup for all repositories in config
        setup_mirrors_from_config(
            config_path=args.config,
            direction=args.direction,
            github_token=args.github_token,
            gitlab_token=args.gitlab_token
        )


if __name__ == "__main__":
    main()
