class AiMlSamplePickSampleMultinomialCdf:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_sample, in_cdf0, in_cdf1, in_cdf2, in_cdf3, in_cdf4, in_cdf5, in_cdf6, in_cdf7):
        cdf = [in_cdf0 & 8589934591, in_cdf1 & 8589934591, in_cdf2 & 8589934591, in_cdf3 & 8589934591, in_cdf4 & 8589934591, in_cdf5 & 8589934591, in_cdf6 & 8589934591, in_cdf7 & 8589934591]
        sample = in_sample & 4294967295
        error = int(cdf[7] != 4294967296)
        previous = 0
        token = 0
        found = 0
        for i in range(8):
            if cdf[i] < previous or cdf[i] > 4294967296:
                error = 1
            previous = cdf[i]
            if found == 0 and sample < cdf[i]:
                token = i
                found = 1
        if error:
            token = 0
        return {'out_token_id': token, 'out_error': error}
