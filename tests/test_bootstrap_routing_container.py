import pytest

import loveapp.bootstrap as bootstrap_module
from loveapp.application.routing import HybridRouter
from loveapp.bootstrap import RoutingContainer, build_container, build_routing_container
from loveapp.safety import SafetyPolicy


class _ClosableAdapter:
    def __init__(self, name: str, close_order: list[str]) -> None:
        self.name = name
        self.close_order = close_order
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1
        self.close_order.append(self.name)


@pytest.mark.asyncio
async def test_routing_container_closes_owned_adapters_once_in_reverse_order(
    app_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_order: list[str] = []
    corrector = _ClosableAdapter("corrector", close_order)
    parser = _ClosableAdapter("parser", close_order)
    monkeypatch.setattr(bootstrap_module, "_build_route_corrector", lambda settings: corrector)
    monkeypatch.setattr(bootstrap_module, "_build_date_semantic_parser", lambda settings: parser)

    container = build_routing_container(app_settings)
    await container.aclose()

    assert container.resources == (corrector, parser)
    assert close_order == ["parser", "corrector"]
    assert corrector.close_calls == 1
    assert parser.close_calls == 1


@pytest.mark.asyncio
async def test_routing_container_deduplicates_shared_adapter_resource(
    app_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_order: list[str] = []
    shared_adapter = _ClosableAdapter("shared", close_order)
    monkeypatch.setattr(
        bootstrap_module,
        "_build_route_corrector",
        lambda settings: shared_adapter,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "_build_date_semantic_parser",
        lambda settings: shared_adapter,
    )

    container = build_routing_container(app_settings)
    await container.aclose()

    assert container.resources == (shared_adapter,)
    assert close_order == ["shared"]
    assert shared_adapter.close_calls == 1


@pytest.mark.asyncio
async def test_app_container_reuses_routing_container_and_owns_its_resources_once(
    app_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_order: list[str] = []
    corrector = _ClosableAdapter("corrector", close_order)
    safety_policy = SafetyPolicy()
    router = HybridRouter(safety_policy, corrector)
    routing_container = RoutingContainer(
        router=router,
        safety_policy=safety_policy,
        resources=(corrector,),
    )
    calls = []

    def fake_build_routing_container(settings):
        calls.append(settings)
        return routing_container

    monkeypatch.setattr(
        bootstrap_module,
        "build_routing_container",
        fake_build_routing_container,
    )

    container = build_container(app_settings)
    try:
        assert calls == [app_settings]
        assert container.router is router
        assert container.resources.count(corrector) == 1
    finally:
        await container.aclose()

    assert close_order == ["corrector"]
    assert corrector.close_calls == 1
