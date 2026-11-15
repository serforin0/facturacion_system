import pytest

from string_expression import StringExpression


@pytest.mark.parametrize(
    "inp, expected",
    [
        ("foursixminustwotwoplusonezero", "threefour"),
        ("onezeropluseight", "oneeight"),
        ("oneminusoneone", "negativeonezero"),
        ("five", "five"),
        ("nineplustwo", "oneone"),
        ("zerominusone", "negativeone"),
        ("onezerominustwoplusthree", "oneone"),
    ],
)
def test_string_expression(inp, expected):
    assert StringExpression(inp) == expected
