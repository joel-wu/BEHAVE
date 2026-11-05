module OrtegaPLL (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic signed [15:0] in_v_alpha, in_v_beta,
    input logic signed [15:0] in_i_alpha, in_i_beta,
    input logic signed [15:0] in_dt, in_r, in_l, in_lambda,
    input logic signed [15:0] in_gamma_half, in_pll_kp, in_pll_ki,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_obs_phase, out_pll_phase,
    output logic signed [31:0] out_pll_speed, out_x1, out_x2
);
    logic signed [31:0] x1, x2, speed, na, nb, nspeed;
    logic [31:0] phase, nphase, observed;
    // The largest unrounded observer product needs fewer than 75 signed bits.
    // This working type prevents all accidental expression-width truncation.
    logic signed [95:0] va, vb, ia, ib, dt, r, l, lam, gain, kp, ki;
    logic signed [95:0] lia, lib, ria, rib, fa, fb, err, ca, cb;
    logic signed [95:0] delta, kp_term, ki_term, increment;

    function automatic logic signed [95:0] tz(input logic signed [95:0] v, input integer bits);
        tz = v < 0 ? -((-v) >>> bits) : v >>> bits;
    endfunction
    function automatic logic signed [31:0] sat(input logic signed [95:0] v);
        if(v > 96'sd2147483647) sat = 32'sh7fffffff;
        else if(v < -96'sd2147483648) sat = 32'sh80000000;
        else sat = v[31:0];
    endfunction
    function automatic logic signed [31:0] boosted(input logic signed [95:0] v);
        boosted = sat(v * 96'sd11 / 96'sd10);
    endfunction
    function automatic logic [31:0] atan_turn(input logic signed [95:0] y, x);
        logic [63:0] ax, ay, base;
        begin
            ax = x < 0 ? -x : x;
            ay = y < 0 ? -y : y;
            base = 0;
            if(ax >= ay && ax != 0) base = (ay << 29) / ax;
            else if(ay != 0) base = 64'd1073741824 - (ax << 29) / ay;
            if(x < 0) base = y >= 0 ? 64'd2147483648 - base : 64'd2147483648 + base;
            else if(y < 0) base = 64'd4294967296 - base;
            atan_turn = base[31:0];
        end
    endfunction

    always_comb begin
        va = in_v_alpha; va = va <<< 9;
        vb = in_v_beta; vb = vb <<< 9;
        ia = in_i_alpha; ib = in_i_beta;
        dt = in_dt; r = in_r; l = in_l;
        lam = in_lambda; lam = lam <<< 9;
        gain = in_gamma_half; kp = in_pll_kp; ki = in_pll_ki;
        lia = tz(l * ia, 6); lib = tz(l * ib, 6);
        ria = tz(r * ia, 6); rib = tz(r * ib, 6);
        fa = x1 - lia; fb = x2 - lib;
        err = tz(lam * lam - fa * fa - fb * fb, 24);
        if(err > 0) err = 0;
        ca = tz(gain * tz(fa * err, 24), 15);
        cb = tz(gain * tz(fb * err, 24), 15);
        na = sat(x1 + tz((va - ria + ca) * dt, 15));
        nb = sat(x2 + tz((vb - rib + cb) * dt, 15));
        // Assign to the wide working values before squaring.
        fa = na; fb = nb;
        if(lam > 0 && 96'sd4 * (fa * fa + fb * fb) < lam * lam) begin
            na = boosted(fa); nb = boosted(fb);
        end
        fa = na; fb = nb;
        observed = atan_turn(fb - lib, fa - lia);
        delta = observed; delta = delta - phase;
        if(delta >= 96'sd2147483648) delta = delta - 96'sd4294967296;
        if(delta < -96'sd2147483648) delta = delta + 96'sd4294967296;
        kp_term = tz(kp * delta, 23); ki_term = tz(ki * delta, 23);
        increment = tz((speed + kp_term) * dt, 7);
        nphase = phase + increment;
        nspeed = sat(speed + tz(ki_term * dt, 15));
    end

    assign in_ready = !rst && (!out_valid || out_ready);
    always_ff @(posedge clk) begin
        if(rst) begin
            x1 <= 0; x2 <= 0; phase <= 0; speed <= 0;
            out_valid <= 0; out_obs_phase <= 0; out_pll_phase <= 0;
            out_pll_speed <= 0; out_x1 <= 0; out_x2 <= 0;
        end else if(in_ready) begin
            out_valid <= in_valid;
            if(in_valid) begin
                x1 <= na; x2 <= nb; phase <= nphase; speed <= nspeed;
                out_obs_phase <= observed; out_pll_phase <= nphase;
                out_pll_speed <= nspeed; out_x1 <= na; out_x2 <= nb;
            end
        end
    end
endmodule
