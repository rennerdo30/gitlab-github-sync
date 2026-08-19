"""CLI wrapper for GitHub (gh) and GitLab (glab) commands."""

import subprocess
import json
import logging
import re
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class CLIError(Exception):
    """Exception raised for CLI command failures."""
    def __init__(self, message: str, returncode: int = 1, stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr
        self.is_not_found = "404" in stderr or "not found" in stderr.lower()


def run_command(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    """
    Run a CLI command and return the result.
    
    Args:
        cmd: Command and arguments as a list
        check: If True, raise exception on non-zero exit code
        
    Returns:
        CompletedProcess object with stdout, stderr, returncode
        
    Raises:
        CLIError: If command fails and check is True
    """
    try:
        logger.debug(f"Running command: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=check
        )
        if result.returncode != 0 and check:
            raise CLIError(
                f"Command failed: {' '.join(cmd)}\n"
                f"Return code: {result.returncode}\n"
                f"Stderr: {result.stderr}",
                returncode=result.returncode,
                stderr=result.stderr
            )
        return result
    except subprocess.CalledProcessError as e:
        raise CLIError(
            f"Command failed: {' '.join(cmd)}\n{e.stderr}",
            returncode=e.returncode,
            stderr=e.stderr if hasattr(e, 'stderr') else str(e)
        )


def parse_json_output(output: str) -> Any:
    """Parse JSON output from CLI commands."""
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse JSON output: {output[:100]}")
        return None


def _extract_trailing_number(output: str) -> Optional[int]:
    """Extract the trailing number of a resource URL printed by `gh`.

    `gh issue create` and `gh pr create` print the URL of the created
    resource, e.g. https://github.com/owner/repo/issues/123
    """
    match = re.search(r'/(\d+)\s*$', output.strip())
    if match:
        return int(match.group(1))
    return None


# GitHub CLI (gh) wrappers

def gh_repo_list(owner: Optional[str] = None, limit: int = 100, json_format: bool = True) -> List[Dict]:
    """List repositories from GitHub.
    
    Args:
        owner: GitHub username or organization (default: authenticated user)
        limit: Maximum number of repositories to list
        json_format: If True, return JSON parsed data
        
    Returns:
        List of repository dictionaries
    """
    cmd = ["gh", "repo", "list"]
    if owner:
        cmd.append(owner)
    cmd.extend(["--limit", str(limit)])
    if json_format:
        cmd.extend(["--json", "nameWithOwner,name,description,homepageUrl,url,repositoryTopics"])
    result = run_command(cmd)
    if json_format:
        return parse_json_output(result.stdout) or []
    return []


def gh_repo_create(repo: str, description: Optional[str] = None,
                   homepage: Optional[str] = None, private: bool = False,
                   add_readme: bool = False) -> Optional[Dict]:
    """Create a new GitHub repository.
    
    Args:
        repo: Repository name (e.g., "owner/repo")
        description: Repository description
        homepage: Homepage URL
        private: If True, create as private repository
        add_readme: If True, add a README file
        
    Returns:
        Repository data if successful, None otherwise
    """
    cmd = ["gh", "repo", "create", repo]
    if private:
        cmd.append("--private")
    else:
        cmd.append("--public")
    if description:
        cmd.extend(["--description", description])
    if homepage:
        cmd.extend(["--homepage", homepage])
    if add_readme:
        cmd.append("--add-readme")
    # gh repo create doesn't support --json, so we verify creation by viewing the repo
    result = run_command(cmd, check=False)
    if result.returncode == 0:
        # Verify the repo was created by trying to view it
        try:
            repo_data = gh_repo_view(repo)
            return repo_data
        except:
            # If we can't view it immediately, assume it was created
            logger.debug(f"Created repo {repo} but couldn't verify immediately")
            return {"name": repo, "created": True}
    else:
        logger.error(f"Failed to create GitHub repo: {result.stderr}")
    return None


def gh_repo_view(repo: str, json_format: bool = True) -> Optional[Dict]:
    """Get repository information from GitHub."""
    cmd = ["gh", "repo", "view", repo]
    if json_format:
        cmd.extend(["--json", "name,description,homepageUrl,url,repositoryTopics"])
    result = run_command(cmd)
    if json_format:
        return parse_json_output(result.stdout)
    return {"output": result.stdout}


def gh_repo_edit(repo: str, description: Optional[str] = None, 
                 homepage: Optional[str] = None, add_topic: Optional[List[str]] = None) -> bool:
    """Edit GitHub repository metadata."""
    cmd = ["gh", "repo", "edit", repo]
    if description is not None:
        cmd.extend(["--description", description])
    if homepage is not None:
        cmd.extend(["--homepage", homepage])
    if add_topic:
        for topic in add_topic:
            cmd.extend(["--add-topic", topic])
    result = run_command(cmd, check=False)
    return result.returncode == 0


def gh_issue_list(repo: str, state: str = "all", json_format: bool = True) -> List[Dict]:
    """List issues from GitHub repository."""
    cmd = ["gh", "issue", "list", "--repo", repo, "--state", state]
    if json_format:
        cmd.extend(["--json", "number,title,body,state,createdAt,updatedAt,labels"])
    result = run_command(cmd)
    if json_format:
        return parse_json_output(result.stdout) or []
    return []


def gh_issue_view(repo: str, issue_number: int, json_format: bool = True) -> Optional[Dict]:
    """View a specific GitHub issue."""
    cmd = ["gh", "issue", "view", str(issue_number), "--repo", repo]
    if json_format:
        cmd.extend(["--json", "number,title,body,state,createdAt,updatedAt,labels,comments"])
    result = run_command(cmd)
    if json_format:
        return parse_json_output(result.stdout)
    return None


def gh_issue_create(repo: str, title: str, body: str = "", labels: Optional[List[str]] = None) -> Optional[Dict]:
    """Create a new GitHub issue.

    Note: `gh issue create` has no JSON output mode. It prints the URL of the
    new issue, so the number is taken from that URL and the issue is then
    fetched with `gh issue view --json`.
    """
    cmd = ["gh", "issue", "create", "--repo", repo, "--title", title]
    if body:
        cmd.extend(["--body", body])
    if labels:
        for label in labels:
            cmd.extend(["--label", label])
    result = run_command(cmd, check=False)
    if result.returncode != 0:
        logger.error(f"Failed to create GitHub issue: {result.stderr}")
        return None

    issue_number = _extract_trailing_number(result.stdout + result.stderr)
    if issue_number is None:
        logger.debug(f"Created issue but could not extract its number from: {result.stdout}")
        return {"created": True, "title": title}
    return gh_issue_view(repo, issue_number)


def gh_issue_edit(repo: str, issue_number: int, title: Optional[str] = None,
                  body: Optional[str] = None, state: Optional[str] = None) -> bool:
    """Edit a GitHub issue."""
    cmd = ["gh", "issue", "edit", str(issue_number), "--repo", repo]
    if title is not None:
        cmd.extend(["--title", title])
    if body is not None:
        cmd.extend(["--body", body])
    if state is not None:
        cmd.extend(["--state", state])
    result = run_command(cmd, check=False)
    return result.returncode == 0


def gh_pr_list(repo: str, state: str = "all", json_format: bool = True) -> List[Dict]:
    """List pull requests from GitHub repository."""
    cmd = ["gh", "pr", "list", "--repo", repo, "--state", state]
    if json_format:
        cmd.extend(["--json", "number,title,body,state,headRefName,baseRefName,createdAt,updatedAt"])
    result = run_command(cmd)
    if json_format:
        return parse_json_output(result.stdout) or []
    return []


def gh_pr_view(repo: str, pr_number: int, json_format: bool = True) -> Optional[Dict]:
    """View a specific GitHub pull request."""
    cmd = ["gh", "pr", "view", str(pr_number), "--repo", repo]
    if json_format:
        cmd.extend(["--json", "number,title,body,state,headRefName,baseRefName,createdAt,updatedAt"])
    result = run_command(cmd)
    if json_format:
        return parse_json_output(result.stdout)
    return None


def gh_pr_create(repo: str, title: str, body: str, head: str, base: str) -> Optional[Dict]:
    """Create a new GitHub pull request.

    Note: `gh pr create` has no JSON output mode either; the number is taken
    from the URL it prints and the PR is then fetched with `gh pr view --json`.
    """
    cmd = ["gh", "pr", "create", "--repo", repo, "--title", title,
           "--body", body, "--head", head, "--base", base]
    result = run_command(cmd, check=False)
    if result.returncode != 0:
        logger.error(f"Failed to create GitHub PR: {result.stderr}")
        return None

    pr_number = _extract_trailing_number(result.stdout + result.stderr)
    if pr_number is None:
        logger.debug(f"Created PR but could not extract its number from: {result.stdout}")
        return {"created": True, "title": title}
    return gh_pr_view(repo, pr_number)


def gh_pr_edit(repo: str, pr_number: int, title: Optional[str] = None,
               body: Optional[str] = None, state: Optional[str] = None) -> bool:
    """Edit a GitHub pull request."""
    cmd = ["gh", "pr", "edit", str(pr_number), "--repo", repo]
    if title is not None:
        cmd.extend(["--title", title])
    if body is not None:
        cmd.extend(["--body", body])
    if state is not None:
        cmd.extend(["--state", state])
    result = run_command(cmd, check=False)
    return result.returncode == 0


# GitLab CLI (glab) wrappers

def glab_repo_list(group: Optional[str] = None, user: Optional[str] = None, mine: bool = True, 
                   per_page: int = 100, json_format: bool = True) -> List[Dict]:
    """List repositories from GitLab.
    
    Args:
        group: GitLab group/namespace to list repos from (optional)
        user: GitLab username to list repos from (optional, use instead of group for user namespaces)
        mine: If True, list only repos owned by authenticated user (default if no group/user specified)
        per_page: Number of items per page
        json_format: If True, return JSON parsed data
        
    Returns:
        List of repository dictionaries
    """
    cmd = ["glab", "repo", "list"]
    if group:
        cmd.extend(["--group", group])
    elif user:
        cmd.extend(["--user", user])
    elif mine:
        cmd.append("--mine")
    cmd.extend(["--per-page", str(per_page)])
    if json_format:
        cmd.extend(["-F", "json"])
    result = run_command(cmd)
    if json_format:
        return parse_json_output(result.stdout) or []
    return []


def glab_repo_view(repo: str, json_format: bool = True) -> Optional[Dict]:
    """Get repository information from GitLab."""
    cmd = ["glab", "repo", "view", repo]
    if json_format:
        cmd.extend(["-F", "json"])
    result = run_command(cmd)
    if json_format:
        return parse_json_output(result.stdout)
    return {"output": result.stdout}


def glab_repo_create(repo: str, description: Optional[str] = None,
                    group: Optional[str] = None, private: bool = False,
                    default_branch: Optional[str] = None) -> Optional[Dict]:
    """Create a new GitLab repository.
    
    Args:
        repo: Repository name or path (e.g., "project-name" or "group/project-name")
        description: Repository description
        group: Group/namespace for the new project (if repo doesn't include path)
        private: If True, create as private repository
        default_branch: Default branch name (defaults to master)
        
    Returns:
        Repository data if successful, None otherwise
    """
    cmd = ["glab", "repo", "create", repo]
    if description:
        cmd.extend(["--description", description])
    if group and "/" not in repo:  # Only use --group if repo doesn't already have a path
        cmd.extend(["--group", group])
    if private:
        cmd.append("--private")
    else:
        cmd.append("--internal")  # Default visibility
    if default_branch:
        cmd.extend(["--defaultBranch", default_branch])
    # Note: glab repo create doesn't support -F json, so we parse the output manually
    result = run_command(cmd, check=False)
    if result.returncode == 0:
        # glab outputs success message, we consider it successful
        # Try to get the repo info to return
        try:
            repo_data = glab_repo_view(repo)
            return repo_data
        except:
            # If we can't get the repo data, still return success indicator
            return {"name": repo, "created": True}
    else:
        logger.error(f"Failed to create GitLab repo: {result.stderr}")
    return None


def glab_repo_update(repo: str, description: Optional[str] = None,
                    homepage: Optional[str] = None, topics: Optional[List[str]] = None) -> bool:
    """Update GitLab repository metadata."""
    cmd = ["glab", "repo", "update", repo]
    if description is not None:
        cmd.extend(["--description", description])
    if homepage is not None:
        cmd.extend(["--homepage", homepage])
    if topics:
        cmd.extend(["--topics", ",".join(topics)])
    result = run_command(cmd, check=False)
    return result.returncode == 0


def glab_issue_list(repo: str, state: str = "all", json_format: bool = True) -> List[Dict]:
    """List issues from GitLab repository."""
    cmd = ["glab", "issue", "list", "--repo", repo]
    if state == "all":
        cmd.append("--all")
    elif state == "closed":
        cmd.append("--closed")
    if json_format:
        cmd.extend(["-O", "json"])
    result = run_command(cmd)
    if json_format:
        return parse_json_output(result.stdout) or []
    return []


def glab_issue_view(repo: str, issue_number: int, json_format: bool = True) -> Optional[Dict]:
    """View a specific GitLab issue."""
    cmd = ["glab", "issue", "view", str(issue_number), "--repo", repo]
    if json_format:
        cmd.extend(["-F", "json"])
    result = run_command(cmd)
    if json_format:
        return parse_json_output(result.stdout)
    return None


def glab_issue_create(repo: str, title: str, description: str = "", labels: Optional[List[str]] = None) -> Optional[Dict]:
    """Create a new GitLab issue.
    
    Note: glab issue create doesn't support JSON output, so we create it and then view it.
    """
    cmd = ["glab", "issue", "create", "--repo", repo, "--title", title]
    if description:
        cmd.extend(["--description", description])
    if labels:
        cmd.extend(["--label", ",".join(labels)])
    # glab issue create doesn't support -F json, so we create and then view
    result = run_command(cmd, check=False)
    if result.returncode == 0:
        # Try to extract issue number from output (format: "!123" or "#123")
        output = result.stdout + result.stderr
        match = re.search(r'[!#](\d+)', output)
        if match:
            issue_number = int(match.group(1))
            # View the created issue to get JSON data
            return glab_issue_view(repo, issue_number)
        else:
            # If we can't extract the number, return success indicator
            logger.debug(f"Created issue but couldn't extract number from: {output}")
            return {"created": True, "title": title}
    else:
        logger.error(f"Failed to create GitLab issue: {result.stderr}")
        return None


def glab_issue_update(repo: str, issue_number: int, title: Optional[str] = None,
                     description: Optional[str] = None, state: Optional[str] = None) -> bool:
    """Update a GitLab issue."""
    cmd = ["glab", "issue", "update", str(issue_number), "--repo", repo]
    if title is not None:
        cmd.extend(["--title", title])
    if description is not None:
        cmd.extend(["--description", description])
    if state is not None:
        cmd.extend(["--state", state])
    result = run_command(cmd, check=False)
    return result.returncode == 0


def glab_mr_list(repo: str, state: str = "all", json_format: bool = True) -> List[Dict]:
    """List merge requests from GitLab repository."""
    cmd = ["glab", "mr", "list", "--repo", repo]
    if state == "all":
        cmd.append("--all")
    elif state == "closed":
        cmd.append("--closed")
    elif state == "merged":
        cmd.append("--merged")
    if json_format:
        cmd.extend(["-F", "json"])
    result = run_command(cmd)
    if json_format:
        return parse_json_output(result.stdout) or []
    return []


def glab_mr_view(repo: str, mr_number: int, json_format: bool = True) -> Optional[Dict]:
    """View a specific GitLab merge request."""
    cmd = ["glab", "mr", "view", str(mr_number), "--repo", repo]
    if json_format:
        cmd.extend(["-F", "json"])
    result = run_command(cmd)
    if json_format:
        return parse_json_output(result.stdout)
    return None


def glab_mr_create(repo: str, title: str, description: str, source_branch: str, target_branch: str) -> Optional[Dict]:
    """Create a new GitLab merge request.
    
    Note: glab mr create doesn't support JSON output, so we create it and then view it.
    """
    cmd = ["glab", "mr", "create", "--repo", repo, "--title", title,
           "--description", description, "--source-branch", source_branch,
           "--target-branch", target_branch]
    # glab mr create doesn't support -F json, so we create and then view
    result = run_command(cmd, check=False)
    if result.returncode == 0:
        # Try to extract MR number from output (format: "!123" or "#123")
        output = result.stdout + result.stderr
        match = re.search(r'[!#](\d+)', output)
        if match:
            mr_number = int(match.group(1))
            # View the created MR to get JSON data
            return glab_mr_view(repo, mr_number)
        else:
            # If we can't extract the number, return success indicator
            logger.debug(f"Created MR but couldn't extract number from: {output}")
            return {"created": True, "title": title}
    else:
        logger.error(f"Failed to create GitLab MR: {result.stderr}")
        return None


def glab_mr_update(repo: str, mr_number: int, title: Optional[str] = None,
                  description: Optional[str] = None, state: Optional[str] = None) -> bool:
    """Update a GitLab merge request."""
    cmd = ["glab", "mr", "update", str(mr_number), "--repo", repo]
    if title is not None:
        cmd.extend(["--title", title])
    if description is not None:
        cmd.extend(["--description", description])
    if state is not None:
        cmd.extend(["--state", state])
    result = run_command(cmd, check=False)
    return result.returncode == 0
