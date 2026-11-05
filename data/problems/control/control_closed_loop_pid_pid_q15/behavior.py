def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def sat_s16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


def trunc_div_toward_zero(n, d):
    if d == 0:
        return 0
    q = abs(n) // abs(d)
    return -q if (n < 0) ^ (d < 0) else q


def abs_limit_q15(limit):
    limit = to_signed(limit, 16)
    if limit >= 0:
        return limit
    if limit == -32768:
        return 32767
    return -limit


class ControlClosedLoopPidPidQ15:
    def __init__(self):
        self.reset()

    def reset(self):
        self.error_prev = 0
        self.output_prev = 0
        self.integral_prev = 0

    def process(self, in_error, in_p_gain, in_i_gain, in_d_gain, in_dt, in_output_ramp, in_limit):
        error = to_signed(in_error, 16)
        p_gain = to_signed(in_p_gain, 16)
        i_gain = to_signed(in_i_gain, 16)
        d_gain = to_signed(in_d_gain, 16)
        dt = to_signed(in_dt, 16)
        output_ramp = to_signed(in_output_ramp, 16)
        limit = to_signed(in_limit, 16)

        error_prev = self.error_prev
        output_prev = self.output_prev
        integral_prev = self.integral_prev

        proportional_q30 = p_gain * error
        proportional = proportional_q30 >> 15

        sum_error = error + error_prev
        i_dt_q30 = i_gain * dt
        i_dt_half_q15 = i_dt_q30 >> 16
        integral_delta_q30 = i_dt_half_q15 * sum_error
        integral_delta = integral_delta_q30 >> 15
        integral_unsat = integral_prev + integral_delta

        limit_pos = abs_limit_q15(limit)
        if integral_unsat > limit_pos:
            integral = limit_pos
        elif integral_unsat < -limit_pos:
            integral = -limit_pos
        else:
            integral = integral_unsat

        error_delta = error - error_prev
        d_num_q30 = d_gain * error_delta
        if dt == 0:
            derivative = 0
        else:
            derivative = trunc_div_toward_zero(d_num_q30, dt)
        derivative = sat_s16(derivative)

        output_unsat = proportional + integral + derivative
        if output_unsat > limit_pos:
            output_limited = limit_pos
        elif output_unsat < -limit_pos:
            output_limited = -limit_pos
        else:
            output_limited = output_unsat

        if output_ramp > 0 and dt > 0:
            output_step = output_limited - output_prev
            output_rate_q30 = output_step << 15
            output_rate = trunc_div_toward_zero(output_rate_q30, dt)
            ramp_step_q30 = output_ramp * dt
            ramp_step = ramp_step_q30 >> 15
            if output_rate > output_ramp:
                output = output_prev + ramp_step
            elif output_rate < -output_ramp:
                output = output_prev - ramp_step
            else:
                output = output_limited
        else:
            output = output_limited

        output = sat_s16(output)
        integral = sat_s16(integral)

        self.integral_prev = integral
        self.output_prev = output
        self.error_prev = sat_s16(error)

        return {"out_output": to_unsigned(output, 16)}