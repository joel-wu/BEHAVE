class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.history = 0

    def process(self, predict_valid, predict_taken, train_mispredicted, train_taken, train_history):
        predict_history = self.history
        if train_mispredicted:
            self.history = ((train_history << 1) | train_taken) & 4294967295
        elif predict_valid:
            self.history = ((self.history << 1) | predict_taken) & 4294967295
        return {"predict_history": predict_history}
