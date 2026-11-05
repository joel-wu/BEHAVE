class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.history = 0
        self.pht = [1] * 128

    def process(self, predict_valid, predict_pc, train_valid, train_taken, train_mispredicted, train_history, train_pc):
        history = self.history
        predict_taken = (self.pht[history ^ predict_pc] >> 1) if predict_valid else 0
        predict_history = history if predict_valid else 0
        if predict_valid:
            self.history = ((history << 1) | predict_taken) & 127
        if train_valid:
            index = train_history ^ train_pc
            counter = self.pht[index]
            if train_taken:
                self.pht[index] = counter + 1 if counter < 3 else 3
            else:
                self.pht[index] = counter - 1 if counter > 0 else 0
            if train_mispredicted:
                self.history = ((train_history << 1) | train_taken) & 127
        return {"predict_taken": predict_taken, "predict_history": predict_history}
