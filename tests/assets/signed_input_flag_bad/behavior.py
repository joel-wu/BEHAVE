class SignedInputFlagBad:
    def reset(self):
        pass

    def process(self, in_x):
        # Relies on replay_tb normalizing signed RTL inputs to Python ints.
        return {"out_neg": 1 if in_x < 0 else 0}
