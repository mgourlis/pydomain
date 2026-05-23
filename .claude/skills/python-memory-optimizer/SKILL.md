---
name: python-memory-optimizer
description: >-
  Analyze Python code for memory inefficiencies, detect leaks, and apply
  optimization patterns. Use when asked to reduce memory footprint, fix
  memory leaks, optimize data structures for memory, handle large datasets
  efficiently, profile memory usage, or diagnose OOM issues. Covers object
  sizing, generator patterns, efficient data structures, leak detection,
  and memory profiling strategies.
when_to_use: >-
  Triggers on: "optimize memory usage", "find memory leaks", "reduce memory
  footprint", "profile memory of [file.py]", "fix OOM error", "memory-efficient
  alternative", "high RSS", "memory leak in my app", "apply memory-safe
  patterns", "this crashes with MemoryError", "too much memory", "memory
  profiling", "memory optimization", "large dataset processing memory".
argument-hint: [file-or-module]
arguments: [target]
allowed-tools: Read Grep Glob Bash
effort: max
---

# Python Memory Optimizer

## Workflow

1. **Scope** -- Clarify: short-lived script or long-lived service? Typical input size? OOM errors observed?
2. **Profile** -- Run profiling to identify bottlenecks (largest allocations, leak patterns)
3. **Analyze** -- Map data structures and object lifecycles
4. **Select** -- Choose optimization strategies based on access patterns
5. **Transform** -- Apply memory-efficient alternatives with before/after code
6. **Verify** -- Confirm memory reduction without correctness loss

If no code is provided, request a file or snippet. `$target` is the file or module to optimize.

## Decision Tree

```
What is consuming memory?

Large collections:
├── List of objects → __slots__, namedtuple, or dataclass(slots=True)
├── List built all at once → Generator/iterator pattern
├── Storing strings → String interning (sys.intern), categorical encoding
└── Numeric data → NumPy arrays instead of lists

Data processing:
├── Loading full file → Chunked reading, memory-mapped files (mmap)
├── Intermediate copies → In-place operations, views (memoryview)
├── Keeping processed data → Process-and-discard pattern
└── DataFrame operations → Downcast dtypes, sparse arrays

Object lifecycle:
├── Objects never freed → Check circular refs, use weakref
├── Cache growing unbounded → @lru_cache(maxsize=N) or WeakValueDictionary
├── Global accumulation → Explicit cleanup, context managers
└── Large temporary objects → Delete explicitly, gc.collect()
```

## Leak Detection Checklist

Scan for these patterns in code or profiling output:

| Pattern | Cause | Fix |
|---------|-------|-----|
| Unbounded global lists/dicts | Append without removal | Bounded `deque`, periodic clear |
| `@lru_cache` without maxsize | Indefinite growth | `@lru_cache(maxsize=N)` |
| Thread locals per-request data | Leaks if threads recycled | Context-local storage, cleanup |
| Cyclic refs with `__del__` | GC cannot collect | `weakref`, break cycle |
| Traceback in `except:` | Keeps whole stack alive | Use `except Exception` + limit |
| Event callbacks not removed | Bound methods held | Weak references or explicit removal |
| `list(giant_generator)` | Materializes entire sequence | Iterate directly |

## Transformation Patterns

### 1. Class to `__slots__` -- 40-60% reduction per instance

```python
# Before
class Point:
    def __init__(self, x, y, z):
        self.x = x; self.y = y; self.z = z

# After
class Point:
    __slots__ = ("x", "y", "z")
    def __init__(self, x, y, z):
        self.x = x; self.y = y; self.z = z
```

### 2. List accumulation to Generator

```python
# Before
def get_all_records(files):
    records = []
    for f in files:
        records.extend(parse_file(f))
    return records

# After
def get_all_records(files):
    for f in files:
        yield from parse_file(f)
```

### 3. String deduplication

```python
# Before
records = [{"status": "active", "type": "user"} for _ in range(1000000)]

# After
import sys
STATUS_ACTIVE = sys.intern("active")
TYPE_USER = sys.intern("user")
records = [{"status": STATUS_ACTIVE, "type": TYPE_USER} for _ in range(1000000)]
```

### 4. Memory-mapped files for large-than-RAM data

```python
import mmap
import numpy as np

# Binary data
with open("large_file.bin", "rb") as f:
    mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    # Process chunks without loading entire file

# NumPy arrays
arr = np.memmap("large_array.dat", dtype="float32", mode="r", shape=(1000000, 100))
```

### 5. Chunked DataFrame processing

```python
def process_large_csv(filepath, chunksize=10000):
    results = []
    for chunk in pd.read_csv(filepath, chunksize=chunksize):
        result = process_chunk(chunk)
        results.append(result)
        del chunk  # Explicit cleanup
    return pd.concat(results)
```

### 6. Downcast numeric types -- 2-8x reduction

```python
# Before
df = pd.read_csv("data.csv")  # Default int64, float64

# After
df = pd.read_csv("data.csv", dtype={"id": "int32", "value": "float32"})
df["category"] = df["category"].astype("category")
```

## Data Structure Memory Comparison

| Structure | Memory/item | Use case |
|-----------|-------------|----------|
| `list` of `dict` | ~400+ bytes | Flexible, small datasets |
| `list` of `class` | ~300 bytes | Object-oriented, small |
| `list` of `__slots__` class | ~120 bytes | Many similar objects |
| `namedtuple` | ~80 bytes | Immutable records |
| `numpy.ndarray` | 8 bytes (float64) | Numeric, vectorized ops |
| `pandas.DataFrame` | ~10-50 bytes/cell | Tabular, analysis |

## Profiling Recipes

```python
# Object size (shallow)
import sys; sys.getsizeof(obj)

# Deep size (includes referenced objects)
from pympler import asizeof; asizeof.asizeof(obj)

# Line-by-line memory profiling
from memory_profiler import profile

@profile
def my_function():
    pass
# Run: mprof run script.py && mprof plot

# Allocation tracking
import tracemalloc

tracemalloc.start()
snap1 = tracemalloc.take_snapshot()
# ... code ...
snap2 = tracemalloc.take_snapshot()
for stat in snap2.compare_to(snap1, "lineno")[:10]:
    print(stat)

# Quick peak measurement
def measure_peak(func, *args):
    tracemalloc.start()
    result = func(*args)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    print(f"Peak: {peak/1024:.1f} KB")
    return result
```

Install profiling tools: `pip install memory-profiler objgraph pympler`

## Advanced Patterns (on request)

- `__slots__` + `array.array` for many instances with homogeneous data
- `weakref.finalize` for resource cleanup (avoids `__del__` issues)
- `array("b")` for boolean/byte flags instead of `list[int]`
- `collections.deque`-based BFS/DFS instead of recursive traversal
- `io.StringIO` instead of repeated string concatenation
- `itertools.chain` to compose generators without materializing

## Constraints

- `__slots__` breaks dynamic attribute addition -- detect such usage first.
- `weakref` callbacks fire at unpredictable times; never rely on order.
- Profiling adds overhead -- avoid in production unless actively diagnosing.
- Data > RAM -- offload to disk (SQLite, HDF5, Zarr) rather than pure-Python optimization.

## Verification Checklist

Before finalizing optimized code:
- [ ] Memory reduced (measured with profiler)
- [ ] Functionality preserved (same outputs)
- [ ] No new leaks introduced
- [ ] Performance acceptable (generators may add iteration overhead)
- [ ] Code remains readable and maintainable
