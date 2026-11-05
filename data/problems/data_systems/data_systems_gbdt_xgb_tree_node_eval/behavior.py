def is_nan(word):
    return (word & 2139095040) == 2139095040 and (word & 8388607) != 0


def less(a, b):
    if is_nan(a) or is_nan(b):
        return 0
    am = a & 2147483647
    bm = b & 2147483647
    if am == 0 and bm == 0:
        return 0
    sa = (a >> 31) & 1
    sb = (b >> 31) & 1
    return int(sa > sb) if sa != sb else int(am > bm) if sa else int(am < bm)


class XGBoostTreeNodeEvaluator:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_fvalue, in_is_missing, in_has_missing, in_has_categorical,
                in_node_is_categorical, in_split_cond, in_left_child, in_default_child,
                in_cat_begin, in_cat_size, in_cat_words):
        left = 0
        error = 0
        if in_has_missing and in_is_missing:
            next_node = in_default_child
        else:
            if in_has_categorical and in_node_is_categorical:
                if in_cat_begin+in_cat_size > 8 or is_nan(in_fvalue):
                    error = 1
                else:
                    magnitude = in_fvalue & 2147483647
                    negative = ((in_fvalue >> 31) & 1) and magnitude != 0
                    left = 1
                    if not negative and magnitude < 1266679808:
                        exponent = (in_fvalue >> 23) & 255
                        key = 0
                        if exponent >= 127:
                            key = ((in_fvalue & 8388607) | 8388608) >> (150-exponent)
                        if key < in_cat_size*32:
                            position = 32*(in_cat_begin+key//32)+31-key%32
                            left = 1-((in_cat_words >> position) & 1)
            else:
                left = less(in_fvalue,in_split_cond)
            next_node = in_left_child if left else (in_left_child+1) & 4294967295
        if error:
            left = 0
            next_node = 0
        return {"out_decision":left,"out_next_node":next_node,"out_error":error}
