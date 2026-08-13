import unittest

from famly_fetch.api_client import ApiClient


class ApiClientJourneyTests(unittest.TestCase):
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
