class FsmSeqDetector:
    def __init__(self):
        self.reset()

    def reset(self):
        self.matched = 0
        self.detected = 0

    def process(self, seq_in):
        old = self.detected
        state = self.matched
        self.detected = 1 if state == 7 and seq_in else 0
        if state == 0:
            self.matched = 1 if seq_in else 0
        elif state == 1:
            self.matched = 1 if seq_in else 2
        elif state == 2:
            self.matched = 3 if seq_in else 0
        elif state == 3:
            self.matched = 4 if seq_in else 2
        elif state == 4:
            self.matched = 1 if seq_in else 5
        elif state == 5:
            self.matched = 3 if seq_in else 6
        elif state == 6:
            self.matched = 1 if seq_in else 7
        else:
            self.matched = 1 if seq_in else 0
        return {"seq_detected": old}
