"""Sample the old count, then apply this clock's enable."""
class Modulo12Counter:
    def __init__(self):
        self.reset()

    def reset(self):
        self.count = 0

    def process(self, valid_count):
        value = self.count
        if valid_count & 1:
            self.count = 0 if self.count == 11 else self.count + 1
        return {"out": value}
