class SyncPosNegEdgeDetector:
    def __init__(self):
        self.reset()

    def reset(self):
        self.previous = 0
        self.positive = 0
        self.negative = 0

    def process(self, i_detection_signal):
        positive = self.positive
        negative = self.negative
        self.positive = (1 - self.previous) & i_detection_signal
        self.negative = self.previous & (1 - i_detection_signal)
        self.previous = i_detection_signal
        return {"o_positive_edge_detected": positive, "o_negative_edge_detected": negative}
