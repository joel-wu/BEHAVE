class RoaringArrayUnion:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_card_1, in_card_2, in_array_1, in_array_2):
        n = in_card_1 & 63
        m = in_card_2 & 63
        bad = 0
        for index in range(1, 63):
            if index < n:
                previous = (in_array_1 >> (16 * (index - 1))) & 65535
                current = (in_array_1 >> (16 * index)) & 65535
                if current <= previous:
                    bad = 1
            if index < m:
                previous = (in_array_2 >> (16 * (index - 1))) & 65535
                current = (in_array_2 >> (16 * index)) & 65535
                if current <= previous:
                    bad = 1
        result = 0
        count = 0
        if bad == 0:
            i = 0
            j = 0
            for unused in range(126):
                if i < n or j < m:
                    a = (in_array_1 >> (16 * i)) & 65535
                    b = (in_array_2 >> (16 * j)) & 65535
                    if i < n and (j == m or a < b):
                        value = a
                        i += 1
                    elif j < m and (i == n or b < a):
                        value = b
                        j += 1
                    else:
                        value = a
                        i += 1
                        j += 1
                    result = result | (value << (16 * count))
                    count += 1
        return {"out_card": count, "out_array": result, "out_error": bad}
