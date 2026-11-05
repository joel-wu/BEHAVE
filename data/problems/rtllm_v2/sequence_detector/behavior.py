"""Moore 1001 detector, with the registered observation before each edge."""
class SequenceDetector:
    def __init__(self):
        self.reset()

    def reset(self):
        self.history = 0

    def process(self, data_in):
        detected = 1 if self.history == 9 else 0
        self.history = ((self.history << 1) | (data_in & 1)) & 15
        return {"sequence_detected": detected}
