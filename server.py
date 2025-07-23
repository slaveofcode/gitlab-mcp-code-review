import os
import json
import logging
from typing import Optional, Dict, Any, Union, List
from dataclasses import dataclass
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from urllib.parse import quote
import requests

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Context

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

@dataclass
class GitLabContext:
    host: str
    token: str
    api_version: str = "v4"

def make_gitlab_api_request(ctx: Context, endpoint: str, method: str = "GET", data: Optional[Dict[str, Any]] = None) -> Any:
    """Make a REST API request to GitLab and handle the response"""
    gitlab_ctx = ctx.request_context.lifespan_context
    
    if not gitlab_ctx.token:
        logger.error("GitLab token not set in context")
        raise ValueError("GitLab token not set. Please set GITLAB_TOKEN in your environment.")
    
    url = f"https://{gitlab_ctx.host}/api/{gitlab_ctx.api_version}/{endpoint}"
    
    # FIX: Use Bearer token format instead of Private-Token
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'GitLabMCPCodeReview/1.0',
        'Authorization': f'Bearer {gitlab_ctx.token}'  # Changed from Private-Token
    }
    
    # Log the request for debugging
    logger.info(f"Making {method} request to: {url}")
    if data:
        logger.debug(f"Request data: {json.dumps(data, indent=2)}")
    
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, verify=True, timeout=30)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, json=data, verify=True, timeout=30)
        elif method.upper() == "PUT":
            response = requests.put(url, headers=headers, json=data, verify=True, timeout=30)
        elif method.upper() == "DELETE":
            response = requests.delete(url, headers=headers, verify=True, timeout=30)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")
        
        # Enhanced error handling
        logger.info(f"Response status: {response.status_code}")
        
        if response.status_code == 401:
            logger.error("Authentication failed. Check your GitLab token.")
            raise Exception("Authentication failed. Please check your GitLab token.")
        elif response.status_code == 403:
            logger.error(f"Access forbidden. Check your permissions for: {endpoint}")
            logger.error(f"Response: {response.text}")
            raise Exception(f"Access forbidden. You may not have permission to access this resource: {endpoint}")
        elif response.status_code == 404:
            logger.error(f"Resource not found: {endpoint}")
            raise Exception(f"Resource not found: {endpoint}")
            
        response.raise_for_status()
        
        if not response.content:
            return {}
            
        try:
            result = response.json()
            logger.debug(f"Response: {json.dumps(result, indent=2)}")
            return result
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {str(e)}")
            logger.error(f"Raw response: {response.text}")
            raise Exception(f"Failed to parse GitLab response as JSON: {str(e)}")
            
    except requests.exceptions.Timeout:
        logger.error("Request timed out")
        raise Exception("Request timed out. GitLab server may be slow.")
    except requests.exceptions.RequestException as e:
        logger.error(f"REST request failed: {str(e)}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response status: {e.response.status_code}")
            logger.error(f"Response text: {e.response.text}")
        raise Exception(f"Failed to make GitLab API request: {str(e)}")

@asynccontextmanager
async def gitlab_lifespan(server: FastMCP) -> AsyncIterator[GitLabContext]:
    """Manage GitLab connection details"""
    host = os.getenv("GITLAB_HOST", "gitlab.com")
    token = os.getenv("GITLAB_TOKEN", "")
    
    if not token:
        logger.error("Missing required environment variable: GITLAB_TOKEN")
        raise ValueError(
            "Missing required environment variable: GITLAB_TOKEN. "
            "Please set this in your environment or .env file."
        )
    
    # Test the token on startup
    ctx = GitLabContext(host=host, token=token)
    logger.info(f"Initialized GitLab context for host: {host}")
    
    try:
        yield ctx
    finally:
        pass

# Create MCP server
mcp = FastMCP(
    "GitLab MCP for Code Review",
    description="MCP server for reviewing GitLab code changes",
    lifespan=gitlab_lifespan,
    dependencies=["python-dotenv", "requests"]
)

