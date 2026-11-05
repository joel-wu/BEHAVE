class PalindromeDetector:
    def __init__(self, N=3):
        self.N = N
        self.reset()

    def reset(self):
        self.window = 0
        self.count = 0

    def process(self, bit_stream):
        palindrome_detected = int(self.count == 3 and ((self.window >> 2) & 1) == (self.window & 1))
        self.window = ((self.window << 1) | bit_stream) & 7
        if self.count < 3:
            self.count += 1
        return {"palindrome_detected": palindrome_detected}
