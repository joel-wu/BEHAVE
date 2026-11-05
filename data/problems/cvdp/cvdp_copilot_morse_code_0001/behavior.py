MORSE_BITS = (1, 8, 10, 4, 0, 2, 6, 0, 0, 7, 5, 4, 3, 2, 7, 6, 13, 2, 0, 1, 1, 1, 3, 9, 11, 12, 31, 15, 7, 3, 1, 0, 16, 24, 28, 30)
MORSE_LENGTHS = (2, 4, 4, 3, 1, 4, 3, 4, 2, 4, 3, 4, 2, 2, 3, 4, 4, 3, 3, 1, 3, 4, 3, 4, 4, 4, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5)


class MorseEncoder:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, ascii_in):
        index = 0
        if ascii_in >= 65 and ascii_in <= 90:
            index = ascii_in - 65
        elif ascii_in >= 48 and ascii_in <= 57:
            index = ascii_in - 48 + 26
        else:
            return {"morse_out": 0, "morse_length": 0}
        return {"morse_out": MORSE_BITS[index], "morse_length": MORSE_LENGTHS[index]}
