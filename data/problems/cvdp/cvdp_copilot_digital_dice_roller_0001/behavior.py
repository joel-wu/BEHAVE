class DigitalDiceRoller:
    def __init__(self):
        self.reset()

    def reset(self):
        self.rolling = 0
        self.counter = 0
        self.result = 0

    def process(self, button):
        output = {"dice_value":self.result}
        if button:
            self.counter = 1 if not self.rolling or self.counter==6 else self.counter+1
            self.rolling = 1
        elif self.rolling:
            self.result = self.counter
            self.counter = 0
            self.rolling = 0
        return output
