class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, d, done_counting, ack, state):
        return {
            "B3_next": (state >> 6) & 1,
            "S_next": (int((state & 11) != 0) & (d ^ 1)) | (((state >> 9) & 1) & ack),
            "S1_next": (state & 1) & d,
            "Count_next": ((state >> 7) & 1) | (((state >> 8) & 1) & (done_counting ^ 1)),
            "Wait_next": (((state >> 8) & 1) & done_counting) | (((state >> 9) & 1) & (ack ^ 1)),
            "done": (state >> 9) & 1,
            "counting": (state >> 8) & 1,
            "shift_ena": int((state & 240) != 0),
        }
