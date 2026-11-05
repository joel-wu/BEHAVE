class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, A, B):
        result = 0
        for i in range(8):
            if (B >> i) & 1:
                result += A << i
        return {"product": result}
