"""Exact bit-vectors wider than Python's default int/str limit survive records.

A count-min sketch with 4x256 16-bit cells packs into one 16,384-bit image.
Its decimal JSON form has about 5,000 digits, above CPython's default limit of
4,300, and the RTL record audit failed to answer for that task (2026-10-18).
"""

import json

from behave.sim.core.record_value import canonical_json
from behave.sim.strict_json import strict_json_loads


def test_a_16384_bit_image_round_trips_through_canonical_and_strict_json():
    image = (1 << 16384) - 12345
    encoded = canonical_json({"image": image})
    assert strict_json_loads(encoded)["image"] == image
    assert json.loads(json.dumps(image)) == image
