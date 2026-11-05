class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, code):
        out = 0
        valid = 0
        if code == 69:
            out = 0
            valid = 1
        elif code == 22:
            out = 1
            valid = 1
        elif code == 30:
            out = 2
            valid = 1
        elif code == 38:
            out = 3
            valid = 1
        elif code == 37:
            out = 4
            valid = 1
        elif code == 46:
            out = 5
            valid = 1
        elif code == 54:
            out = 6
            valid = 1
        elif code == 61:
            out = 7
            valid = 1
        elif code == 62:
            out = 8
            valid = 1
        elif code == 70:
            out = 9
            valid = 1
        return {"out": out, "valid": valid}
