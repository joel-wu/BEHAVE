class BufferBankConflictArbiter:
    """One independent arbitration decision per addressed scratchpad bank."""
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_single_ported, in_ex_read_valid, in_ex_read_bank, in_dma_read_valid, in_dma_read_bank, in_ex_write_valid, in_ex_write_bank, in_dma_write_valid, in_dma_write_bank):
        in_single_ported = in_single_ported & 1
        in_ex_read_valid = in_ex_read_valid & 1
        in_ex_read_bank = in_ex_read_bank & 255
        in_dma_read_valid = in_dma_read_valid & 1
        in_dma_read_bank = in_dma_read_bank & 255
        in_ex_write_valid = in_ex_write_valid & 1
        in_ex_write_bank = in_ex_write_bank & 255
        in_dma_write_valid = in_dma_write_valid & 1
        in_dma_write_bank = in_dma_write_bank & 255
        ex_write = in_ex_write_valid != 0
        dma_write = in_dma_write_valid != 0 and not (
            ex_write and in_ex_write_bank == in_dma_write_bank)
        ex_read = in_ex_read_valid != 0
        dma_read = in_dma_read_valid != 0 and not (
            ex_read and in_ex_read_bank == in_dma_read_bank)
        ex_blocked = in_single_ported != 0 and ex_read and (
            (ex_write and in_ex_write_bank == in_ex_read_bank) or
            (dma_write and in_dma_write_bank == in_ex_read_bank))
        dma_blocked = in_single_ported != 0 and dma_read and (
            (ex_write and in_ex_write_bank == in_dma_read_bank) or
            (dma_write and in_dma_write_bank == in_dma_read_bank))
        return {
            "out_ex_read_grant": 1 if ex_read and not ex_blocked else 0,
            "out_dma_read_grant": 1 if dma_read and not dma_blocked else 0,
            "out_ex_write_grant": 1 if ex_write else 0,
            "out_dma_write_grant": 1 if dma_write else 0,
            "out_ex_read_blocked": 1 if ex_blocked else 0,
            "out_dma_read_blocked": 1 if dma_blocked else 0,
        }
