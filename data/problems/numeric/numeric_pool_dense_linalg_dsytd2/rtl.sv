module symmetric_tridiagonal_step4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic signed [31:0] in_a00,
    input  logic signed [31:0] in_a10,
    input  logic signed [31:0] in_a11,
    input  logic signed [31:0] in_a20,
    input  logic signed [31:0] in_a21,
    input  logic signed [31:0] in_a22,
    input  logic signed [31:0] in_a30,
    input  logic signed [31:0] in_a31,
    input  logic signed [31:0] in_a32,
    input  logic signed [31:0] in_a33,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [31:0] out_d0,
    output logic signed [31:0] out_d1,
    output logic signed [31:0] out_d2,
    output logic signed [31:0] out_d3,
    output logic signed [31:0] out_e0,
    output logic signed [31:0] out_e1,
    output logic signed [31:0] out_e2,
    output logic signed [31:0] out_tau0,
    output logic signed [31:0] out_tau1,
    output logic signed [31:0] out_tau2,
    output logic signed [31:0] out_v10,
    output logic signed [31:0] out_v20,
    output logic signed [31:0] out_v30,
    output logic signed [31:0] out_v21,
    output logic signed [31:0] out_v31,
    output logic signed [31:0] out_v32
);

    localparam logic signed [31:0] ONE_Q16      = 32'sh00010000;
    localparam logic signed [31:0] NEG_HALF_Q16 = 32'shFFFF8000;

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic signed [31:0] trunc32(input logic signed [63:0] v);
        logic signed [31:0] tmp;
        begin
            tmp = v[31:0];
            trunc32 = tmp;
        end
    endfunction

    function automatic logic signed [31:0] mul_q16(
        input logic signed [31:0] a,
        input logic signed [31:0] b
    );
        logic signed [63:0] prod;
        logic signed [63:0] shifted;
        begin
            prod = a * b;
            shifted = prod >>> 16;
            mul_q16 = trunc32(shifted);
        end
    endfunction

    function automatic logic signed [31:0] div_q16(
        input logic signed [31:0] a,
        input logic signed [31:0] b
    );
        logic signed [63:0] num;
        logic signed [63:0] q;
        logic signed [63:0] r;
        begin
            if (b == 0) begin
                div_q16 = 32'sd0;
            end else begin
                num = ({{32{a[31]}}, a}) <<< 16;
                q = num / b;
                r = num % b;
                if ((r != 0) && ((num < 0) != (b < 0))) begin
                    q = q - 64'sd1;
                end
                div_q16 = trunc32(q);
            end
        end
    endfunction

    function automatic logic signed [31:0] sqrt_q16(input logic signed [31:0] x);
        logic [63:0] target;
        logic [24:0] lo;
        logic [24:0] hi;
        logic [24:0] mid;
        logic [49:0] midsq;
        integer i;
        begin
            if (x <= 0) begin
                sqrt_q16 = 32'sd0;
            end else begin
                target = {x, 16'd0};
                lo = 25'd0;
                hi = 25'd16777216;
                for (i = 0; i < 25; i = i + 1) begin
                    mid = (lo + hi + 25'd1) >> 1;
                    midsq = mid * mid;
                    if (midsq <= target) begin
                        lo = mid;
                    end else begin
                        hi = mid - 25'd1;
                    end
                end
                sqrt_q16 = $signed({7'd0, lo});
            end
        end
    endfunction

    logic signed [31:0] n_d0;
    logic signed [31:0] n_d1;
    logic signed [31:0] n_d2;
    logic signed [31:0] n_d3;
    logic signed [31:0] n_e0;
    logic signed [31:0] n_e1;
    logic signed [31:0] n_e2;
    logic signed [31:0] n_tau0;
    logic signed [31:0] n_tau1;
    logic signed [31:0] n_tau2;
    logic signed [31:0] n_v10;
    logic signed [31:0] n_v20;
    logic signed [31:0] n_v30;
    logic signed [31:0] n_v21;
    logic signed [31:0] n_v31;
    logic signed [31:0] n_v32;

    always_comb begin
        logic signed [31:0] A00;
        logic signed [31:0] A10;
        logic signed [31:0] A11;
        logic signed [31:0] A20;
        logic signed [31:0] A21;
        logic signed [31:0] A22;
        logic signed [31:0] A30;
        logic signed [31:0] A31;
        logic signed [31:0] A32;
        logic signed [31:0] A33;

        logic signed [31:0] alpha0_in;
        logic signed [31:0] x0;
        logic signed [31:0] x1;
        logic signed [31:0] xx0;
        logic signed [31:0] xx1;
        logic signed [31:0] sigma0;
        logic signed [31:0] aa0;
        logic signed [31:0] norm0;
        logic signed [31:0] beta0;
        logic signed [31:0] tau0;
        logic signed [31:0] scale0;
        logic signed [31:0] v0_tail0;
        logic signed [31:0] v0_tail1;
        logic signed [31:0] E0;
        logic signed [31:0] V10;
        logic signed [31:0] V20;
        logic signed [31:0] V30;

        logic signed [31:0] hv0;
        logic signed [31:0] hv1;
        logic signed [31:0] hv2;

        logic signed [31:0] S00;
        logic signed [31:0] S01;
        logic signed [31:0] S02;
        logic signed [31:0] S10;
        logic signed [31:0] S11;
        logic signed [31:0] S12;
        logic signed [31:0] S20;
        logic signed [31:0] S21;
        logic signed [31:0] S22;

        logic signed [31:0] m_s00_h0;
        logic signed [31:0] m_s01_h1;
        logic signed [31:0] m_s02_h2;
        logic signed [31:0] m_s10_h0;
        logic signed [31:0] m_s11_h1;
        logic signed [31:0] m_s12_h2;
        logic signed [31:0] m_s20_h0;
        logic signed [31:0] m_s21_h1;
        logic signed [31:0] m_s22_h2;
        logic signed [31:0] t0;
        logic signed [31:0] t1;
        logic signed [31:0] t2;
        logic signed [31:0] xv0;
        logic signed [31:0] xv1;
        logic signed [31:0] xv2;
        logic signed [31:0] m_xv0_h0;
        logic signed [31:0] m_xv1_h1;
        logic signed [31:0] m_xv2_h2;
        logic signed [31:0] dot0;
        logic signed [31:0] alpha_corr0;
        logic signed [31:0] m_ac0_h0;
        logic signed [31:0] m_ac0_h1;
        logic signed [31:0] m_ac0_h2;
        logic signed [31:0] w0;
        logic signed [31:0] w1;
        logic signed [31:0] w2;
        logic signed [31:0] m_h0_w0;
        logic signed [31:0] m_w0_h0;
        logic signed [31:0] m_h0_w1;
        logic signed [31:0] m_w0_h1;
        logic signed [31:0] m_h0_w2;
        logic signed [31:0] m_w0_h2;
        logic signed [31:0] m_h1_w1;
        logic signed [31:0] m_w1_h1;
        logic signed [31:0] m_h1_w2;
        logic signed [31:0] m_w1_h2;
        logic signed [31:0] m_h2_w2;
        logic signed [31:0] m_w2_h2;

        logic signed [31:0] D0;

        logic signed [31:0] alpha1_in;
        logic signed [31:0] y0;
        logic signed [31:0] yy0;
        logic signed [31:0] sigma1;
        logic signed [31:0] aa1;
        logic signed [31:0] norm1;
        logic signed [31:0] beta1;
        logic signed [31:0] tau1;
        logic signed [31:0] scale1;
        logic signed [31:0] v1_tail0;
        logic signed [31:0] E1;
        logic signed [31:0] V21;
        logic signed [31:0] V31;

        logic signed [31:0] gv0;
        logic signed [31:0] gv1;
        logic signed [31:0] T00;
        logic signed [31:0] T01;
        logic signed [31:0] T10;
        logic signed [31:0] T11;
        logic signed [31:0] m_t00_g0;
        logic signed [31:0] m_t01_g1;
        logic signed [31:0] m_t10_g0;
        logic signed [31:0] m_t11_g1;
        logic signed [31:0] u0;
        logic signed [31:0] u1;
        logic signed [31:0] yv0;
        logic signed [31:0] yv1;
        logic signed [31:0] m_yv0_g0;
        logic signed [31:0] m_yv1_g1;
        logic signed [31:0] dot1;
        logic signed [31:0] alpha_corr1;
        logic signed [31:0] m_ac1_g0;
        logic signed [31:0] m_ac1_g1;
        logic signed [31:0] z0;
        logic signed [31:0] z1;
        logic signed [31:0] m_g0_z0;
        logic signed [31:0] m_z0_g0;
        logic signed [31:0] m_g0_z1;
        logic signed [31:0] m_z0_g1;
        logic signed [31:0] m_g1_z1;
        logic signed [31:0] m_z1_g1;

        logic signed [31:0] D1;
        logic signed [31:0] tau2;
        logic signed [31:0] E2;
        logic signed [31:0] D2;
        logic signed [31:0] D3;
        logic signed [31:0] V32;

        A00 = 32'sd0;
        A10 = 32'sd0;
        A11 = 32'sd0;
        A20 = 32'sd0;
        A21 = 32'sd0;
        A22 = 32'sd0;
        A30 = 32'sd0;
        A31 = 32'sd0;
        A32 = 32'sd0;
        A33 = 32'sd0;

        alpha0_in = 32'sd0;
        x0 = 32'sd0;
        x1 = 32'sd0;
        xx0 = 32'sd0;
        xx1 = 32'sd0;
        sigma0 = 32'sd0;
        aa0 = 32'sd0;
        norm0 = 32'sd0;
        beta0 = 32'sd0;
        tau0 = 32'sd0;
        scale0 = 32'sd0;
        v0_tail0 = 32'sd0;
        v0_tail1 = 32'sd0;
        E0 = 32'sd0;
        V10 = 32'sd0;
        V20 = 32'sd0;
        V30 = 32'sd0;

        hv0 = 32'sd0;
        hv1 = 32'sd0;
        hv2 = 32'sd0;

        S00 = 32'sd0;
        S01 = 32'sd0;
        S02 = 32'sd0;
        S10 = 32'sd0;
        S11 = 32'sd0;
        S12 = 32'sd0;
        S20 = 32'sd0;
        S21 = 32'sd0;
        S22 = 32'sd0;

        m_s00_h0 = 32'sd0;
        m_s01_h1 = 32'sd0;
        m_s02_h2 = 32'sd0;
        m_s10_h0 = 32'sd0;
        m_s11_h1 = 32'sd0;
        m_s12_h2 = 32'sd0;
        m_s20_h0 = 32'sd0;
        m_s21_h1 = 32'sd0;
        m_s22_h2 = 32'sd0;
        t0 = 32'sd0;
        t1 = 32'sd0;
        t2 = 32'sd0;
        xv0 = 32'sd0;
        xv1 = 32'sd0;
        xv2 = 32'sd0;
        m_xv0_h0 = 32'sd0;
        m_xv1_h1 = 32'sd0;
        m_xv2_h2 = 32'sd0;
        dot0 = 32'sd0;
        alpha_corr0 = 32'sd0;
        m_ac0_h0 = 32'sd0;
        m_ac0_h1 = 32'sd0;
        m_ac0_h2 = 32'sd0;
        w0 = 32'sd0;
        w1 = 32'sd0;
        w2 = 32'sd0;
        m_h0_w0 = 32'sd0;
        m_w0_h0 = 32'sd0;
        m_h0_w1 = 32'sd0;
        m_w0_h1 = 32'sd0;
        m_h0_w2 = 32'sd0;
        m_w0_h2 = 32'sd0;
        m_h1_w1 = 32'sd0;
        m_w1_h1 = 32'sd0;
        m_h1_w2 = 32'sd0;
        m_w1_h2 = 32'sd0;
        m_h2_w2 = 32'sd0;
        m_w2_h2 = 32'sd0;

        D0 = 32'sd0;

        alpha1_in = 32'sd0;
        y0 = 32'sd0;
        yy0 = 32'sd0;
        sigma1 = 32'sd0;
        aa1 = 32'sd0;
        norm1 = 32'sd0;
        beta1 = 32'sd0;
        tau1 = 32'sd0;
        scale1 = 32'sd0;
        v1_tail0 = 32'sd0;
        E1 = 32'sd0;
        V21 = 32'sd0;
        V31 = 32'sd0;

        gv0 = 32'sd0;
        gv1 = 32'sd0;
        T00 = 32'sd0;
        T01 = 32'sd0;
        T10 = 32'sd0;
        T11 = 32'sd0;
        m_t00_g0 = 32'sd0;
        m_t01_g1 = 32'sd0;
        m_t10_g0 = 32'sd0;
        m_t11_g1 = 32'sd0;
        u0 = 32'sd0;
        u1 = 32'sd0;
        yv0 = 32'sd0;
        yv1 = 32'sd0;
        m_yv0_g0 = 32'sd0;
        m_yv1_g1 = 32'sd0;
        dot1 = 32'sd0;
        alpha_corr1 = 32'sd0;
        m_ac1_g0 = 32'sd0;
        m_ac1_g1 = 32'sd0;
        z0 = 32'sd0;
        z1 = 32'sd0;
        m_g0_z0 = 32'sd0;
        m_z0_g0 = 32'sd0;
        m_g0_z1 = 32'sd0;
        m_z0_g1 = 32'sd0;
        m_g1_z1 = 32'sd0;
        m_z1_g1 = 32'sd0;

        D1 = 32'sd0;
        tau2 = 32'sd0;
        E2 = 32'sd0;
        D2 = 32'sd0;
        D3 = 32'sd0;
        V32 = 32'sd0;

        A00 = in_a00;
        A10 = in_a10;
        A11 = in_a11;
        A20 = in_a20;
        A21 = in_a21;
        A22 = in_a22;
        A30 = in_a30;
        A31 = in_a31;
        A32 = in_a32;
        A33 = in_a33;

        alpha0_in = A10;
        x0 = A20;
        x1 = A30;
        xx0 = mul_q16(x0, x0);
        xx1 = mul_q16(x1, x1);
        sigma0 = trunc32({{32{xx0[31]}}, xx0} + {{32{xx1[31]}}, xx1});
        if (sigma0 == 0) begin
            tau0 = 32'sd0;
            beta0 = alpha0_in;
            v0_tail0 = x0;
            v0_tail1 = x1;
        end else begin
            aa0 = mul_q16(alpha0_in, alpha0_in);
            norm0 = sqrt_q16(trunc32({{32{aa0[31]}}, aa0} + {{32{sigma0[31]}}, sigma0}));
            if (alpha0_in >= 0) begin
                beta0 = trunc32(-{{32{norm0[31]}}, norm0});
            end else begin
                beta0 = norm0;
            end
            tau0 = div_q16(trunc32({{32{beta0[31]}}, beta0} - {{32{alpha0_in[31]}}, alpha0_in}), beta0);
            scale0 = div_q16(ONE_Q16, trunc32({{32{alpha0_in[31]}}, alpha0_in} - {{32{beta0[31]}}, beta0}));
            v0_tail0 = mul_q16(x0, scale0);
            v0_tail1 = mul_q16(x1, scale0);
        end

        E0 = beta0;
        V10 = E0;
        V20 = v0_tail0;
        V30 = v0_tail1;

        if (tau0 != 0) begin
            hv0 = ONE_Q16;
            hv1 = v0_tail0;
            hv2 = v0_tail1;

            S00 = A11;
            S01 = A21;
            S02 = A31;
            S10 = A21;
            S11 = A22;
            S12 = A32;
            S20 = A31;
            S21 = A32;
            S22 = A33;

            m_s00_h0 = mul_q16(S00, hv0);
            m_s01_h1 = mul_q16(S01, hv1);
            m_s02_h2 = mul_q16(S02, hv2);
            t0 = trunc32({{32{m_s00_h0[31]}}, m_s00_h0} + {{32{m_s01_h1[31]}}, m_s01_h1} + {{32{m_s02_h2[31]}}, m_s02_h2});

            m_s10_h0 = mul_q16(S10, hv0);
            m_s11_h1 = mul_q16(S11, hv1);
            m_s12_h2 = mul_q16(S12, hv2);
            t1 = trunc32({{32{m_s10_h0[31]}}, m_s10_h0} + {{32{m_s11_h1[31]}}, m_s11_h1} + {{32{m_s12_h2[31]}}, m_s12_h2});

            m_s20_h0 = mul_q16(S20, hv0);
            m_s21_h1 = mul_q16(S21, hv1);
            m_s22_h2 = mul_q16(S22, hv2);
            t2 = trunc32({{32{m_s20_h0[31]}}, m_s20_h0} + {{32{m_s21_h1[31]}}, m_s21_h1} + {{32{m_s22_h2[31]}}, m_s22_h2});

            xv0 = mul_q16(tau0, t0);
            xv1 = mul_q16(tau0, t1);
            xv2 = mul_q16(tau0, t2);

            m_xv0_h0 = mul_q16(xv0, hv0);
            m_xv1_h1 = mul_q16(xv1, hv1);
            m_xv2_h2 = mul_q16(xv2, hv2);
            dot0 = trunc32({{32{m_xv0_h0[31]}}, m_xv0_h0} + {{32{m_xv1_h1[31]}}, m_xv1_h1} + {{32{m_xv2_h2[31]}}, m_xv2_h2});

            alpha_corr0 = mul_q16(mul_q16(NEG_HALF_Q16, tau0), dot0);

            m_ac0_h0 = mul_q16(alpha_corr0, hv0);
            m_ac0_h1 = mul_q16(alpha_corr0, hv1);
            m_ac0_h2 = mul_q16(alpha_corr0, hv2);
            w0 = trunc32({{32{xv0[31]}}, xv0} + {{32{m_ac0_h0[31]}}, m_ac0_h0});
            w1 = trunc32({{32{xv1[31]}}, xv1} + {{32{m_ac0_h1[31]}}, m_ac0_h1});
            w2 = trunc32({{32{xv2[31]}}, xv2} + {{32{m_ac0_h2[31]}}, m_ac0_h2});

            m_h0_w0 = mul_q16(hv0, w0);
            m_w0_h0 = mul_q16(w0, hv0);
            S00 = trunc32({{32{S00[31]}}, S00} - {{32{m_h0_w0[31]}}, m_h0_w0} - {{32{m_w0_h0[31]}}, m_w0_h0});

            m_h0_w1 = mul_q16(hv0, w1);
            m_w0_h1 = mul_q16(w0, hv1);
            S01 = trunc32({{32{S01[31]}}, S01} - {{32{m_h0_w1[31]}}, m_h0_w1} - {{32{m_w0_h1[31]}}, m_w0_h1});

            m_h0_w2 = mul_q16(hv0, w2);
            m_w0_h2 = mul_q16(w0, hv2);
            S02 = trunc32({{32{S02[31]}}, S02} - {{32{m_h0_w2[31]}}, m_h0_w2} - {{32{m_w0_h2[31]}}, m_w0_h2});

            m_h1_w1 = mul_q16(hv1, w1);
            m_w1_h1 = mul_q16(w1, hv1);
            S11 = trunc32({{32{S11[31]}}, S11} - {{32{m_h1_w1[31]}}, m_h1_w1} - {{32{m_w1_h1[31]}}, m_w1_h1});

            m_h1_w2 = mul_q16(hv1, w2);
            m_w1_h2 = mul_q16(w1, hv2);
            S12 = trunc32({{32{S12[31]}}, S12} - {{32{m_h1_w2[31]}}, m_h1_w2} - {{32{m_w1_h2[31]}}, m_w1_h2});

            m_h2_w2 = mul_q16(hv2, w2);
            m_w2_h2 = mul_q16(w2, hv2);
            S22 = trunc32({{32{S22[31]}}, S22} - {{32{m_h2_w2[31]}}, m_h2_w2} - {{32{m_w2_h2[31]}}, m_w2_h2});

            A11 = S00;
            A21 = S01;
            A31 = S02;
            A22 = S11;
            A32 = S12;
            A33 = S22;
        end

        D0 = A00;

        alpha1_in = A21;
        y0 = A31;
        yy0 = mul_q16(y0, y0);
        sigma1 = yy0;
        if (sigma1 == 0) begin
            tau1 = 32'sd0;
            beta1 = alpha1_in;
            v1_tail0 = y0;
        end else begin
            aa1 = mul_q16(alpha1_in, alpha1_in);
            norm1 = sqrt_q16(trunc32({{32{aa1[31]}}, aa1} + {{32{sigma1[31]}}, sigma1}));
            if (alpha1_in >= 0) begin
                beta1 = trunc32(-{{32{norm1[31]}}, norm1});
            end else begin
                beta1 = norm1;
            end
            tau1 = div_q16(trunc32({{32{beta1[31]}}, beta1} - {{32{alpha1_in[31]}}, alpha1_in}), beta1);
            scale1 = div_q16(ONE_Q16, trunc32({{32{alpha1_in[31]}}, alpha1_in} - {{32{beta1[31]}}, beta1}));
            v1_tail0 = mul_q16(y0, scale1);
        end

        E1 = beta1;
        V21 = E1;
        V31 = v1_tail0;

        if (tau1 != 0) begin
            gv0 = ONE_Q16;
            gv1 = v1_tail0;

            T00 = A22;
            T01 = A32;
            T10 = A32;
            T11 = A33;

            m_t00_g0 = mul_q16(T00, gv0);
            m_t01_g1 = mul_q16(T01, gv1);
            u0 = trunc32({{32{m_t00_g0[31]}}, m_t00_g0} + {{32{m_t01_g1[31]}}, m_t01_g1});

            m_t10_g0 = mul_q16(T10, gv0);
            m_t11_g1 = mul_q16(T11, gv1);
            u1 = trunc32({{32{m_t10_g0[31]}}, m_t10_g0} + {{32{m_t11_g1[31]}}, m_t11_g1});

            yv0 = mul_q16(tau1, u0);
            yv1 = mul_q16(tau1, u1);

            m_yv0_g0 = mul_q16(yv0, gv0);
            m_yv1_g1 = mul_q16(yv1, gv1);
            dot1 = trunc32({{32{m_yv0_g0[31]}}, m_yv0_g0} + {{32{m_yv1_g1[31]}}, m_yv1_g1});

            alpha_corr1 = mul_q16(mul_q16(NEG_HALF_Q16, tau1), dot1);

            m_ac1_g0 = mul_q16(alpha_corr1, gv0);
            m_ac1_g1 = mul_q16(alpha_corr1, gv1);
            z0 = trunc32({{32{yv0[31]}}, yv0} + {{32{m_ac1_g0[31]}}, m_ac1_g0});
            z1 = trunc32({{32{yv1[31]}}, yv1} + {{32{m_ac1_g1[31]}}, m_ac1_g1});

            m_g0_z0 = mul_q16(gv0, z0);
            m_z0_g0 = mul_q16(z0, gv0);
            T00 = trunc32({{32{T00[31]}}, T00} - {{32{m_g0_z0[31]}}, m_g0_z0} - {{32{m_z0_g0[31]}}, m_z0_g0});

            m_g0_z1 = mul_q16(gv0, z1);
            m_z0_g1 = mul_q16(z0, gv1);
            T01 = trunc32({{32{T01[31]}}, T01} - {{32{m_g0_z1[31]}}, m_g0_z1} - {{32{m_z0_g1[31]}}, m_z0_g1});

            m_g1_z1 = mul_q16(gv1, z1);
            m_z1_g1 = mul_q16(z1, gv1);
            T11 = trunc32({{32{T11[31]}}, T11} - {{32{m_g1_z1[31]}}, m_g1_z1} - {{32{m_z1_g1[31]}}, m_z1_g1});

            A22 = T00;
            A32 = T01;
            A33 = T11;
        end

        D1 = A11;
        tau2 = 32'sd0;
        E2 = A32;
        D2 = A22;
        D3 = A33;
        V32 = E2;

        n_d0   = D0;
        n_d1   = D1;
        n_d2   = D2;
        n_d3   = D3;
        n_e0   = E0;
        n_e1   = E1;
        n_e2   = E2;
        n_tau0 = tau0;
        n_tau1 = tau1;
        n_tau2 = tau2;
        n_v10  = V10;
        n_v20  = V20;
        n_v30  = V30;
        n_v21  = V21;
        n_v31  = V31;
        n_v32  = V32;
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy     <= 1'b0;
            out_d0   <= 32'sd0;
            out_d1   <= 32'sd0;
            out_d2   <= 32'sd0;
            out_d3   <= 32'sd0;
            out_e0   <= 32'sd0;
            out_e1   <= 32'sd0;
            out_e2   <= 32'sd0;
            out_tau0 <= 32'sd0;
            out_tau1 <= 32'sd0;
            out_tau2 <= 32'sd0;
            out_v10  <= 32'sd0;
            out_v20  <= 32'sd0;
            out_v30  <= 32'sd0;
            out_v21  <= 32'sd0;
            out_v31  <= 32'sd0;
            out_v32  <= 32'sd0;
        end else if (input_accepted) begin
            busy     <= 1'b1;
            out_d0   <= n_d0;
            out_d1   <= n_d1;
            out_d2   <= n_d2;
            out_d3   <= n_d3;
            out_e0   <= n_e0;
            out_e1   <= n_e1;
            out_e2   <= n_e2;
            out_tau0 <= n_tau0;
            out_tau1 <= n_tau1;
            out_tau2 <= n_tau2;
            out_v10  <= n_v10;
            out_v20  <= n_v20;
            out_v30  <= n_v30;
            out_v21  <= n_v21;
            out_v31  <= n_v31;
            out_v32  <= n_v32;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule