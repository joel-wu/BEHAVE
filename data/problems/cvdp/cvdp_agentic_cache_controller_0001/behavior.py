class CacheController:
    def __init__(self):
        self.reset()

    def reset(self):
        self.words = [0] * 32
        self.tags = [0] * 32
        self.valid = [0] * 32
        self.last_data = 0
        self.last_hit = 0
        self.last_miss = 0

    def process(self, address, write_data, read, write, mem_read_data, mem_ready):
        index = address & 31
        tag = address >> 5
        line_hit = int(self.valid[index] != 0 and self.tags[index] == tag)
        read_data = self.last_data
        hit = self.last_hit
        miss = self.last_miss
        mem_write = 0
        mem_address = 0
        mem_write_data = 0
        if read or write:
            hit = line_hit
            miss = 1 - line_hit
            read_data = 0
            if read:
                if line_hit:
                    read_data = self.words[index]
                else:
                    mem_address = address
                    if mem_ready:
                        read_data = mem_read_data
            else:
                mem_write = 1
                mem_address = address
                mem_write_data = write_data
        out = {"read_data": read_data, "hit": hit, "miss": miss, "mem_write": mem_write, "mem_address": mem_address, "mem_write_data": mem_write_data}
        if read and (line_hit or mem_ready):
            if not line_hit:
                self.words[index] = mem_read_data
                self.tags[index] = tag
                self.valid[index] = 1
            self.last_data = read_data
            self.last_hit = hit
            self.last_miss = miss
        elif write and not read and mem_ready:
            if line_hit:
                self.words[index] = write_data
            self.last_data = 0
            self.last_hit = hit
            self.last_miss = miss
        return out
