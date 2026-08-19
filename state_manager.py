"""State management for tracking sync state and issue/MR mappings."""

import json
import os
from typing import Dict, Optional, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class StateManager:
    """Manages sync state and mappings between GitLab and GitHub."""
    
    def __init__(self, state_file: str = ".sync_state.json"):
        """
        Initialize the state manager.
        
        Args:
            state_file: Path to the JSON file storing sync state
        """
        self.state_file = state_file
        self.state = self._load_state()
    
    def _load_state(self) -> Dict[str, Any]:
        """Load state from file or return empty state."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load state file: {e}. Starting with empty state.")
                return self._empty_state()
        return self._empty_state()
    
    def _empty_state(self) -> Dict[str, Any]:
        """Return an empty state structure."""
        return {
            "repositories": {},
            "last_sync": None
        }
    
    def _save_state(self):
        """Save state to file."""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save state file: {e}")
    
    def get_repo_state(self, repo_key: str) -> Dict[str, Any]:
        """
        Get state for a specific repository pair.
        
        Args:
            repo_key: Unique key for the repo pair (e.g., "gitlab_repo:github_repo")
            
        Returns:
            Dictionary with repo-specific state
        """
        if repo_key not in self.state["repositories"]:
            self.state["repositories"][repo_key] = {
                "last_sync": None,
                "issue_mappings": {},  # gitlab_issue_id -> github_issue_id and vice versa
                "mr_mappings": {},     # gitlab_mr_id -> github_pr_id and vice versa
                "last_metadata_sync": None,
                "last_code_sync": None,
                "last_issues_sync": None,
                "last_mrs_sync": None
            }
        return self.state["repositories"][repo_key]
    
    def update_last_sync(self, repo_key: str, sync_type: str = "full"):
        """
        Update the last sync timestamp for a repository.
        
        Args:
            repo_key: Unique key for the repo pair
            sync_type: Type of sync (full, metadata, code, issues, mrs)
        """
        repo_state = self.get_repo_state(repo_key)
        now = datetime.utcnow().isoformat()
        
        if sync_type == "full":
            repo_state["last_sync"] = now
        elif sync_type == "metadata":
            repo_state["last_metadata_sync"] = now
        elif sync_type == "code":
            repo_state["last_code_sync"] = now
        elif sync_type == "issues":
            repo_state["last_issues_sync"] = now
        elif sync_type == "mrs":
            repo_state["last_mrs_sync"] = now
        
        self.state["last_sync"] = now
        self._save_state()
    
    def map_issue(self, repo_key: str, gitlab_issue_id: Optional[int] = None,
                  github_issue_id: Optional[int] = None):
        """
        Map a GitLab issue to a GitHub issue (bidirectional).
        
        Args:
            repo_key: Unique key for the repo pair
            gitlab_issue_id: GitLab issue number
            github_issue_id: GitHub issue number
        """
        if not gitlab_issue_id and not github_issue_id:
            return
        
        repo_state = self.get_repo_state(repo_key)
        mappings = repo_state["issue_mappings"]
        
        if gitlab_issue_id and github_issue_id:
            # Bidirectional mapping
            mappings[f"gitlab:{gitlab_issue_id}"] = github_issue_id
            mappings[f"github:{github_issue_id}"] = gitlab_issue_id
        elif gitlab_issue_id:
            # Check if we already have a mapping
            key = f"gitlab:{gitlab_issue_id}"
            if key in mappings:
                return  # Already mapped
        elif github_issue_id:
            # Check if we already have a mapping
            key = f"github:{github_issue_id}"
            if key in mappings:
                return  # Already mapped
        
        self._save_state()
    
    def get_issue_mapping(self, repo_key: str, gitlab_issue_id: Optional[int] = None,
                         github_issue_id: Optional[int] = None) -> Optional[int]:
        """
        Get the mapped issue ID.
        
        Args:
            repo_key: Unique key for the repo pair
            gitlab_issue_id: GitLab issue number to look up
            github_issue_id: GitHub issue number to look up
            
        Returns:
            The mapped issue ID, or None if not found
        """
        repo_state = self.get_repo_state(repo_key)
        mappings = repo_state["issue_mappings"]
        
        if gitlab_issue_id:
            key = f"gitlab:{gitlab_issue_id}"
            return mappings.get(key)
        elif github_issue_id:
            key = f"github:{github_issue_id}"
            return mappings.get(key)
        
        return None
    
    def map_mr(self, repo_key: str, gitlab_mr_id: Optional[int] = None,
               github_pr_id: Optional[int] = None):
        """
        Map a GitLab MR to a GitHub PR (bidirectional).
        
        Args:
            repo_key: Unique key for the repo pair
            gitlab_mr_id: GitLab MR number
            github_pr_id: GitHub PR number
        """
        if not gitlab_mr_id and not github_pr_id:
            return
        
        repo_state = self.get_repo_state(repo_key)
        mappings = repo_state["mr_mappings"]
        
        if gitlab_mr_id and github_pr_id:
            # Bidirectional mapping
            mappings[f"gitlab:{gitlab_mr_id}"] = github_pr_id
            mappings[f"github:{github_pr_id}"] = gitlab_mr_id
        elif gitlab_mr_id:
            # Check if we already have a mapping
            key = f"gitlab:{gitlab_mr_id}"
            if key in mappings:
                return  # Already mapped
        elif github_pr_id:
            # Check if we already have a mapping
            key = f"github:{github_pr_id}"
            if key in mappings:
                return  # Already mapped
        
        self._save_state()
    
    def get_mr_mapping(self, repo_key: str, gitlab_mr_id: Optional[int] = None,
                      github_pr_id: Optional[int] = None) -> Optional[int]:
        """
        Get the mapped MR/PR ID.
        
        Args:
            repo_key: Unique key for the repo pair
            gitlab_mr_id: GitLab MR number to look up
            github_pr_id: GitHub PR number to look up
            
        Returns:
            The mapped MR/PR ID, or None if not found
        """
        repo_state = self.get_repo_state(repo_key)
        mappings = repo_state["mr_mappings"]
        
        if gitlab_mr_id:
            key = f"gitlab:{gitlab_mr_id}"
            return mappings.get(key)
        elif github_pr_id:
            key = f"github:{github_pr_id}"
            return mappings.get(key)
        
        return None
    
    def is_issue_synced(self, repo_key: str, gitlab_issue_id: Optional[int] = None,
                       github_issue_id: Optional[int] = None) -> bool:
        """Check if an issue has already been synced."""
        return self.get_issue_mapping(repo_key, gitlab_issue_id, github_issue_id) is not None
    
    def is_mr_synced(self, repo_key: str, gitlab_mr_id: Optional[int] = None,
                    github_pr_id: Optional[int] = None) -> bool:
        """Check if an MR/PR has already been synced."""
        return self.get_mr_mapping(repo_key, gitlab_mr_id, github_pr_id) is not None
