from src.server import sampling


def test_create_sampling_handler_returns_none_without_dependencies(monkeypatch):
    monkeypatch.setattr(sampling, "OpenAISamplingHandler", None)
    monkeypatch.setattr(sampling, "OpenAI", None)

    handler = sampling.create_sampling_handler()

    assert handler is None


def test_create_sampling_handler_initializes_client(monkeypatch):
    monkeypatch.setenv("FASTMCP_SAMPLING_API_KEY", "sample-key")
    monkeypatch.setenv("FASTMCP_SAMPLING_MODEL", "sample-model")
    monkeypatch.delenv("FASTMCP_SAMPLING_BASE_URL", raising=False)

    created_clients = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created_clients.append(kwargs)

    class FakeHandler:
        def __init__(self, *, default_model, client):
            self.default_model = default_model
            self.client = client

    monkeypatch.setattr(sampling, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(sampling, "OpenAISamplingHandler", FakeHandler)

    handler = sampling.create_sampling_handler()

    assert isinstance(handler, FakeHandler)
    assert handler.default_model == "sample-model"
    assert created_clients == [{"api_key": "sample-key"}]
