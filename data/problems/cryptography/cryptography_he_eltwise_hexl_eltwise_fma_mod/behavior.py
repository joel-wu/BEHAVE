class CryptographyHeEltwiseHexlEltwiseFmaMod:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_arg1, in_arg2, in_arg3, in_arg3_enable, in_modulus, in_input_mod_factor):
        if in_modulus < 2:
            return {"out_result": 0}
        addend = in_arg3 if in_arg3_enable else 0
        return {"out_result": (in_arg1 * in_arg2 + addend) % in_modulus}