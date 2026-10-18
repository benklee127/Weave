"""Unit-test configuration: registers custom markers (pyproject uses --strict-markers)."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "docker: requires a reachable Docker daemon and a local image with pytest"
    )
