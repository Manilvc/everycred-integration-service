from app.core.data_paths import (
    MAX_DESCRIBED_DEPTH,
    MAX_DESCRIBED_PATHS,
    MISSING,
    describe_paths,
    read_path,
)


def test_describe_paths_lists_keys_and_types_only() -> None:
    data = {
        "full_name": "Asha Verma",
        "age": 34,
        "verified": True,
        "photo": None,
        "address": {"zip": "411001", "geo": {"lat": 18.5}},
        "documents": [{"id": 1}, {"id": 2}],
        "extra": {},
    }

    assert describe_paths(data) == {
        "full_name": "string",
        "age": "number",
        "verified": "boolean",
        "photo": "null",
        "address.zip": "string",
        "address.geo.lat": "number",
        "documents": "array",
        "extra": "object",
    }


def test_described_paths_can_be_read_back() -> None:
    data = {"address": {"zip": "411001"}, "name": "x"}

    for path in describe_paths(data):
        assert read_path(data, path) is not MISSING


def test_keys_unusable_in_a_dot_path_are_skipped() -> None:
    data = {"a.b": 1, "has space": 2, "ok_key": 3, "dash-key": 4}

    assert describe_paths(data) == {"ok_key": "number", "dash-key": "number"}


def test_non_object_data_has_no_paths() -> None:
    assert describe_paths(["a", "b"]) == {}
    assert describe_paths("text") == {}
    assert describe_paths(None) == {}


def test_output_is_bounded() -> None:
    wide = {f"key_{index}": index for index in range(MAX_DESCRIBED_PATHS + 50)}
    deep: dict = {"leaf": 1}
    for _ in range(MAX_DESCRIBED_DEPTH + 5):
        deep = {"level": deep}

    assert len(describe_paths(wide)) == MAX_DESCRIBED_PATHS
    (deep_path,) = describe_paths(deep)
    assert deep_path.count(".") == MAX_DESCRIBED_DEPTH - 1
