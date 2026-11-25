"""Actual SeekDB SQL with labeled synthetic vectors; never a real-model score."""
import pytest

from app.core.db import connect
from app.evaluation.retrieval import experiment_database, run_case
from app.models.embedding import EmbeddingBatch, EmbeddingError
from app.retrieval.search import search


class TestEmbedding:
    __test__ = False

    def __init__(self, fail_query=False, different_profile=False):
        self.calls = []
        self.fail_query = fail_query
        self.different_profile = different_profile

    def embed(self, texts, *, kind):
        self.calls.append((kind, list(texts)))
        if kind == "query" and self.fail_query:
            raise EmbeddingError("TEST_FAILURE", "Fixed simulated failure")
        vectors = ((0.0, 1.0, 0.0), (0.1, 0.9, 0.0), (1.0, 0.0, 0.0), (0.2, 0.8, 0.0)) if kind == "document" else ((0.0, 1.0, 0.0),)
        profile = "test-double:3d"
        if kind == "query" and self.different_profile: profile = "different-profile"
        return EmbeddingBatch(vectors, profile, "not-a-real-model", None, None, 0)


@pytest.mark.integration
def test_business_schema_sql_and_visibility_variants():
    provider = TestEmbedding()
    report = run_case(provider, dimensions=3, source="synthetic_test_double")
    assert report["passed"] and report["temporary_database_cleaned"]
    assert report["authorized_candidate_ids"] == ["A", "B", "C"]
    assert [r["document_id"] for r in report["results"]] == ["A", "B"]
    assert report["metrics"]["recall_at_2"] == 1
    assert report["reported_total_tokens"] is None
    assert len(report["visibility_variants"]) == 13
    assert all(report["checks"].values())
    assert [kind for kind, _ in provider.calls] == ["document", "query"]
    assert len(provider.calls[0][1]) == 4


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["upstream_failure", "different_profile"])
def test_failed_experiment_preserves_usage_and_cleans_database(mode):
    provider = TestEmbedding(fail_query=mode == "upstream_failure", different_profile=mode == "different_profile")
    report = {}
    with pytest.raises((EmbeddingError, ValueError)):
        run_case(provider, dimensions=3, source="synthetic_test_double", report=report)
    assert report["temporary_database_cleaned"]
    assert len(report["calls"]) == (1 if mode == "upstream_failure" else 2)
    assert len(provider.calls) == 2


@pytest.mark.integration
def test_only_own_temporary_database_is_dropped():
    with experiment_database(3) as database:
        name = database.name
    assert database.cleaned
    connection = connect(root=True, database="")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (name,))
            assert cursor.fetchone() is None
    finally:
        connection.close()


@pytest.mark.parametrize("vector,dimensions,top_k", [([1, 0], 3, 2), ([0, 0, 0], 3, 2),
    ([float("nan"), 1, 0], 3, 2), ([True, 1, 0], 3, 2), ([1, 0, 0], 3, 0), ([1, 0, 0], 3, 21)])
def test_invalid_query_does_not_reach_sql(vector, dimensions, top_k):
    class NeverCursor:
        def execute(self, *args): pytest.fail("Invalid query reached database")
    with pytest.raises(ValueError):
        search(NeverCursor(), actor_id=11, course_id=1, profile="test", query_vector=vector, dimensions=dimensions, top_k=top_k)
