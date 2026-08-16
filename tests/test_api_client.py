import io
import unittest
import urllib.error
from unittest.mock import patch

from famly_fetch.api_client import (
    ApiClient,
    AuthenticationError,
    GraphQLResponseError,
)
from famly_fetch.network import BRIGHT_HORIZONS_API_BASE


class ApiClientAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.client = ApiClient.__new__(ApiClient)
        self.client._base = "https://app.famly.co"
        self.client._access_token = None
        self.client._device_id = "device-1"

    def test_password_authentication_accepts_an_immediate_success(self):
        self.client.make_graphql_request = lambda method, variables: {
            "me": {
                "authenticateWithPassword": {
                    "__typename": "AuthenticationSucceeded",
                    "accessToken": "access-token",
                }
            }
        }

        self.client.login("parent@example.com", "password")

        self.assertEqual(self.client._access_token, "access-token")

    def test_password_authentication_answers_two_factor_challenge(self):
        challenge = {
            "__typename": "AuthenticationChallenged",
            "deviceId": "device-1",
            "loginId": "login-1",
            "expiresAt": 123456,
            "choices": [],
        }
        calls = []

        def graphql_request(method, variables):
            calls.append((method, variables))
            if method == "Authenticate":
                return {"me": {"authenticateWithPassword": challenge}}
            return {
                "me": {
                    "answerChallenge": {
                        "__typename": "AuthenticationSucceeded",
                        "accessToken": "challenged-access-token",
                    }
                }
            }

        answer = {
            "deviceId": "device-1",
            "loginId": "login-1",
            "expiresAt": 123456,
            "userContextId": "context-1",
            "hmac": "signed-choice",
            "twoFactorCode": 123456,
            "recoveryCode": None,
        }
        self.client.make_graphql_request = graphql_request

        self.client.login(
            "parent@example.com",
            "password",
            challenge_resolver=lambda received: (
                answer if received is challenge else None
            ),
        )

        self.assertEqual(calls[1], ("AnswerChallenge", answer))
        self.assertEqual(self.client._access_token, "challenged-access-token")

    def test_password_authentication_reports_rejection_details(self):
        self.client.make_graphql_request = lambda method, variables: {
            "me": {
                "authenticateWithPassword": {
                    "__typename": "AuthenticationFailed",
                    "errorDetails": "Incorrect credentials",
                }
            }
        }

        with self.assertRaisesRegex(AuthenticationError, "Incorrect credentials"):
            self.client.login("parent@example.com", "password")

    def test_required_mfa_setup_must_be_completed_in_web_app(self):
        self.client.make_graphql_request = lambda method, variables: {
            "me": {
                "authenticateWithPassword": {
                    "__typename": "AuthenticationChallenged",
                    "requiredMfaSetup": {"availableMethods": ["TOTP"]},
                }
            }
        }

        with self.assertRaisesRegex(AuthenticationError, "finish two-factor setup"):
            self.client.login(
                "parent@example.com",
                "password",
                challenge_resolver=lambda challenge: {},
            )

    def test_bright_horizons_discovers_legacy_authentication_before_login(self):
        self.client._base = BRIGHT_HORIZONS_API_BASE
        calls = []

        def api_request(method, path, body=None, params=None):
            calls.append((method, path, body))
            return [
                {
                    "exists": True,
                    "local": True,
                    "passwordLoginAvailable": True,
                    "useUserContextAuthentication": False,
                }
            ]

        def graphql_request(method, variables):
            calls.append((method, variables))
            return {
                "me": {
                    "authenticateWithPassword": {
                        "__typename": "AuthenticationSucceeded",
                        "accessToken": "access-token",
                    }
                }
            }

        self.client.make_api_request = api_request
        self.client.make_graphql_request = graphql_request

        self.client.login("parent@example.com", "password")

        self.assertEqual(
            calls,
            [
                (
                    "POST",
                    "/api/v2/logins/exists",
                    {"email": "parent@example.com"},
                ),
                (
                    "Authenticate",
                    {
                        "email": "parent@example.com",
                        "password": "password",
                        "deviceId": "device-1",
                        "legacy": True,
                    },
                ),
            ],
        )

    def test_bright_horizons_keeps_user_context_authentication_when_enabled(self):
        self.client._base = BRIGHT_HORIZONS_API_BASE
        self.client.make_api_request = lambda *args, **kwargs: [
            {
                "exists": True,
                "local": True,
                "passwordLoginAvailable": True,
                "useUserContextAuthentication": True,
            }
        ]
        captured = {}

        def graphql_request(method, variables):
            captured.update(variables)
            return {
                "me": {
                    "authenticateWithPassword": {
                        "__typename": "AuthenticationSucceeded",
                        "accessToken": "access-token",
                    }
                }
            }

        self.client.make_graphql_request = graphql_request

        self.client.login("parent@example.com", "password")

        self.assertFalse(captured["legacy"])

    def test_bright_horizons_reports_browser_only_login(self):
        self.client._base = BRIGHT_HORIZONS_API_BASE
        self.client.make_api_request = lambda *args, **kwargs: [
            {
                "exists": True,
                "local": True,
                "passwordLoginAvailable": False,
                "mustUseOidc": True,
            }
        ]

        with self.assertRaisesRegex(AuthenticationError, "single sign-on"):
            self.client.login("parent@example.com", "password")


class ApiClientJourneyTests(unittest.TestCase):
    def test_graphql_errors_fail_even_when_partial_data_is_present(self):
        client = ApiClient.__new__(ApiClient)
        client.make_api_request = lambda *args, **kwargs: {
            "data": {"childDevelopment": {"observations": {"results": []}}},
            "errors": [{"message": "Synthetic partial-response failure"}],
        }

        with self.assertRaisesRegex(
            GraphQLResponseError, "Synthetic partial-response failure"
        ):
            client.make_graphql_request("LearningJourneyQuery", {})

    def test_graphql_response_requires_data(self):
        client = ApiClient.__new__(ApiClient)
        client.make_api_request = lambda *args, **kwargs: {"data": None}

        with self.assertRaisesRegex(GraphQLResponseError, "returned no GraphQL data"):
            client.make_graphql_request("LearningJourneyQuery", {})

    def test_http_errors_propagate_to_the_caller(self):
        client = ApiClient.__new__(ApiClient)
        client._base = "https://app.famly.co"
        client._user_agent = "test"
        client._access_token = "test-token"
        error = urllib.error.HTTPError(
            "https://app.famly.co/api/test",
            500,
            "Rejected synthetic request",
            {},
            io.BytesIO(b"synthetic error"),
        )

        with patch("famly_fetch.api_client.open_famly_api", side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                client.make_api_request("GET", "/api/test")
        error.close()

    def test_journey_query_requests_every_published_journey_variant(self):
        client = ApiClient.__new__(ApiClient)
        captured = {}

        def fake_graphql_request(method, variables):
            captured["method"] = method
            captured["variables"] = variables
            return {"childDevelopment": {"observations": {"results": [], "next": None}}}

        client.make_graphql_request = fake_graphql_request
        client.learning_journey_query("child-1")

        self.assertEqual(captured["method"], "LearningJourneyQuery")
        self.assertEqual(
            captured["variables"]["variants"],
            [
                "REGULAR_OBSERVATION",
                "PARENT_OBSERVATION",
                "ASSESSMENT",
                "TWO_YEAR_PROGRESS",
                "UP_TO_SPEED_OBSERVATION",
            ],
        )


if __name__ == "__main__":
    unittest.main()
