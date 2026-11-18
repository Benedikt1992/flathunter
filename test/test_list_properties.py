"""Property-based tests for flathunter.utils.list (template for Hypothesis tests).

Property tests state invariants that must hold for *every* input and let Hypothesis
search for counterexamples. Prefer them for pure functions: parsers, filters,
formatting and data transformations.
"""

import math

from hypothesis import given
from hypothesis import strategies as st

from flathunter.utils.list import chunk_list

items = st.lists(st.integers(), max_size=200)
sizes = st.integers(min_value=1, max_value=50)


@given(items, sizes)
def test_chunks_reassemble_to_the_original_list(values: list[int], size: int) -> None:
    assert [value for chunk in chunk_list(values, size) for value in chunk] == values


@given(items, sizes)
def test_all_chunks_but_the_last_are_full(values: list[int], size: int) -> None:
    chunks = list(chunk_list(values, size))
    assert all(len(chunk) == size for chunk in chunks[:-1])
    assert all(1 <= len(chunk) <= size for chunk in chunks)


@given(items, sizes)
def test_number_of_chunks(values: list[int], size: int) -> None:
    assert len(list(chunk_list(values, size))) == math.ceil(len(values) / size)
