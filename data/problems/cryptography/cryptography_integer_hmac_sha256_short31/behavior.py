# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def ror32(x, n):
    return ((x >> n) | (x << (32-n))) & 4294967295

def sha256_two_blocks(data):
    constants=[1116352408, 1899447441, 3049323471, 3921009573, 961987163, 1508970993, 2453635748, 2870763221, 3624381080, 310598401, 607225278, 1426881987, 1925078388, 2162078206, 2614888103, 3248222580, 3835390401, 4022224774, 264347078, 604807628, 770255983, 1249150122, 1555081692, 1996064986, 2554220882, 2821834349, 2952996808, 3210313671, 3336571891, 3584528711, 113926993, 338241895, 666307205, 773529912, 1294757372, 1396182291, 1695183700, 1986661051, 2177026350, 2456956037, 2730485921, 2820302411, 3259730800, 3345764771, 3516065817, 3600352804, 4094571909, 275423344, 430227734, 506948616, 659060556, 883997877, 958139571, 1322822218, 1537002063, 1747873779, 1955562222, 2024104815, 2227730452, 2361852424, 2428436474, 2756734187, 3204031479, 3329325298]
    state=[1779033703, 3144134277, 1013904242, 2773480762, 1359893119, 2600822924, 528734635, 1541459225]
    words=[0]*64
    for block in range(2):
        for i in range(16):
            words[i]=0
            for j in range(4):
                words[i] |= ((data >> (8*(64*block+4*i+j)))&255) << (8*(3-j))
        for i in range(16,64):
            s0=ror32(words[i-15],7)^ror32(words[i-15],18)^(words[i-15]>>3)
            s1=ror32(words[i-2],17)^ror32(words[i-2],19)^(words[i-2]>>10)
            words[i]=(words[i-16]+s0+words[i-7]+s1)&4294967295
        a=state[0]
        b=state[1]
        c=state[2]
        d=state[3]
        e=state[4]
        f=state[5]
        g=state[6]
        h=state[7]
        for i in range(64):
            t1=(h+(ror32(e,6)^ror32(e,11)^ror32(e,25))+((e&f)^((~e)&g))+constants[i]+words[i])&4294967295
            t2=((ror32(a,2)^ror32(a,13)^ror32(a,22))+((a&b)^(a&c)^(b&c)))&4294967295
            h=g
            g=f
            f=e
            e=(d+t1)&4294967295
            d=c
            c=b
            b=a
            a=(t1+t2)&4294967295
        state[0]=(state[0]+a)&4294967295
        state[1]=(state[1]+b)&4294967295
        state[2]=(state[2]+c)&4294967295
        state[3]=(state[3]+d)&4294967295
        state[4]=(state[4]+e)&4294967295
        state[5]=(state[5]+f)&4294967295
        state[6]=(state[6]+g)&4294967295
        state[7]=(state[7]+h)&4294967295
    result=0
    for i in range(8):
        for j in range(4):
            result |= ((state[i]>>(8*(3-j)))&255) << (8*(4*i+j))
    return result

class HMACSHA256Short31:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_key, in_message, in_length):
        inner=0
        outer=0
        for i in range(64):
            key_byte=(in_key >> (8*i))&255 if i<32 else 0
            inner |= (key_byte^54) << (8*i)
            outer |= (key_byte^92) << (8*i)
        inner |= (in_message&((1<<(8*in_length))-1)) << 512
        inner |= 128 << (8*(64+in_length))
        bit_length=(64+in_length)*8
        for i in range(8):
            inner |= ((bit_length>>(8*(7-i)))&255) << (8*(120+i))
        digest=sha256_two_blocks(inner)
        outer |= digest << 512
        outer |= 128 << 768
        outer |= 3 << 1008
        result=sha256_two_blocks(outer)
        return {"out_values": result}
