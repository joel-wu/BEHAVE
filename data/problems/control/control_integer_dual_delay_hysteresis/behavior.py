# SPDX-License-Identifier: BSD-3-Clause
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class DualDelayHysteresis:
    def __init__(self):
        self.reset()

    def reset(self):
        self.level = 0
        self.requested = 0
        self.age = 0

    def process(self, in_request, in_rise_delay, in_fall_delay):
        level = self.level
        requested = self.requested
        age = self.age
        if in_request == level:
            requested = level
            age = 0
        else:
            if in_request != requested:
                requested = in_request
                age = 0
            elif age < 15:
                age += 1
            delay = in_fall_delay if level else in_rise_delay
            if age >= delay:
                level = requested
                age = 0
        self.level = level
        self.requested = requested
        self.age = age
        result = level
        return {"out_values": result}
