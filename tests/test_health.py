from utils.health import bind_port, on_render


def test_on_render(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME", raising=False)
    assert on_render() is False
    monkeypatch.setenv("RENDER", "true")
    assert on_render() is True


def test_bind_port_only_when_hosted(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME", raising=False)
    assert bind_port() is None
    monkeypatch.setenv("PORT", "8765")
    assert bind_port() == 8765
