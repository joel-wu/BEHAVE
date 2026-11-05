# SPDX-License-Identifier: BSD-3-Clause
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class ViterbiK3Block8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_codeword):
        metrics = [0, 17, 17, 17]
        paths = [0] * 4
        next_metrics = [17] * 4
        next_paths = [256] * 4
        for t in range(8):
            for s in range(4):
                next_metrics[s] = 17
                next_paths[s] = 256
            received = (in_codeword >> (2*t)) & 3
            for s in range(4):
                if metrics[s] < 17:
                    for bit in range(2):
                        register = (s << 1) | bit
                        first = (register ^ (register >> 1) ^ (register >> 2)) & 1
                        second = (register ^ (register >> 2)) & 1
                        difference = received ^ (first | (second << 1))
                        metric = metrics[s] + (difference & 1) + ((difference >> 1) & 1)
                        path = (paths[s] << 1) | bit
                        target = register & 3
                        if metric < next_metrics[target] or (metric == next_metrics[target] and path < next_paths[target]):
                            next_metrics[target] = metric
                            next_paths[target] = path
            for s in range(4):
                metrics[s] = next_metrics[s]
                paths[s] = next_paths[s]
        best_metric = 17
        best_path = 256
        for s in range(4):
            if metrics[s] < best_metric or (metrics[s] == best_metric and paths[s] < best_path):
                best_metric = metrics[s]
                best_path = paths[s]
        result = best_path | (best_metric << 8)
        return {"out_values": result}
