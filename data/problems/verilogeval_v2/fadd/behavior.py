class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b, cin):
        return {"cout": (a & b) | (a & cin) | (b & cin), "sum": a ^ b ^ cin}
