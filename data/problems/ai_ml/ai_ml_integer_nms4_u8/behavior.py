# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

class NMS4U8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_boxes, in_scores, in_threshold):
        result = 0
        count = 0
        visited = 0
        blocked = 0
        for rank in range(4):
            best = 0
            best_score = -129
            for i in range(4):
                score = s8(in_scores >> (8*i))
                if ((visited >> i) & 1) == 0 and score > best_score:
                    best = i
                    best_score = score
            visited |= 1 << best
            if ((blocked >> best) & 1) == 0:
                result |= best << (2*count)
                count += 1
                first = (in_boxes >> (32*best)) & 4294967295
                ya = min(first & 255,(first >> 16) & 255)
                yb = max(first & 255,(first >> 16) & 255)
                xa = min((first >> 8) & 255,(first >> 24) & 255)
                xb = max((first >> 8) & 255,(first >> 24) & 255)
                area = (yb-ya)*(xb-xa)
                for j in range(4):
                    other = (in_boxes >> (32*j)) & 4294967295
                    yc = min(other & 255,(other >> 16) & 255)
                    yd = max(other & 255,(other >> 16) & 255)
                    xc = min((other >> 8) & 255,(other >> 24) & 255)
                    xd = max((other >> 8) & 255,(other >> 24) & 255)
                    intersection = max(0,min(yb,yd)-max(ya,yc))*max(0,min(xb,xd)-max(xa,xc))
                    union = area+(yd-yc)*(xd-xc)-intersection
                    if intersection > 0 and 255*intersection > in_threshold*union:
                        blocked |= 1 << j
        result |= count << 8
        return {"out_values": result}
