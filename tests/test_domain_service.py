from __future__ import annotations

from typing import Protocol

from pydomain.ddd.domain_service import DomainService


class TestDomainServiceMarker:
    """The DomainService base class is a marker — no state or behaviour."""

    def test_is_subclassable(self) -> None:
        """Concrete services must be able to subclass DomainService."""

        class FundsTransferService(DomainService):
            pass

        assert issubclass(FundsTransferService, DomainService)

    def test_instance_is_domain_service(self) -> None:
        """An instance of a subclass should be recognised as DomainService."""

        class PricingService(DomainService):
            pass

        svc = PricingService()
        assert isinstance(svc, DomainService)

    def test_has_no_default_state(self) -> None:
        """The marker itself carries no attributes or mutable state."""

        class TaxCalculator(DomainService):
            pass

        svc = TaxCalculator()
        assert len(svc.__dict__) == 0

    def test_subclass_can_accept_dependencies(self) -> None:
        """Concrete services may accept injected dependencies via __init__."""

        class RateProvider(Protocol):
            def get_rate(self, src: str, dst: str) -> float: ...

        class FakeRates:
            def get_rate(self, src: str, dst: str) -> float:
                return 1.0

        class CurrencyConversionService(DomainService):
            def __init__(self, rate_provider: RateProvider) -> None:
                self._rates = rate_provider

            def convert(self, amount: float, target: str) -> float:
                rate = self._rates.get_rate("USD", target)
                return amount * rate

        rates = FakeRates()
        svc = CurrencyConversionService(rates)
        assert svc.convert(100.0, "EUR") == 100.0

    def test_multiple_instances_independent(self) -> None:
        """Each service instance should be independent (no shared state)."""

        class AuditService(DomainService):
            def __init__(self, name: str) -> None:
                self._name = name

            @property
            def name(self) -> str:
                return self._name

        audit_a = AuditService("orders")
        audit_b = AuditService("payments")
        assert audit_a.name != audit_b.name


class TestDomainServiceProtocol:
    """Domain services must look right at the protocol / type-check level."""

    def test_isinstance_with_protocol_like_check(self) -> None:
        """DomainService subclass instances pass structural checks."""

        class MyService(DomainService):
            pass

        svc = MyService()
        assert isinstance(svc, DomainService)

    def test_importable_from_package(self) -> None:
        """DomainService must be importable from pydomain.ddd."""
        from pydomain.ddd import DomainService as DDDomainService

        assert DDDomainService is DomainService


class TestStandaloneFunctionAlternative:
    """The KB (DCE-A-9) recommends standalone functions over classes
    where no persistent state is needed. The DomainService marker exists
    for when a class form is the right call — this test group documents
    both idioms working together."""

    def test_standalone_function_fulfills_service_role(self) -> None:
        """A plain function can fulfil the role of a DomainService
        when no injected dependencies are needed."""

        def calculate_tax(amount: float, rate: float) -> float:
            return amount * rate

        assert calculate_tax(100.0, 0.20) == 20.0

    def test_function_and_class_interoperate(self) -> None:
        """A DomainService subclass and a standalone function should
        interoperate — the function can be used inside the class."""

        def compute_discount(amount: float, tier: str) -> float:
            discounts = {"gold": 0.2, "silver": 0.1, "bronze": 0.05}
            return amount * discounts.get(tier, 0.0)

        class BillingService(DomainService):
            def calculate_total(
                self, amount: float, tier: str, tax_rate: float
            ) -> float:
                discounted = amount - compute_discount(amount, tier)
                return discounted * (1 + tax_rate)

        svc = BillingService()
        total = svc.calculate_total(100.0, "gold", 0.20)
        assert total == 96.0  # (100 - 20) * 1.20
