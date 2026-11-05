class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b, c, d):
        ab = a if a < b else b
        cd = c if c < d else d
        return {"min": ab if ab < cd else cd}
