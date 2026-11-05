class CaesarCipher:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, input_char, key):
        result = input_char
        if input_char >= 65 and input_char <= 90:
            offset = input_char - 65 + key
            result = 65 + (offset - 26 if offset >= 26 else offset)
        elif input_char >= 97 and input_char <= 122:
            offset = input_char - 97 + key
            result = 97 + (offset - 26 if offset >= 26 else offset)
        return {"output_char": result}
