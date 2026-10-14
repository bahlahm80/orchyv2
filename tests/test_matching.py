from orchy.matching import heuristic_score


def test_engineering_role_scores_higher_than_sales():
    resume = "Software engineer Python FastAPI SQLite REST APIs"
    eng = heuristic_score(
        resume,
        "Backend Software Engineer",
        "Python FastAPI services and SQLite data stores",
    )
    sales = heuristic_score(
        resume,
        "Enterprise Account Executive",
        "Quota carrying SaaS sales cold outbound closing deals",
    )
    assert eng.score > sales.score
