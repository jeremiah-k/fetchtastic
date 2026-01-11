import requests


def _block_network(*_args, **_kwargs):
    raise RuntimeError(
        "Network access is blocked during tests. Mock requests.* or Session.request."
    )


def pytest_runtest_setup():
    requests.get = _block_network
    requests.post = _block_network
    requests.put = _block_network
    requests.delete = _block_network
    requests.head = _block_network
    requests.Session.request = _block_network
