def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AiMlArchCrossbarPriorityEncoderMoe:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_req):
        req = to_unsigned(in_req, 8)
        found = 1 if req != 0 else 0
        if req & 0x01:
            index = 0
        elif req & 0x02:
            index = 1
        elif req & 0x04:
            index = 2
        elif req & 0x08:
            index = 3
        elif req & 0x10:
            index = 4
        elif req & 0x20:
            index = 5
        elif req & 0x40:
            index = 6
        elif req & 0x80:
            index = 7
        else:
            index = 0
        grant = (1 << index) if found else 0
        return {
            "out_found": to_unsigned(found, 1),
            "out_index": to_unsigned(index, 3),
            "out_grant": to_unsigned(grant, 8),
        }