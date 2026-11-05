# SPDX-License-Identifier: Apache-2.0
# Fixed-point adaptation of FilterPy GHFilter.update; see source.json.


class AlphaBetaTrackerQ16:
    def __init__(self):
        self.reset()

    def reset(self):
        self.position = 0
        self.velocity = 0

    def process(self, in_measurement, in_alpha, in_beta):
        measurement = in_measurement
        if measurement >= 2147483648:
            measurement -= 4294967296
        predicted = self.position + self.velocity
        residual = measurement - predicted
        position = predicted + ((in_alpha * residual) >> 15)
        velocity = self.velocity + ((in_beta * residual) >> 15)
        self.position = max(-2147483648, min(2147483647, position))
        self.velocity = max(-2147483648, min(2147483647, velocity))
        return {
            "out_position": self.position & 4294967295,
            "out_velocity": self.velocity & 4294967295,
        }
