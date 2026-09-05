import json

import pytest

from loveapp.evaluation.memory_extraction_alignment import _parse_alignment_result


def test_alignment_parser_accepts_one_to_one_semantic_pair() -> None:
    result = _parse_alignment_result(
        json.dumps(
            {
                "matches": [
                    {
                        "expected_index": 0,
                        "actual_index": 1,
                        "proposition_equivalent": True,
                        "semantic_match": True,
                        "evidence_support": "PASS",
                        "reason": "equivalent predicate wording",
                    }
                ],
                "unmatched_expected": [],
                "unmatched_actual": [0],
                "over_merge_actual_indices": [],
                "over_split_expected_indices": [],
                "uncertain": False,
                "reason": "",
            }
        ),
        expected_count=1,
        actual_count=2,
    )

    assert result.matches[0].semantic_match is True
    assert result.matches[0].actual_index == 1


def test_alignment_parser_repairs_marked_over_merge_to_one_to_one() -> None:
    payload = {
        "matches": [
            {
                "expected_index": expected,
                "actual_index": 0,
                "proposition_equivalent": True,
                "semantic_match": True,
                "evidence_support": "PASS",
                "reason": "merged",
            }
            for expected in (0, 1)
        ],
        "unmatched_expected": [],
        "unmatched_actual": [],
        "over_merge_actual_indices": [0],
        "over_split_expected_indices": [],
        "uncertain": False,
        "reason": "",
    }

    result = _parse_alignment_result(
        json.dumps(payload),
        expected_count=2,
        actual_count=1,
    )

    assert len(result.matches) == 1
    assert result.unmatched_expected == [1]
    assert result.over_merge_actual_indices == [0]


def test_alignment_parser_infers_over_merge_from_many_to_one_pairs() -> None:
    payload = {
        "matches": [
            {
                "expected_index": expected,
                "actual_index": 0,
                "proposition_equivalent": True,
                "semantic_match": True,
                "evidence_support": "PASS",
                "reason": "duplicate",
            }
            for expected in (0, 1)
        ],
        "unmatched_expected": [],
        "unmatched_actual": [],
        "over_merge_actual_indices": [],
        "over_split_expected_indices": [],
        "uncertain": False,
        "reason": "",
    }

    result = _parse_alignment_result(
        json.dumps(payload),
        expected_count=2,
        actual_count=1,
    )

    assert [(pair.expected_index, pair.actual_index) for pair in result.matches] == [
        (0, 0)
    ]
    assert result.unmatched_expected == [1]
    assert result.unmatched_actual == []
    assert result.over_merge_actual_indices == [0]


def test_alignment_parser_rejects_duplicate_same_pair() -> None:
    pair = {
        "expected_index": 0,
        "actual_index": 0,
        "proposition_equivalent": True,
        "semantic_match": True,
        "evidence_support": "PASS",
        "reason": "duplicate",
    }
    payload = {
        "matches": [pair, pair],
        "unmatched_expected": [],
        "unmatched_actual": [],
        "over_merge_actual_indices": [],
        "over_split_expected_indices": [],
        "uncertain": False,
        "reason": "",
    }

    with pytest.raises(ValueError, match="one-to-one"):
        _parse_alignment_result(json.dumps(payload), expected_count=1, actual_count=1)


def test_non_equivalent_pair_does_not_reserve_duplicate_expected_index() -> None:
    payload = {
        "matches": [
            {
                "expected_index": 0,
                "actual_index": 0,
                "proposition_equivalent": False,
                "semantic_match": False,
                "evidence_support": "FAIL",
                "reason": "different proposition",
            },
            {
                "expected_index": 0,
                "actual_index": 1,
                "proposition_equivalent": True,
                "semantic_match": True,
                "evidence_support": "PASS",
                "reason": "equivalent proposition",
            },
        ],
        "unmatched_expected": [],
        "unmatched_actual": [],
        "over_merge_actual_indices": [],
        "over_split_expected_indices": [],
        "uncertain": False,
        "reason": "the first proposal is not a match",
    }

    result = _parse_alignment_result(
        json.dumps(payload),
        expected_count=1,
        actual_count=2,
    )

    assert [(pair.expected_index, pair.actual_index) for pair in result.matches] == [
        (0, 1)
    ]
    assert result.unmatched_expected == []
    assert result.unmatched_actual == [0]


