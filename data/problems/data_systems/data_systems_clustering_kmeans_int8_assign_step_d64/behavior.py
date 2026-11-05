def signed8(v):
    v = v & 255
    return v-256 if v >= 128 else v


class KMeansInt8AssignStepD64:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_vector, in_centroids):
        best = 4194303
        winner = 0
        for c in range(8):
            distance = 0
            for j in range(64):
                x = signed8(in_vector >> (8*j))
                center = signed8(in_centroids >> (8*(64*c+j)))
                difference = x-center
                distance += difference*difference
            if distance < best:
                best = distance
                winner = c
        return {'out_assign': winner, 'out_distance': best}
