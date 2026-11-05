# CMSIS-NN v7.0.0 portable S8/S16 LSTM, fixed finite dimensions.
# Derived arithmetic is Apache-2.0; see SOURCE_LICENSE and the pinned source.
SIGMOID = [32768, 33451, 34133, 34813, 35493, 36169, 36843, 37513, 38180, 38841, 39498, 40149, 40794, 41432, 42064, 42688, 43304, 43912, 44511, 45102, 45683, 46255, 46817, 47369, 47911, 48443, 48964, 49475, 49975, 50464, 50942, 51409, 51865, 52311, 52745, 53169, 53581, 53983, 54374, 54755, 55125, 55485, 55834, 56174, 56503, 56823, 57133, 57433, 57724, 58007, 58280, 58544, 58800, 59048, 59288, 59519, 59743, 59959, 60168, 60370, 60565, 60753, 60935, 61110, 61279, 61441, 61599, 61750, 61896, 62036, 62172, 62302, 62428, 62549, 62666, 62778, 62886, 62990, 63090, 63186, 63279, 63368, 63454, 63536, 63615, 63691, 63765, 63835, 63903, 63968, 64030, 64090, 64148, 64204, 64257, 64308, 64357, 64405, 64450, 64494, 64536, 64576, 64614, 64652, 64687, 64721, 64754, 64786, 64816, 64845, 64873, 64900, 64926, 64950, 64974, 64997, 65019, 65039, 65060, 65079, 65097, 65115, 65132, 65149, 65164, 65179, 65194, 65208, 65221, 65234, 65246, 65258, 65269, 65280, 65291, 65301, 65310, 65319, 65328, 65337, 65345, 65352, 65360, 65367, 65374, 65381, 65387, 65393, 65399, 65404, 65410, 65415, 65420, 65425, 65429, 65433, 65438, 65442, 65445, 65449, 65453, 65456, 65459, 65462, 65465, 65468, 65471, 65474, 65476, 65479, 65481, 65483, 65485, 65488, 65489, 65491, 65493, 65495, 65497, 65498, 65500, 65501, 65503, 65504, 65505, 65507, 65508, 65509, 65510, 65511, 65512, 65513, 65514, 65515, 65516, 65517, 65517, 65518, 65519, 65520, 65520, 65521, 65522, 65522, 65523, 65523, 65524, 65524, 65525, 65525, 65526, 65526, 65526, 65527, 65527, 65528, 65528, 65528, 65529, 65529, 65529, 65529, 65530, 65530, 65530, 65530, 65531, 65531, 65531, 65531, 65531, 65532, 65532, 65532, 65532, 65532, 65532, 65533, 65533, 65533, 65533, 65533, 65533, 65533, 65533, 65534, 65534, 65534, 65534, 65534, 65534, 65534, 65534, 65534, 65534, 65535]

def signed8(x):
    return ((x+128) & 255)-128

def signed16(x):
    return ((x+32768) & 65535)-32768

def sat16(x):
    return max(-32768, min(32767, x))

def requant(x, shift):
    # multiplier=2^30, negative shift, default CMSIS double rounding.
    v = (x+1) >> 1
    if v < 0:
        return -(((-v)+(1 << (shift-1))) >> shift)
    return (v+(1 << (shift-1))) >> shift

def activate(x, tanh):
    scaled = x*3
    magnitude = abs(scaled)
    bits = 9
    if tanh != 0:
        bits = 8
    index = magnitude >> bits
    if index >= 255:
        value = 32767 << 10
        if tanh != 0:
            value = 65535 << 8
    else:
        value = (SIGMOID[index] << bits)+(magnitude & ((1 << bits)-1))*(SIGMOID[index+1]-SIGMOID[index])
    if tanh == 0:
        if scaled >= 0:
            value += 512
        else:
            value = (1 << 25)-value+511
        value >>= 10
    else:
        if scaled >= 0:
            value = value-(1 << 23)+128
        else:
            value = (1 << 23)-value+127
        value >>= 8
    return signed16(value)

class CMSISS8LSTM:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_time_major, in_input_data, in_input_weights, in_hidden_weights, in_bias):
        h = [0]*4
        cell = [0]*4
        output = 0
        for t in range(3):
            for b in range(2):
                base = (b*3+t)*2
                if (in_time_major & 1) != 0:
                    base = (t*2+b)*2
                gates = [0]*8
                for g in range(4):
                    for j in range(2):
                        acc = signed16(in_bias >> (16*(g*2+j)))
                        recurrent = 0
                        for k in range(2):
                            acc += signed8(in_input_data >> (8*(base+k)))*signed8(in_input_weights >> (8*(g*4+j*2+k)))
                            recurrent += h[b*2+k]*signed8(in_hidden_weights >> (8*(g*4+j*2+k)))
                        pre = sat16(sat16(acc)+recurrent)
                        kind = 0
                        if g == 2:
                            kind = 1
                        gates[g*2+j] = activate(pre, kind)
                for j in range(2):
                    previous = sat16(requant(gates[j]*cell[b*2+j],14))
                    cell[b*2+j] = max(-32767,min(32767,previous+requant(gates[2+j]*gates[4+j],17)))
                    h[b*2+j] = max(-128,min(127,requant(gates[6+j]*activate(cell[b*2+j],1),22)))
                    output |= (h[b*2+j] & 255) << (8*(base+j))
        final_cell = 0
        for j in range(4):
            final_cell |= (cell[j] & 65535) << (16*j)
        return {"out_status": 0, "out_output_data": output, "out_cell_state": final_cell}