@mcp.tool()
def test_gitlab_connection(ctx: Context) -> Dict[str, Any]:
    """
    Test the GitLab API connection and token permissions.
    
    Returns:
        Dict containing connection test results
    """
    try:
        # Test basic API access
        user_info = make_gitlab_api_request(ctx, "user")
        
        return {
            "status": "success",
            "message": "GitLab connection successful",
            "user": {
                "id": user_info.get("id"),
                "username": user_info.get("username"),
                "name": user_info.get("name")
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"GitLab connection failed: {str(e)}"
        }

@mcp.tool()
def fetch_merge_request(ctx: Context, project_id: str, merge_request_iid: str) -> Dict[str, Any]:
    """
    Fetch a GitLab merge request and its contents.
    
    Args:
        project_id: The GitLab project ID or URL-encoded path
        merge_request_iid: The merge request IID (project-specific ID)
    Returns:
        Dict containing the merge request information
    """
    # Get merge request details
    mr_endpoint = f"projects/{quote(project_id, safe='')}/merge_requests/{merge_request_iid}"
    mr_info = make_gitlab_api_request(ctx, mr_endpoint)
    
    if not mr_info:
        raise ValueError(f"Merge request {merge_request_iid} not found in project {project_id}")
    
    # Get the changes (diffs) for this merge request
    changes_endpoint = f"{mr_endpoint}/changes"
    changes_info = make_gitlab_api_request(ctx, changes_endpoint)
    
    return {
        "merge_request": mr_info,
        "changes": changes_info
    }

@mcp.tool()
def fetch_merge_request_diff(ctx: Context, project_id: str, merge_request_iid: str, file_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch the diff for a specific file in a merge request, or all files if none specified.
    
    Args:
        project_id: The GitLab project ID or URL-encoded path
        merge_request_iid: The merge request IID (project-specific ID)
        file_path: Optional specific file path to get diff for
    Returns:
        Dict containing the diff information
    """
    # Get the changes for this merge request
    changes_endpoint = f"projects/{quote(project_id, safe='')}/merge_requests/{merge_request_iid}/changes"
    changes_info = make_gitlab_api_request(ctx, changes_endpoint)
    
    if not changes_info:
        raise ValueError(f"Changes not found for merge request {merge_request_iid}")
    
    # Extract all changes
    files = changes_info.get("changes", [])
    
    # Filter by file path if specified
    if file_path:
        files = [f for f in files if f.get("new_path") == file_path or f.get("old_path") == file_path]
        if not files:
            raise ValueError(f"File '{file_path}' not found in the merge request changes")
    
    return {
        "merge_request_iid": merge_request_iid,
        "files": files
    }

@mcp.tool()
def fetch_commit_diff(ctx: Context, project_id: str, commit_sha: str, file_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch the diff for a specific commit, or for a specific file in that commit.
    
    Args:
        project_id: The GitLab project ID or URL-encoded path
        commit_sha: The commit SHA
        file_path: Optional specific file path to get diff for
    Returns:
        Dict containing the diff information
    """
    # Get the diff for this commit
    diff_endpoint = f"projects/{quote(project_id, safe='')}/repository/commits/{commit_sha}/diff"
    diff_info = make_gitlab_api_request(ctx, diff_endpoint)
    
    if not diff_info:
        raise ValueError(f"Diff not found for commit {commit_sha}")
    
    # Filter by file path if specified
    if file_path:
        diff_info = [d for d in diff_info if d.get("new_path") == file_path or d.get("old_path") == file_path]
        if not diff_info:
            raise ValueError(f"File '{file_path}' not found in the commit diff")
    
    # Get the commit details
    commit_endpoint = f"projects/{quote(project_id, safe='')}/repository/commits/{commit_sha}"
    commit_info = make_gitlab_api_request(ctx, commit_endpoint)
    
    return {
        "commit": commit_info,
        "diffs": diff_info
    }

@mcp.tool()
def compare_versions(ctx: Context, project_id: str, from_sha: str, to_sha: str) -> Dict[str, Any]:
    """
    Compare two commits/branches/tags to see the differences between them.
    
    Args:
        project_id: The GitLab project ID or URL-encoded path
        from_sha: The source commit/branch/tag
        to_sha: The target commit/branch/tag
    Returns:
        Dict containing the comparison information
    """
    # Compare the versions
    compare_endpoint = f"projects/{quote(project_id, safe='')}/repository/compare?from={quote(from_sha, safe='')}&to={quote(to_sha, safe='')}"
    compare_info = make_gitlab_api_request(ctx, compare_endpoint)
    
    if not compare_info:
        raise ValueError(f"Comparison failed between {from_sha} and {to_sha}")
    
    return compare_info

@mcp.tool()
def add_merge_request_comment(ctx: Context, project_id: str, merge_request_iid: str, body: str, position: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Add a comment to a merge request, optionally at a specific position in a file.
    
    Args:
        project_id: The GitLab project ID or URL-encoded path
        merge_request_iid: The merge request IID (project-specific ID)
        body: The comment text
        position: Optional position data for line comments
    Returns:
        Dict containing the created comment information
    """
    logger.info(f"Adding comment to MR {merge_request_iid} in project {project_id}")
    
    # Create the comment data
    data = {
        "body": body
    }
    
    # Add position data if provided
    if position:
        data["position"] = position
    
    # Add the comment
    comment_endpoint = f"projects/{quote(project_id, safe='')}/merge_requests/{merge_request_iid}/notes"
    
    try:
        comment_info = make_gitlab_api_request(ctx, comment_endpoint, method="POST", data=data)
        
        if not comment_info:
            raise ValueError("Failed to add comment to merge request")
        
        logger.info(f"Successfully added comment with ID: {comment_info.get('id')}")
        return comment_info
        
    except Exception as e:
        logger.error(f"Failed to add comment: {str(e)}")
        raise

@mcp.tool()
def get_merge_request_notes(ctx: Context, project_id: str, merge_request_iid: str) -> List[Dict[str, Any]]:
    """
    Get all notes/comments for a merge request.
    
    Args:
        project_id: The GitLab project ID or URL-encoded path
        merge_request_iid: The merge request IID (project-specific ID)
    Returns:
        List of note objects
    """
    notes_endpoint = f"projects/{quote(project_id, safe='')}/merge_requests/{merge_request_iid}/notes"
    notes_info = make_gitlab_api_request(ctx, notes_endpoint)
    
    return notes_info

@mcp.tool()
def approve_merge_request(ctx: Context, project_id: str, merge_request_iid: str, approvals_required: Optional[int] = None) -> Dict[str, Any]:
    """
    Approve a merge request.
    
    Args:
        project_id: The GitLab project ID or URL-encoded path
        merge_request_iid: The merge request IID (project-specific ID)
        approvals_required: Optional number of required approvals to set
    Returns:
        Dict containing the approval information
    """
    # Approve the merge request
    approve_endpoint = f"projects/{quote(project_id, safe='')}/merge_requests/{merge_request_iid}/approve"
    approve_info = make_gitlab_api_request(ctx, approve_endpoint, method="POST")
    
    # Set required approvals if specified
    if approvals_required is not None:
        approvals_endpoint = f"projects/{quote(project_id, safe='')}/merge_requests/{merge_request_iid}/approvals"
        data = {
            "approvals_required": approvals_required
        }
        make_gitlab_api_request(ctx, approvals_endpoint, method="POST", data=data)
    
    return approve_info

@mcp.tool()
def unapprove_merge_request(ctx: Context, project_id: str, merge_request_iid: str) -> Dict[str, Any]:
    """
    Unapprove a merge request.
    
    Args:
        project_id: The GitLab project ID or URL-encoded path
        merge_request_iid: The merge request IID (project-specific ID)
    Returns:
        Dict containing the unapproval information
    """
    # Unapprove the merge request
    unapprove_endpoint = f"projects/{quote(project_id, safe='')}/merge_requests/{merge_request_iid}/unapprove"
    unapprove_info = make_gitlab_api_request(ctx, unapprove_endpoint, method="POST")
    
    return unapprove_info

@mcp.tool()
def get_project_merge_requests(ctx: Context, project_id: str, state: str = "all", limit: int = 20) -> List[Dict[str, Any]]:
    """
    Get all merge requests for a project.
    
    Args:
        project_id: The GitLab project ID or URL-encoded path
        state: Filter merge requests by state (all, opened, closed, merged, or locked)
        limit: Maximum number of merge requests to return
    Returns:
        List of merge request objects
    """
    # Get the merge requests
    mrs_endpoint = f"projects/{quote(project_id, safe='')}/merge_requests?state={state}&per_page={limit}"
    mrs_info = make_gitlab_api_request(ctx, mrs_endpoint)
    
    return mrs_info

@mcp.tool()
def get_user_approved_merge_requests(
    ctx: Context, 
    project_id: str, 
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    state: str = "merged",
    limit: int = 100
) -> Dict[str, Any]:
    """
    Get merge requests approved by a specific user within a timeframe.
    
    Args:
        project_id: The GitLab project ID or URL-encoded path
        user_id: The GitLab user ID (either user_id or username must be provided)
        username: The GitLab username (either user_id or username must be provided)
        created_after: ISO 8601 formatted date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)
        created_before: ISO 8601 formatted date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)
        state: Filter merge requests by state (all, opened, closed, merged, or locked)
        limit: Maximum number of merge requests to return
    Returns:
        Dict containing approved merge requests and statistics
    """
    # First, get the user info if username is provided instead of user_id
    target_user_id = user_id
    if not target_user_id and username:
        users_endpoint = f"users?username={quote(username, safe='')}"
        users_info = make_gitlab_api_request(ctx, users_endpoint)
        if not users_info:
            raise ValueError(f"User '{username}' not found")
        target_user_id = str(users_info[0]["id"])
    
    if not target_user_id:
        raise ValueError("Either user_id or username must be provided")
    
    # Build the query parameters
    params = [
        f"state={state}",
        f"per_page={limit}",
        "sort=desc",
        "order_by=created_at"
    ]
    
    if created_after:
        params.append(f"created_after={quote(created_after, safe='')}")
    if created_before:
        params.append(f"created_before={quote(created_before, safe='')}")
    
    # Get merge requests
    mrs_endpoint = f"projects/{quote(project_id, safe='')}/merge_requests?{'&'.join(params)}"
    mrs_info = make_gitlab_api_request(ctx, mrs_endpoint)
    
    approved_mrs = []
    total_approved = 0
    
    for mr in mrs_info:
        # Get approval information for each MR
        approval_endpoint = f"projects/{quote(project_id, safe='')}/merge_requests/{mr['iid']}/approvals"
        try:
            approval_info = make_gitlab_api_request(ctx, approval_endpoint)
            
            # Check if the user approved this MR
            approved_by_users = approval_info.get("approved_by", [])
            user_approved = any(
                str(approver.get("user", {}).get("id")) == target_user_id 
                for approver in approved_by_users
            )
            
            if user_approved:
                # Find the specific approval by this user
                user_approval = next(
                    (approver for approver in approved_by_users 
                     if str(approver.get("user", {}).get("id")) == target_user_id),
                    None
                )
                
                mr_data = {
                    "merge_request": mr,
                    "approval_info": approval_info,
                    "user_approval": user_approval,
                    "approved_at": user_approval.get("created_at") if user_approval else None
                }
                approved_mrs.append(mr_data)
                total_approved += 1
                
        except Exception as e:
            logger.warning(f"Could not fetch approval info for MR {mr['iid']}: {str(e)}")
            continue
    
    return {
        "project_id": project_id,
        "user_id": target_user_id,
        "timeframe": {
            "created_after": created_after,
            "created_before": created_before
        },
        "statistics": {
            "total_approved": total_approved,
            "total_mrs_checked": len(mrs_info)
        },
        "approved_merge_requests": approved_mrs
    }

@mcp.tool()
def get_project_approval_statistics(
    ctx: Context,
    project_id: str,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    state: str = "merged",
    limit: int = 200
) -> Dict[str, Any]:
    """
    Get aggregated approval statistics for a project within a timeframe.
    
    Args:
        project_id: The GitLab project ID or URL-encoded path
        created_after: ISO 8601 formatted date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)
        created_before: ISO 8601 formatted date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)
        state: Filter merge requests by state (all, opened, closed, merged, or locked)
        limit: Maximum number of merge requests to analyze
    Returns:
        Dict containing approval statistics aggregated by user
    """
    # Build the query parameters
    params = [
        f"state={state}",
        f"per_page={limit}",
        "sort=desc",
        "order_by=created_at"
    ]
    
    if created_after:
        params.append(f"created_after={quote(created_after, safe='')}")
    if created_before:
        params.append(f"created_before={quote(created_before, safe='')}")
    
    # Get merge requests
    mrs_endpoint = f"projects/{quote(project_id, safe='')}/merge_requests?{'&'.join(params)}"
    mrs_info = make_gitlab_api_request(ctx, mrs_endpoint)
    
    user_stats = {}
    total_mrs = len(mrs_info)
    total_approvals = 0
    
    for mr in mrs_info:
        # Get approval information for each MR
        approval_endpoint = f"projects/{quote(project_id, safe='')}/merge_requests/{mr['iid']}/approvals"
        try:
            approval_info = make_gitlab_api_request(ctx, approval_endpoint)
            approved_by_users = approval_info.get("approved_by", [])
            
            for approver in approved_by_users:
                user_info = approver.get("user", {})
                user_id = str(user_info.get("id", "unknown"))
                username = user_info.get("username", "unknown")
                name = user_info.get("name", "unknown")
                
                if user_id not in user_stats:
                    user_stats[user_id] = {
                        "user_id": user_id,
                        "username": username,
                        "name": name,
                        "total_approvals": 0,
                        "approved_mrs": []
                    }
                
                user_stats[user_id]["total_approvals"] += 1
                user_stats[user_id]["approved_mrs"].append({
                    "mr_iid": mr["iid"],
                    "mr_title": mr["title"],
                    "approved_at": approver.get("created_at"),
                    "mr_created_at": mr["created_at"],
                    "mr_web_url": mr["web_url"]
                })
                total_approvals += 1
                
        except Exception as e:
            logger.warning(f"Could not fetch approval info for MR {mr['iid']}: {str(e)}")
            continue
    
    # Sort users by total approvals
    sorted_users = sorted(user_stats.values(), key=lambda x: x["total_approvals"], reverse=True)
    
    return {
        "project_id": project_id,
        "timeframe": {
            "created_after": created_after,
            "created_before": created_before
        },
        "statistics": {
            "total_merge_requests": total_mrs,
            "total_approvals": total_approvals,
            "unique_approvers": len(user_stats),
            "average_approvals_per_mr": round(total_approvals / total_mrs, 2) if total_mrs > 0 else 0
        },
        "users": sorted_users
    }

@mcp.tool()
def get_user_approval_summary(
    ctx: Context,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    project_ids: Optional[List[str]] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    state: str = "merged"
) -> Dict[str, Any]:
    """
    Get a comprehensive approval summary for a user across multiple projects.
    
    Args:
        user_id: The GitLab user ID (either user_id or username must be provided)
        username: The GitLab username (either user_id or username must be provided)
        project_ids: List of project IDs to analyze (if None, gets user's accessible projects)
        created_after: ISO 8601 formatted date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)
        created_before: ISO 8601 formatted date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)
        state: Filter merge requests by state (all, opened, closed, merged, or locked)
    Returns:
        Dict containing comprehensive approval summary across projects
    """
    # First, get the user info if username is provided instead of user_id
    target_user_id = user_id
    user_info = None
    
    if not target_user_id and username:
        users_endpoint = f"users?username={quote(username, safe='')}"
        users_data = make_gitlab_api_request(ctx, users_endpoint)
        if not users_data:
            raise ValueError(f"User '{username}' not found")
        user_info = users_data[0]
        target_user_id = str(user_info["id"])
    elif target_user_id:
        users_endpoint = f"users/{target_user_id}"
        user_info = make_gitlab_api_request(ctx, users_endpoint)
    
    if not target_user_id:
        raise ValueError("Either user_id or username must be provided")
    
    # If no project_ids provided, get user's accessible projects
    if not project_ids:
        projects_endpoint = f"users/{target_user_id}/projects?per_page=50"
        try:
            projects_data = make_gitlab_api_request(ctx, projects_endpoint)
            project_ids = [str(project["id"]) for project in projects_data]
        except Exception as e:
            logger.warning(f"Could not fetch user projects: {str(e)}")
            project_ids = []
    
    summary = {
        "user": {
            "id": target_user_id,
            "username": user_info.get("username") if user_info else username,
            "name": user_info.get("name") if user_info else "Unknown"
        },
        "timeframe": {
            "created_after": created_after,
            "created_before": created_before
        },
        "total_approvals": 0,
        "projects": []
    }
    
    for project_id in project_ids:
        try:
            # Get approvals for this project
            project_approvals = get_user_approved_merge_requests(
                ctx=ctx,
                project_id=project_id,
                user_id=target_user_id,
                created_after=created_after,
                created_before=created_before,
                state=state,
                limit=100
            )
            
            if project_approvals["statistics"]["total_approved"] > 0:
                # Get project info
                project_endpoint = f"projects/{quote(project_id, safe='')}"
                project_info = make_gitlab_api_request(ctx, project_endpoint)
                
                project_summary = {
                    "project_id": project_id,
                    "project_name": project_info.get("name", "Unknown"),
                    "project_path": project_info.get("path_with_namespace", "Unknown"),
                    "total_approvals": project_approvals["statistics"]["total_approved"],
                    "approved_mrs": [
                        {
                            "iid": mr["merge_request"]["iid"],
                            "title": mr["merge_request"]["title"],
                            "approved_at": mr["approved_at"],
                            "web_url": mr["merge_request"]["web_url"]
                        }
                        for mr in project_approvals["approved_merge_requests"]
                    ]
                }
                
                summary["projects"].append(project_summary)
                summary["total_approvals"] += project_approvals["statistics"]["total_approved"]
                
        except Exception as e:
            logger.warning(f"Could not fetch approvals for project {project_id}: {str(e)}")
            continue
    
    # Sort projects by total approvals
    summary["projects"].sort(key=lambda x: x["total_approvals"], reverse=True)
    
    return summary

@mcp.tool()
def get_monthly_approval_trends(
    ctx: Context,
    project_id: str,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    months_back: int = 6
) -> Dict[str, Any]:
    """
    Get monthly approval trends for a project or specific user.
    
    Args:
        project_id: The GitLab project ID or URL-encoded path
        user_id: Optional user ID to focus on specific user trends
        username: Optional username to focus on specific user trends
        months_back: Number of months to look back from current date
    Returns:
        Dict containing monthly approval trends and statistics
    """
    from datetime import datetime, timedelta
    import calendar
    
    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=months_back * 30)  # Approximate months
    
    # Get user ID if username provided
    target_user_id = user_id
    if not target_user_id and username:
        users_endpoint = f"users?username={quote(username, safe='')}"
        users_info = make_gitlab_api_request(ctx, users_endpoint)
        if not users_info:
            raise ValueError(f"User '{username}' not found")
        target_user_id = str(users_info[0]["id"])
    
    # Generate monthly buckets
    monthly_stats = {}
    current_date = start_date.replace(day=1)  # Start from first day of month
    
    while current_date <= end_date:
        month_key = current_date.strftime("%Y-%m")
        monthly_stats[month_key] = {
            "month": current_date.strftime("%B %Y"),
            "total_approvals": 0,
            "unique_approvers": set(),
            "approved_mrs": []
        }
        
        # Move to next month
        if current_date.month == 12:
            current_date = current_date.replace(year=current_date.year + 1, month=1)
        else:
            current_date = current_date.replace(month=current_date.month + 1)
    
    # Get all merge requests in the timeframe
    created_after = start_date.strftime("%Y-%m-%dT00:00:00Z")
    created_before = end_date.strftime("%Y-%m-%dT23:59:59Z")
    
    if target_user_id:
        # Get approvals for specific user
        approval_data = get_user_approved_merge_requests(
            ctx=ctx,
            project_id=project_id,
            user_id=target_user_id,
            created_after=created_after,
            created_before=created_before,
            limit=500
        )
        
        for mr_data in approval_data["approved_merge_requests"]:
            approved_at = mr_data.get("approved_at")
            if approved_at:
                approved_date = datetime.fromisoformat(approved_at.replace('Z', '+00:00'))
                month_key = approved_date.strftime("%Y-%m")
                
                if month_key in monthly_stats:
                    monthly_stats[month_key]["total_approvals"] += 1
                    monthly_stats[month_key]["approved_mrs"].append({
                        "iid": mr_data["merge_request"]["iid"],
                        "title": mr_data["merge_request"]["title"],
                        "approved_at": approved_at
                    })
    else:
        # Get project-wide approval statistics
        project_stats = get_project_approval_statistics(
            ctx=ctx,
            project_id=project_id,
            created_after=created_after,
            created_before=created_before,
            limit=500
        )
        
        for user_data in project_stats["users"]:
            for mr in user_data["approved_mrs"]:
                approved_at = mr.get("approved_at")
                if approved_at:
                    approved_date = datetime.fromisoformat(approved_at.replace('Z', '+00:00'))
                    month_key = approved_date.strftime("%Y-%m")
                    
                    if month_key in monthly_stats:
                        monthly_stats[month_key]["total_approvals"] += 1
                        monthly_stats[month_key]["unique_approvers"].add(user_data["user_id"])
                        monthly_stats[month_key]["approved_mrs"].append({
                            "iid": mr["mr_iid"],
                            "title": mr["mr_title"],
                            "approved_at": approved_at,
                            "approver": user_data["username"]
                        })
    
    # Convert sets to counts and format results
    formatted_stats = []
    for month_key in sorted(monthly_stats.keys()):
        stats = monthly_stats[month_key]
        formatted_stats.append({
            "month": stats["month"],
            "month_key": month_key,
            "total_approvals": stats["total_approvals"],
            "unique_approvers": len(stats["unique_approvers"]) if not target_user_id else None,
            "approved_mrs": stats["approved_mrs"]
        })
    
    return {
        "project_id": project_id,
        "user_id": target_user_id,
        "timeframe": {
            "start_date": created_after,
            "end_date": created_before,
            "months_analyzed": len(formatted_stats)
        },
        "monthly_trends": formatted_stats,
        "summary": {
            "total_approvals": sum(month["total_approvals"] for month in formatted_stats),
            "average_monthly_approvals": round(
                sum(month["total_approvals"] for month in formatted_stats) / len(formatted_stats), 2
            ) if formatted_stats else 0
        }
    }

@mcp.tool()
def get_group_projects(ctx: Context, group_id: str, per_page: int = 100) -> List[Dict[str, Any]]:
    """
    Get all projects within a GitLab group.
    
    Args:
        group_id: The GitLab group ID or path
        per_page: Number of projects to return per page (max 100)
    Returns:
        List of project objects in the group
    """
    # Get projects in the group
    projects_endpoint = f"groups/{quote(group_id, safe='')}/projects?per_page={per_page}&include_subgroups=true"
    projects_info = make_gitlab_api_request(ctx, projects_endpoint)
    
    return projects_info

@mcp.tool()
def get_user_approvals_across_group(
    ctx: Context,
    group_id: str,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    state: str = "merged",
    limit_per_project: int = 50
) -> Dict[str, Any]:
    """
    Get merge requests approved by a specific user across all projects in a GitLab group.
    
    Args:
        group_id: The GitLab group ID or path
        user_id: The GitLab user ID (either user_id or username must be provided)
        username: The GitLab username (either user_id or username must be provided)
        created_after: ISO 8601 formatted date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)
        created_before: ISO 8601 formatted date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)
        state: Filter merge requests by state (all, opened, closed, merged, or locked)
        limit_per_project: Maximum number of merge requests to check per project
    Returns:
        Dict containing approved merge requests across all projects in the group
    """
    # First, get the user info if username is provided instead of user_id
    target_user_id = user_id
    if not target_user_id and username:
        users_endpoint = f"users?username={quote(username, safe='')}"
        users_info = make_gitlab_api_request(ctx, users_endpoint)
        if not users_info:
            raise ValueError(f"User '{username}' not found")
        target_user_id = str(users_info[0]["id"])
    
    if not target_user_id:
        raise ValueError("Either user_id or username must be provided")
    
    # Get all projects in the group
    projects = get_group_projects(ctx, group_id)
    
    group_summary = {
        "group_id": group_id,
        "user_id": target_user_id,
        "timeframe": {
            "created_after": created_after,
            "created_before": created_before
        },
        "total_approvals": 0,
        "total_projects_checked": len(projects),
        "projects_with_approvals": 0,
        "projects": []
    }
    
    for project in projects:
        project_id = str(project["id"])
        project_path = project["path_with_namespace"]
        
        try:
            # Get approvals for this project
            project_approvals = get_user_approved_merge_requests(
                ctx=ctx,
                project_id=project_id,
                user_id=target_user_id,
                created_after=created_after,
                created_before=created_before,
                state=state,
                limit=limit_per_project
            )
            
            if project_approvals["statistics"]["total_approved"] > 0:
                project_summary = {
                    "project_id": project_id,
                    "project_name": project["name"],
                    "project_path": project_path,
                    "project_web_url": project["web_url"],
                    "total_approvals": project_approvals["statistics"]["total_approved"],
                    "approved_mrs": [
                        {
                            "iid": mr["merge_request"]["iid"],
                            "title": mr["merge_request"]["title"],
                            "approved_at": mr["approved_at"],
                            "web_url": mr["merge_request"]["web_url"]
                        }
                        for mr in project_approvals["approved_merge_requests"]
                    ]
                }
                
                group_summary["projects"].append(project_summary)
                group_summary["total_approvals"] += project_approvals["statistics"]["total_approved"]
                group_summary["projects_with_approvals"] += 1
                
        except Exception as e:
            logger.warning(f"Could not fetch approvals for project {project_path}: {str(e)}")
            continue
    
    # Sort projects by total approvals
    group_summary["projects"].sort(key=lambda x: x["total_approvals"], reverse=True)
    
    return group_summary

if __name__ == "__main__":
    try:
        logger.info("Starting GitLab Review MCP server")
        # Initialize and run the server
        mcp.run(transport='stdio')
    except Exception as e:
        logger.error(f"Failed to start MCP server: {str(e)}")
        raise