from pydomain.ddd.specification import Specification


class IsEven(Specification):
    def is_satisfied_by(self, obj):
        return obj % 2 == 0


for depth in (100, 200, 500, 1000, 5000):
    spec = IsEven()
    for _ in range(depth):
        spec = spec.and_(IsEven())
    try:
        spec.model_dump()
        spec.model_dump_json()
        print(depth, "ok")
    except Exception as exc:  # noqa: BLE001
        print(depth, type(exc).__name__, str(exc)[:120])
