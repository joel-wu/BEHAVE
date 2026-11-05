class SignedInputNegateI8:
    def reset(self):
        pass

    def process(self, in_mag):
        return {"out_y": -in_mag}
