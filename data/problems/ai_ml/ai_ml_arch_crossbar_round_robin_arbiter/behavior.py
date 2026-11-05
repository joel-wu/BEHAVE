def to_unsigned(value, width):
    return value & ((1 << width) - 1)


class AiMlArchCrossbarRoundRobinArbiter:
    def __init__(self, N=4, DATA_W=32):
        self.N = N
        self.DATA_W = DATA_W
        self.reset()

    def reset(self):
        self.last_grant = self.N - 1

    def process(self, in_req, in_data):
        requests = to_unsigned(in_req, self.N)
        if requests == 0:
            return None

        chosen = 0
        for offset in range(1, self.N + 1):
            candidate = (self.last_grant + offset) % self.N
            if (requests >> candidate) & 1:
                chosen = candidate
                break

        self.last_grant = chosen
        payload = (in_data >> (chosen * self.DATA_W)) & (
            (1 << self.DATA_W) - 1
        )
        return {"out_data": payload, "out_index": chosen}
