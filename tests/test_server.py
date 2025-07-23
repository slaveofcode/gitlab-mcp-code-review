import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

# This is a basic test skeleton for the server
# You would need to add more comprehensive tests


class TestGitLabMCP(unittest.TestCase):
    """Test cases for GitLab MCP server"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_ctx = MagicMock()
        self.mock_lifespan_context = MagicMock()
        self.mock_ctx.request_context.lifespan_context = self.mock_lifespan_context
        self.mock_lifespan_context.token = "fake_token"
        self.mock_lifespan_context.host = "gitlab.com"

    @patch('requests.get')
    def test_make_gitlab_api_request(self, mock_get):
        """Test the GitLab API request function"""
        # Import here to avoid module-level imports before patching
        from server import make_gitlab_api_request
        
        # Setup mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 123, "name": "test_project"}
        mock_get.return_value = mock_response
        
        # Test the function
        result = make_gitlab_api_request(self.mock_ctx, "projects/123")
        
        # Assertions
        mock_get.assert_called_once()
        self.assertEqual(result, {"id": 123, "name": "test_project"})


class TestApprovalAnalytics(unittest.TestCase):
    """Test cases for the new approval analytics functions"""

    def setUp(self):
        """Set up test fixtures for approval analytics tests"""
        self.mock_ctx = MagicMock()
        self.mock_lifespan_context = MagicMock()
        self.mock_ctx.request_context.lifespan_context = self.mock_lifespan_context
        self.mock_lifespan_context.token = "fake_token"
        self.mock_lifespan_context.host = "gitlab.com"
        
        # Sample data for testing
        self.sample_user = {"id": 100, "username": "john.doe", "name": "John Doe"}
        self.sample_mr = {
            "iid": 5,
            "title": "Test MR",
            "created_at": "2024-01-15T10:00:00Z",
            "web_url": "https://gitlab.com/project/repo/-/merge_requests/5"
        }
        self.sample_approval = {
            "approved_by": [
                {
                    "user": self.sample_user,
                    "created_at": "2024-01-15T11:00:00Z"
                }
            ]
        }

    @patch('server.make_gitlab_api_request')
    def test_get_user_approved_merge_requests_with_username(self, mock_api_request):
        """Test getting user approved MRs using username"""
        from server import get_user_approved_merge_requests
        
        # Setup mock responses
        mock_api_request.side_effect = [
            [self.sample_user],  # User lookup response
            [self.sample_mr],    # MRs response
            self.sample_approval  # Approval info response
        ]
        
        # Test the function
        result = get_user_approved_merge_requests(
            self.mock_ctx,
            project_id="123",
            username="john.doe",
            created_after="2024-01-01",
            created_before="2024-01-31"
        )
        
        # Assertions
        self.assertEqual(result["project_id"], "123")
        self.assertEqual(result["user_id"], "100")
        self.assertEqual(result["statistics"]["total_approved"], 1)
        self.assertEqual(len(result["approved_merge_requests"]), 1)
        self.assertEqual(mock_api_request.call_count, 3)

    @patch('server.make_gitlab_api_request')
    def test_get_user_approved_merge_requests_with_user_id(self, mock_api_request):
        """Test getting user approved MRs using user_id directly"""
        from server import get_user_approved_merge_requests
        
        # Setup mock responses
        mock_api_request.side_effect = [
            [self.sample_mr],    # MRs response
            self.sample_approval  # Approval info response
        ]
        
        # Test the function
        result = get_user_approved_merge_requests(
            self.mock_ctx,
            project_id="123",
            user_id="100",
            created_after="2024-01-01"
        )
        
        # Assertions
        self.assertEqual(result["user_id"], "100")
        self.assertEqual(result["statistics"]["total_approved"], 1)
        self.assertEqual(mock_api_request.call_count, 2)

    @patch('server.make_gitlab_api_request')
    def test_get_project_approval_statistics(self, mock_api_request):
        """Test getting project approval statistics"""
        from server import get_project_approval_statistics
        
        # Setup mock responses
        mock_api_request.side_effect = [
            [self.sample_mr],    # MRs response
            self.sample_approval  # Approval info response
        ]
        
        # Test the function
        result = get_project_approval_statistics(
            self.mock_ctx,
            project_id="123",
            created_after="2024-01-01",
            created_before="2024-01-31"
        )
        
        # Assertions
        self.assertEqual(result["project_id"], "123")
        self.assertEqual(result["statistics"]["total_merge_requests"], 1)
        self.assertEqual(result["statistics"]["total_approvals"], 1)
        self.assertEqual(result["statistics"]["unique_approvers"], 1)
        self.assertEqual(len(result["users"]), 1)
        self.assertEqual(result["users"][0]["username"], "john.doe")

    @patch('server.get_user_approved_merge_requests')
    @patch('server.make_gitlab_api_request')
    def test_get_user_approval_summary(self, mock_api_request, mock_get_user_approved):
        """Test getting user approval summary across projects"""
        from server import get_user_approval_summary
        
        # Setup mock responses
        mock_api_request.side_effect = [
            [self.sample_user],  # User lookup response
            {"name": "Project 1", "path_with_namespace": "group/project1"},  # Project 1 info
            {"name": "Project 2", "path_with_namespace": "group/project2"}   # Project 2 info
        ]
        
        # Mock approval responses for projects
        mock_get_user_approved.side_effect = [
            {
                "statistics": {"total_approved": 2},
                "approved_merge_requests": [
                    {"merge_request": {"iid": 1, "title": "MR 1", "web_url": "url1"}, "approved_at": "2024-01-10T10:00:00Z"},
                    {"merge_request": {"iid": 2, "title": "MR 2", "web_url": "url2"}, "approved_at": "2024-01-15T10:00:00Z"}
                ]
            },
            {
                "statistics": {"total_approved": 1},
                "approved_merge_requests": [
                    {"merge_request": {"iid": 3, "title": "MR 3", "web_url": "url3"}, "approved_at": "2024-01-20T10:00:00Z"}
                ]
            }
        ]
        
        # Test the function
        result = get_user_approval_summary(
            self.mock_ctx,
            username="john.doe",
            project_ids=["123", "456"],
            created_after="2024-01-01"
        )
        
        # Assertions
        self.assertEqual(result["user"]["username"], "john.doe")
        self.assertEqual(result["total_approvals"], 3)
        self.assertEqual(len(result["projects"]), 2)
        self.assertEqual(result["projects"][0]["total_approvals"], 2)  # Sorted by total_approvals desc
        self.assertEqual(result["projects"][1]["total_approvals"], 1)

    @patch('server.get_user_approved_merge_requests')
    @patch('server.make_gitlab_api_request')
    def test_get_monthly_approval_trends_for_user(self, mock_api_request, mock_get_user_approved):
        """Test getting monthly approval trends for a specific user"""
        from server import get_monthly_approval_trends
        
        # Setup mock responses
        mock_api_request.return_value = [self.sample_user]  # User lookup response
        
        # Use current datetime for more predictable results
        from datetime import datetime
        current_date = datetime.now()
        current_month = current_date.strftime("%Y-%m")
        
        # Mock approval data with current month dates
        approved_date = current_date.strftime("%Y-%m-%dT10:00:00Z")
        mock_get_user_approved.return_value = {
            "approved_merge_requests": [
                {
                    "merge_request": {"iid": 1, "title": "MR 1"},
                    "approved_at": approved_date
                },
                {
                    "merge_request": {"iid": 2, "title": "MR 2"},
                    "approved_at": approved_date
                },
                {
                    "merge_request": {"iid": 3, "title": "MR 3"},
                    "approved_at": approved_date
                }
            ]
        }
        
        # Test the function
        result = get_monthly_approval_trends(
            self.mock_ctx,
            project_id="123",
            username="john.doe",
            months_back=3
        )
        
        # Assertions
        self.assertEqual(result["project_id"], "123")
        self.assertEqual(result["user_id"], "100")
        self.assertGreater(len(result["monthly_trends"]), 0)
        self.assertEqual(result["summary"]["total_approvals"], 3)
        
        # Check that current month has the approvals
        monthly_totals = {trend["month_key"]: trend["total_approvals"] for trend in result["monthly_trends"]}
        self.assertEqual(monthly_totals.get(current_month, 0), 3)

    def test_get_user_approved_merge_requests_invalid_input(self):
        """Test error handling for invalid input"""
        from server import get_user_approved_merge_requests
        
        # Test with neither user_id nor username provided
        with self.assertRaises(ValueError) as context:
            get_user_approved_merge_requests(
                self.mock_ctx,
                project_id="123"
            )
        
        self.assertIn("Either user_id or username must be provided", str(context.exception))

    @patch('server.make_gitlab_api_request')
    def test_get_user_approved_merge_requests_user_not_found(self, mock_api_request):
        """Test error handling when user is not found"""
        from server import get_user_approved_merge_requests
        
        # Setup mock response for user not found
        mock_api_request.return_value = []  # Empty response means user not found
        
        # Test the function
        with self.assertRaises(ValueError) as context:
            get_user_approved_merge_requests(
                self.mock_ctx,
                project_id="123",
                username="nonexistent_user"
            )
        
        self.assertIn("User 'nonexistent_user' not found", str(context.exception))


if __name__ == '__main__':
    unittest.main() 