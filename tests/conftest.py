import requests

_NETWORK_BLOCK_MSG = (
    "Network access is blocked during tests. " "Mock requests.* or Session.request."
)


def _block_network(*_args, **_kwargs):
    """
    Raise a RuntimeError indicating network access is blocked during tests.
    
    This function is intended to replace network request callables and always raises a RuntimeError with the message stored in `_NETWORK_BLOCK_MSG`.
    
    Raises:
        RuntimeError: with `_NETWORK_BLOCK_MSG` explaining that network access is blocked and suggesting mocking requests or Session.request.
    """
    raise RuntimeError(_NETWORK_BLOCK_MSG)


def pytest_runtest_setup():
    """
    Disable real network requests during pytest runs by patching requests' HTTP entry points.
    
    Patches requests.get, requests.post, requests.put, requests.delete, requests.head and requests.Session.request so that any call raises a RuntimeError with a message indicating network access is blocked during tests.
    """
    requests.get = _block_network
    requests.post = _block_network
    requests.put = _block_network
    requests.delete = _block_network
    requests.head = _block_network
    requests.Session.request = _block_network