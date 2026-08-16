import hashlib
import json
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable

from importlib_resources import files

from famly_fetch.network import open_famly_api, validate_api_base_url


class GraphQLResponseError(RuntimeError):
    """Raised when Famly returns GraphQL errors in an HTTP 200 response."""


class AuthenticationError(RuntimeError):
    """Raised when Famly rejects or cannot complete authentication."""


ChallengeResolver = Callable[[dict], dict]


def get_device_id() -> str:
    """
    Generates a consistent device identifier as an UUID string.
    This function retrieves the hardware address as a 48-bit positive integer using `uuid.getnode()`,
    converts it to a hexadecimal string, hashes it using MD5 to ensure privacy and consistency,
    and then formats the hash as a UUID string.
    Returns:
        str: A 128-bit UUID string representing the device identifier.
    """

    raw_id = hex(uuid.getnode())
    # Hash + convert to UUID format (ensures consistent 128-bit UUID string)
    return str(uuid.UUID(hashlib.md5(raw_id.encode()).hexdigest()))


class ApiClient:
    _access_token = None

    def __init__(
        self,
        base_url: str,
        user_agent: str | None = None,
        access_token: str | None = None,
    ):
        """
        Initialize the ApiClient.

        Args:
            user_agent (str): The user agent to use for requests.
            access_token (str): Optional access token to use directly.
        """
        self._user_agent: str | None = user_agent
        self._device_id = get_device_id()
        self._access_token = access_token
        self._base = validate_api_base_url(base_url)

    @staticmethod
    def _authentication_failure(result: dict) -> AuthenticationError:
        details = result.get("errorDetails") or result.get("errorTitle")
        return AuthenticationError(str(details or "Famly authentication failed"))

    def _accept_authentication_result(
        self,
        result: dict,
        challenge_resolver: ChallengeResolver | None,
    ) -> None:
        result_type = result.get("__typename")
        if result_type == "AuthenticationSucceeded":
            access_token = result.get("accessToken")
            if not access_token:
                raise AuthenticationError(
                    "Famly reported a successful login without an access token"
                )
            self._access_token = access_token
            return
        if result_type == "AuthenticationFailed":
            raise self._authentication_failure(result)
        if result_type != "AuthenticationChallenged":
            raise AuthenticationError("Famly returned an unknown authentication result")
        if challenge_resolver is None:
            raise AuthenticationError(
                "Famly requires a login context or two-factor authentication"
            )
        if result.get("requiredMfaSetup"):
            raise AuthenticationError(
                "This account must finish two-factor setup in the Famly web app first"
            )

        answer_data = self.make_graphql_request(
            "AnswerChallenge", challenge_resolver(result)
        )
        answer_result = answer_data.get("me", {}).get("answerChallenge")
        if not isinstance(answer_result, dict):
            raise AuthenticationError("Famly returned no challenge result")
        self._accept_authentication_result(answer_result, None)

    def login(
        self,
        email,
        password,
        challenge_resolver: ChallengeResolver | None = None,
    ):
        """
        Authenticate with the Famly API and store the access token for future requests.

        Args:
            email (str): The user's email address.
            password (str): The user's password.

        Raises:
            Exception: If the server returns a non-200 HTTP status code.
        """

        login_data = self.make_graphql_request(
            "Authenticate",
            {
                "email": email,
                "password": password,
                "deviceId": self._device_id,
                "legacy": False,
            },
        )

        result = login_data.get("me", {}).get("authenticateWithPassword")
        if not isinstance(result, dict):
            raise AuthenticationError("Famly returned no authentication result")
        self._accept_authentication_result(result, challenge_resolver)

    def get_child_notes(self, childId, cursor=None, first=10):
        data = self.make_graphql_request(
            "GetChildNotes",
            {
                "noteTypes": ["Classic"],
                "childId": childId,
                "parentVisible": True,
                "safeguardingConcern": False,
                "sensitive": False,
                "limit": first,
                "cursor": cursor,
            },
        )

        return data["childNotes"]

    def learning_journey_query(self, childId, cursor=None, first=10):
        data = self.make_graphql_request(
            "LearningJourneyQuery",
            {
                "childId": childId,
                "variants": [
                    "REGULAR_OBSERVATION",
                    "PARENT_OBSERVATION",
                    "ASSESSMENT",
                    "TWO_YEAR_PROGRESS",
                    "UP_TO_SPEED_OBSERVATION",
                ],
                "first": first,
                "next": cursor,
            },
        )

        return data["childDevelopment"]["observations"]

    def make_graphql_request(self, method, variables):
        query = files("famly_fetch.graphql").joinpath(f"{method}.graphql").read_text()

        postBody = {"operationName": method, "variables": variables, "query": query}

        data = self.make_api_request(
            "POST",
            f"/graphql?{method}",
            body=postBody,
        )
        errors = data.get("errors") if isinstance(data, dict) else None
        if errors:
            messages = []
            for error in errors:
                if isinstance(error, dict) and error.get("message"):
                    messages.append(str(error["message"]))
                else:
                    messages.append("Unknown GraphQL error")
            summary = "; ".join(messages[:3])
            if len(messages) > 3:
                summary += f"; and {len(messages) - 3} more"
            raise GraphQLResponseError(f"{method} failed: {summary}")
        if not isinstance(data, dict) or "data" not in data or data["data"] is None:
            raise GraphQLResponseError(f"{method} returned no GraphQL data")
        return data["data"]

    def make_api_request(self, method, path, body=None, params=None):
        """
        Make a request to the Famly API and return the response.

        Args:
            method (str): The HTTP method to use for the request (e.g., "GET", "POST").
            path (str): The path of the API endpoint (e.g., "/graphql?Authenticate").
            body (dict, optional): The body of the request. Defaults to None.
            params (dict, optional): The query parameters to include in the request. Defaults to None.

        Returns:
            dict: The JSON response from the server.

        Raises:
            urllib.error.HTTPError: If the server couldn't fulfill the request.
            Exception: If the server returns a non-200 HTTP status code.
        """

        b = None
        if body:
            b = json.dumps(body).encode("utf-8")

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._user_agent:
            headers["User-Agent"] = self._user_agent

        # If we already have the token, use it
        if self._access_token:
            headers["x-famly-accesstoken"] = self._access_token

        url = self._base + path

        if params:
            query_string = urllib.parse.urlencode(params)
            url += "?" + query_string

        req = urllib.request.Request(url=url, headers=headers, method=method, data=b)
        try:
            with open_famly_api(req) as f:
                body = f.read().decode("utf-8")
                if f.status != 200:
                    raise Exception(f"Broken! {body}")

                return json.loads(body)
        except urllib.error.HTTPError:
            # Preserve the failure so the CLI returns a non-zero exit status.
            raise

    def feed(
        self,
        cursor: str | None = None,
        older_than: str | None = None,
        limit: int | None = None,
    ):
        params = {}
        if cursor:
            params["cursor"] = cursor
        if older_than:
            params["olderThan"] = older_than
        if limit:
            params["first"] = limit
        return self.make_api_request("GET", "/api/feed/feed/feed", params=params)

    def me_me_me(self):
        """
        Get information about the currently authenticated user.

        Returns:
            dict: The JSON response from the server.

        Raises:
            urllib.error.HTTPError: If the server couldn't fulfill the request.
            Exception: If the server returns a non-200 HTTP status code.
        """

        return self.make_api_request("GET", "/api/me/me/me")

    def get_relations(self, child_id: str) -> list[dict]:
        """
        Get the relations of a given child ID.

        Args:
            child_id (str): The ID of the child.

        Returns:
            list[dict]: A list of dictionary representing the relations.
        """
        return self.make_api_request(
            "GET", "/api/v2/relations", params={"childId": child_id}
        )
