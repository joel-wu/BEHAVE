class Gp:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, i_A, i_B, i_Cin):
        generate = i_A & i_B
        propagate = i_A | i_B
        return {"o_generate": generate, "o_propagate": propagate, "o_Cout": generate | (propagate & i_Cin)}