def test_alignment_parser_repairs_marked_over_split_to_one_to_one() -> None:
    payload = {
        "matches": [
            {
                "expected_index": 0,
                "actual_index": actual,
                "proposition_equivalent": True,
                "semantic_match": True,
                "evidence_support": "PASS",
                "reason": "split detail",
            }
            for actual in (0, 1)
        ],
        "unmatched_expected": [],
        "unmatched_actual": [],
        "over_merge_actual_indices": [],
        "over_split_expected_indices": [0],
        "uncertain": False,
        "reason": "one preference was split",
    }

    result = _parse_alignment_result(
        json.dumps(payload),
        expected_count=1,
        actual_count=2,
    )

    assert len(result.matches) == 1
    assert result.unmatched_actual == [1]
    assert result.over_split_expected_indices == [0]


def test_alignment_parser_rejects_unmarked_over_split_pairs() -> None:
    payload = {
        "matches": [
            {
                "expected_index": 0,
                "actual_index": actual,
                "proposition_equivalent": True,
                "semantic_match": True,
                "evidence_support": "PASS",
                "reason": "unmarked split",
            }
            for actual in (0, 1)
        ],
        "unmatched_expected": [],
        "unmatched_actual": [],
        "over_merge_actual_indices": [],
        "over_split_expected_indices": [],
        "uncertain": False,
        "reason": "",
    }

    with pytest.raises(ValueError, match="one-to-one"):
        _parse_alignment_result(json.dumps(payload), expected_count=1, actual_count=2)


def test_alignment_parser_ignores_non_equivalent_duplicate_expected_pair() -> None:
    payload = {
        "matches": [
            {
                "expected_index": 0,
                "actual_index": 0,
                "proposition_equivalent": True,
                "semantic_match": True,
                "evidence_support": "PASS",
                "reason": "the expected preference",
            },
            {
                "expected_index": 0,
                "actual_index": 1,
                "proposition_equivalent": False,
                "semantic_match": False,
                "evidence_support": "PASS",
                "reason": "a narrower unsupported preference",
            },
        ],
        "unmatched_expected": [],
        "unmatched_actual": [],
        "over_merge_actual_indices": [],
        "over_split_expected_indices": [],
        "uncertain": False,
        "reason": "one match and one spurious actual claim",
    }

    result = _parse_alignment_result(
        json.dumps(payload),
        expected_count=1,
        actual_count=2,
    )

    assert [(pair.expected_index, pair.actual_index) for pair in result.matches] == [
        (0, 0)
    ]
    assert result.unmatched_expected == []
    assert result.unmatched_actual == [1]


def test_alignment_parser_reconstructs_unmatched_indices() -> None:
    payload = {
        "matches": [
            {
                "expected_index": 0,
                "actual_index": 0,
                "proposition_equivalent": False,
                "semantic_match": False,
                "evidence_support": "FAIL",
                "reason": "different proposition",
            }
        ],
        "unmatched_expected": [],
        "unmatched_actual": [],
        "over_merge_actual_indices": [],
        "over_split_expected_indices": [],
        "uncertain": False,
        "reason": "no equivalent pair",
    }

    result = _parse_alignment_result(
        json.dumps(payload),
        expected_count=1,
        actual_count=1,
    )

    assert result.matches == []
    assert result.unmatched_expected == [0]
    assert result.unmatched_actual == [0]


def test_alignment_parser_rejects_negative_index() -> None:
    payload = {
        "matches": [],
        "unmatched_expected": [-1],
        "unmatched_actual": [],
        "over_merge_actual_indices": [],
        "over_split_expected_indices": [],
        "uncertain": False,
        "reason": "invalid index",
    }

    with pytest.raises(ValueError, match="out of range"):
        _parse_alignment_result(
            json.dumps(payload),
            expected_count=1,
            actual_count=0,
        )


def test_alignment_parser_rejects_out_of_range_index() -> None:
    payload = {
        "matches": [
            {
                "expected_index": 3,
                "actual_index": 0,
                "proposition_equivalent": True,
                "semantic_match": True,
                "evidence_support": "PASS",
                "reason": "bad index",
            }
        ],
        "unmatched_expected": [],
        "unmatched_actual": [],
        "over_merge_actual_indices": [],
        "over_split_expected_indices": [],
        "uncertain": False,
        "reason": "",
    }

    with pytest.raises(ValueError, match="out-of-range"):
        _parse_alignment_result(
            json.dumps(payload),
            expected_count=1,
            actual_count=1,
        )
