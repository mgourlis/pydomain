import asyncio, inspect, sys, re, types
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tests.docs import doc_examples as de

def unwrap(f):
    for attr in ("_get_wrapped_function",):
        if hasattr(f, attr):
            return getattr(f, attr)()
    return getattr(f, "__wrapped__", getattr(f, "_fixture_function", f))

def is_fixture(v):
    return type(v).__name__ in ("FixtureFunctionDefinition",) or hasattr(v, "_pytestfixturefunction")

async def resolve(name, inst, ns, cache, finals):
    if name in cache: return cache[name]
    f = getattr(type(inst), name, None) if inst is not None else None
    bound = True
    if f is None or not is_fixture(f):
        f = ns.get(name); bound = False
    if f is None or not is_fixture(f):
        raise LookupError(f"fixture {name} not found")
    fn = unwrap(f)
    params = list(inspect.signature(fn).parameters)
    if bound: params = params[1:]
    args = [await resolve(p, inst, ns, cache, finals) for p in params]
    call = (lambda *a: fn(inst, *a)) if bound else fn
    r = call(*args)
    if inspect.isasyncgen(r):
        v = await r.__anext__(); finals.append(r)
    elif inspect.isgenerator(r):
        v = next(r)
    elif inspect.iscoroutine(r):
        v = await r
    else:
        v = r
    cache[name] = v
    return v

for page in sys.argv[2:]:
    p = Path(page)
    blocks = de.parse_page(p)
    name = "docs_example_" + re.sub(r"\W", "_", p.stem)
    module = types.ModuleType(name); sys.modules[name] = module
    ns = module.__dict__
    runner = asyncio.Runner()
    for b in [b for b in blocks if b.kind=="setup"] + [b for b in blocks if b.kind in ("run","check")]:
        de._run_block(p, b, ns, runner)
    for k, v in list(ns.items()):
        if inspect.isclass(v) and k.startswith("Test"):
            for m, f in vars(v).items():
                if m.startswith("test_") and inspect.iscoroutinefunction(f):
                    inst = v()
                    async def go():
                        cache, finals = {}, []
                        params = list(inspect.signature(f).parameters)[1:]
                        args = [await resolve(q, inst, ns, cache, finals) for q in params]
                        await f(inst, *args)
                        for g in finals:
                            try: await g.__anext__()
                            except StopAsyncIteration: pass
                    try:
                        runner.run(asyncio.wait_for(go(), 5)); print(p.name, f"{k}.{m}", "OK")
                    except Exception as e:
                        print(p.name, f"{k}.{m}", "FAIL", type(e).__name__, str(e)[:160])
    runner.close()
