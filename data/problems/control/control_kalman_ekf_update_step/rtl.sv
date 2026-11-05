module control_kalman_ekf_update_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_x0,
    input  logic [15:0]  in_x1,
    input  logic [31:0]  in_p00,
    input  logic [31:0]  in_p01,
    input  logic [31:0]  in_p10,
    input  logic [31:0]  in_p11,
    input  logic [15:0]  in_h0,
    input  logic [15:0]  in_h1,
    input  logic [15:0]  in_hx,
    input  logic [15:0]  in_z,
    input  logic [31:0]  in_r,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_x0,
    output logic [15:0]  out_x1,
    output logic [31:0]  out_p00,
    output logic [31:0]  out_p01,
    output logic [31:0]  out_p10,
    output logic [31:0]  out_p11,
    output logic [15:0]  out_k0,
    output logic [15:0]  out_k1,
    output logic [15:0]  out_y,
    output logic [31:0]  out_s
);

logic busy;
wire output_consumed;
wire input_accepted;

assign out_valid = busy;
assign in_ready  = ~busy | output_consumed;

assign output_consumed = out_valid & out_ready;
assign input_accepted  = in_valid & in_ready;

function automatic logic signed [15:0] sat16(input logic signed [31:0] v);
    begin
        if (v > 32'sd32767)
            sat16 = 16'sd32767;
        else if (v < -32'sd32768)
            sat16 = -16'sd32768;
        else
            sat16 = v[15:0];
    end
endfunction

function automatic logic signed [31:0] sat32(input logic signed [63:0] v);
    begin
        if (v > 64'sd2147483647)
            sat32 = 32'sd2147483647;
        else if (v < -64'sd2147483648)
            sat32 = -32'sd2147483648;
        else
            sat32 = v[31:0];
    end
endfunction

function automatic logic signed [15:0] trunc_div_q31_to_q15(
    input logic signed [31:0] pht,
    input logic signed [31:0] s
);
    logic signed [63:0] num;
    logic signed [63:0] abs_num;
    logic signed [63:0] abs_den;
    logic signed [63:0] q_abs;
    logic signed [63:0] q_signed;
    logic               neg_result;
    begin
        trunc_div_q31_to_q15 = 16'sd0;
        if (s == 32'sd0) begin
            trunc_div_q31_to_q15 = 16'sd0;
        end else begin
            num = $signed({{32{pht[31]}}, pht}) <<< 15;
            if (num < 0)
                abs_num = -num;
            else
                abs_num = num;

            if ($signed(s) < 0)
                abs_den = -$signed({{32{s[31]}}, s});
            else
                abs_den =  $signed({{32{s[31]}}, s});

            q_abs = abs_num / abs_den;
            neg_result = (num < 0) != ($signed(s) < 0);

            if (neg_result)
                q_signed = -q_abs;
            else
                q_signed = q_abs;

            if (q_signed > 64'sd32767)
                trunc_div_q31_to_q15 = 16'sd32767;
            else if (q_signed < -64'sd32768)
                trunc_div_q31_to_q15 = -16'sd32768;
            else
                trunc_div_q31_to_q15 = q_signed[15:0];
        end
    end
endfunction

logic signed [15:0] x0_s;
logic signed [15:0] x1_s;
logic signed [31:0] p00_s;
logic signed [31:0] p01_s;
logic signed [31:0] p10_s;
logic signed [31:0] p11_s;
logic signed [15:0] h0_s;
logic signed [15:0] h1_s;
logic signed [15:0] hx_s;
logic signed [15:0] z_s;
logic signed [31:0] r_s;

logic signed [16:0] y_int;
logic signed [15:0] y;

logic signed [47:0] p00h0_prod;
logic signed [47:0] p01h1_prod;
logic signed [47:0] p10h0_prod;
logic signed [47:0] p11h1_prod;
logic signed [48:0] pht0_sum;
logic signed [48:0] pht1_sum;
logic signed [48:0] pht0_shr;
logic signed [48:0] pht1_shr;
logic signed [31:0] pht0;
logic signed [31:0] pht1;

logic signed [47:0] h0pht0_prod;
logic signed [47:0] h1pht1_prod;
logic signed [48:0] hpht_sum;
logic signed [48:0] hpht_shr;
logic signed [31:0] hpht;
logic signed [32:0] s_sum;
logic signed [31:0] s_val;

logic signed [15:0] k0;
logic signed [15:0] k1;

logic signed [31:0] k0y_prod;
logic signed [31:0] k1y_prod;
logic signed [31:0] x0_corr_ext;
logic signed [31:0] x1_corr_ext;
logic signed [15:0] x0_corr;
logic signed [15:0] x1_corr;
logic signed [16:0] x0_new_sum;
logic signed [16:0] x1_new_sum;
logic signed [15:0] x0_new;
logic signed [15:0] x1_new;

logic signed [31:0] k0h0_prod;
logic signed [31:0] k0h1_prod;
logic signed [31:0] k1h0_prod;
logic signed [31:0] k1h1_prod;
logic signed [15:0] k0h0_q15;
logic signed [15:0] k0h1_q15;
logic signed [15:0] k1h0_q15;
logic signed [15:0] k1h1_q15;
logic signed [31:0] a00_tmp;
logic signed [31:0] a01_tmp;
logic signed [31:0] a10_tmp;
logic signed [31:0] a11_tmp;
logic signed [15:0] a00;
logic signed [15:0] a01;
logic signed [15:0] a10;
logic signed [15:0] a11;

logic signed [47:0] a00p00_prod;
logic signed [47:0] a01p10_prod;
logic signed [47:0] a00p01_prod;
logic signed [47:0] a01p11_prod;
logic signed [47:0] a10p00_prod;
logic signed [47:0] a11p10_prod;
logic signed [47:0] a10p01_prod;
logic signed [47:0] a11p11_prod;
logic signed [48:0] m00_sum;
logic signed [48:0] m01_sum;
logic signed [48:0] m10_sum;
logic signed [48:0] m11_sum;
logic signed [48:0] m00_shr;
logic signed [48:0] m01_shr;
logic signed [48:0] m10_shr;
logic signed [48:0] m11_shr;
logic signed [31:0] m00;
logic signed [31:0] m01;
logic signed [31:0] m10;
logic signed [31:0] m11;

logic signed [47:0] m00a00_prod;
logic signed [47:0] m01a01_prod;
logic signed [47:0] m00a10_prod;
logic signed [47:0] m01a11_prod;
logic signed [47:0] m10a00_prod;
logic signed [47:0] m11a01_prod;
logic signed [47:0] m10a10_prod;
logic signed [47:0] m11a11_prod;
logic signed [48:0] j00_sum;
logic signed [48:0] j01_sum;
logic signed [48:0] j10_sum;
logic signed [48:0] j11_sum;
logic signed [48:0] j00_shr;
logic signed [48:0] j01_shr;
logic signed [48:0] j10_shr;
logic signed [48:0] j11_shr;
logic signed [31:0] j00;
logic signed [31:0] j01;
logic signed [31:0] j10;
logic signed [31:0] j11;

logic signed [47:0] k0r_prod;
logic signed [47:0] k1r_prod;
logic signed [47:0] kr0_shr;
logic signed [47:0] kr1_shr;
logic signed [31:0] kr0;
logic signed [31:0] kr1;

logic signed [47:0] kr0k0_prod;
logic signed [47:0] kr0k1_prod;
logic signed [47:0] kr1k0_prod;
logic signed [47:0] kr1k1_prod;
logic signed [47:0] n00_shr;
logic signed [47:0] n01_shr;
logic signed [47:0] n10_shr;
logic signed [47:0] n11_shr;
logic signed [31:0] n00;
logic signed [31:0] n01;
logic signed [31:0] n10;
logic signed [31:0] n11;

logic signed [32:0] p00_new_sum;
logic signed [32:0] p01_new_sum;
logic signed [32:0] p10_new_sum;
logic signed [32:0] p11_new_sum;
logic signed [31:0] p00_new;
logic signed [31:0] p01_new;
logic signed [31:0] p10_new;
logic signed [31:0] p11_new;

always_comb begin
    x0_s  = $signed(in_x0);
    x1_s  = $signed(in_x1);
    p00_s = $signed(in_p00);
    p01_s = $signed(in_p01);
    p10_s = $signed(in_p10);
    p11_s = $signed(in_p11);
    h0_s  = $signed(in_h0);
    h1_s  = $signed(in_h1);
    hx_s  = $signed(in_hx);
    z_s   = $signed(in_z);
    r_s   = $signed(in_r);

    y_int = z_s - hx_s;
    y = sat16($signed({{15{y_int[16]}}, y_int}));

    p00h0_prod = p00_s * h0_s;
    p01h1_prod = p01_s * h1_s;
    p10h0_prod = p10_s * h0_s;
    p11h1_prod = p11_s * h1_s;

    pht0_sum = $signed({p00h0_prod[47], p00h0_prod}) + $signed({p01h1_prod[47], p01h1_prod});
    pht1_sum = $signed({p10h0_prod[47], p10h0_prod}) + $signed({p11h1_prod[47], p11h1_prod});
    pht0_shr = pht0_sum >>> 15;
    pht1_shr = pht1_sum >>> 15;
    pht0 = sat32($signed({{15{pht0_shr[48]}}, pht0_shr}));
    pht1 = sat32($signed({{15{pht1_shr[48]}}, pht1_shr}));

    h0pht0_prod = h0_s * pht0;
    h1pht1_prod = h1_s * pht1;
    hpht_sum = $signed({h0pht0_prod[47], h0pht0_prod}) + $signed({h1pht1_prod[47], h1pht1_prod});
    hpht_shr = hpht_sum >>> 15;
    hpht = sat32($signed({{15{hpht_shr[48]}}, hpht_shr}));

    s_sum = $signed({hpht[31], hpht}) + $signed({r_s[31], r_s});
    s_val = sat32($signed({{31{s_sum[32]}}, s_sum}));

    if (s_val == 32'sd0) begin
        k0 = 16'sd0;
        k1 = 16'sd0;
    end else begin
        k0 = trunc_div_q31_to_q15(pht0, s_val);
        k1 = trunc_div_q31_to_q15(pht1, s_val);
    end

    k0y_prod = k0 * y;
    k1y_prod = k1 * y;
    x0_corr_ext = $signed(k0y_prod) >>> 15;
    x1_corr_ext = $signed(k1y_prod) >>> 15;
    x0_corr = sat16(x0_corr_ext);
    x1_corr = sat16(x1_corr_ext);

    x0_new_sum = x0_s + x0_corr;
    x1_new_sum = x1_s + x1_corr;
    x0_new = sat16($signed({{15{x0_new_sum[16]}}, x0_new_sum}));
    x1_new = sat16($signed({{15{x1_new_sum[16]}}, x1_new_sum}));

    k0h0_prod = k0 * h0_s;
    k0h1_prod = k0 * h1_s;
    k1h0_prod = k1 * h0_s;
    k1h1_prod = k1 * h1_s;
    k0h0_q15 = sat16($signed(k0h0_prod >>> 15));
    k0h1_q15 = sat16($signed(k0h1_prod >>> 15));
    k1h0_q15 = sat16($signed(k1h0_prod >>> 15));
    k1h1_q15 = sat16($signed(k1h1_prod >>> 15));

    a00_tmp = 32'sd32767 - k0h0_q15;
    a01_tmp = 32'sd0 - k0h1_q15;
    a10_tmp = 32'sd0 - k1h0_q15;
    a11_tmp = 32'sd32767 - k1h1_q15;
    a00 = sat16(a00_tmp);
    a01 = sat16(a01_tmp);
    a10 = sat16(a10_tmp);
    a11 = sat16(a11_tmp);

    a00p00_prod = a00 * p00_s;
    a01p10_prod = a01 * p10_s;
    a00p01_prod = a00 * p01_s;
    a01p11_prod = a01 * p11_s;
    a10p00_prod = a10 * p00_s;
    a11p10_prod = a11 * p10_s;
    a10p01_prod = a10 * p01_s;
    a11p11_prod = a11 * p11_s;

    m00_sum = $signed({a00p00_prod[47], a00p00_prod}) + $signed({a01p10_prod[47], a01p10_prod});
    m01_sum = $signed({a00p01_prod[47], a00p01_prod}) + $signed({a01p11_prod[47], a01p11_prod});
    m10_sum = $signed({a10p00_prod[47], a10p00_prod}) + $signed({a11p10_prod[47], a11p10_prod});
    m11_sum = $signed({a10p01_prod[47], a10p01_prod}) + $signed({a11p11_prod[47], a11p11_prod});

    m00_shr = m00_sum >>> 15;
    m01_shr = m01_sum >>> 15;
    m10_shr = m10_sum >>> 15;
    m11_shr = m11_sum >>> 15;

    m00 = sat32($signed({{15{m00_shr[48]}}, m00_shr}));
    m01 = sat32($signed({{15{m01_shr[48]}}, m01_shr}));
    m10 = sat32($signed({{15{m10_shr[48]}}, m10_shr}));
    m11 = sat32($signed({{15{m11_shr[48]}}, m11_shr}));

    m00a00_prod = m00 * a00;
    m01a01_prod = m01 * a01;
    m00a10_prod = m00 * a10;
    m01a11_prod = m01 * a11;
    m10a00_prod = m10 * a00;
    m11a01_prod = m11 * a01;
    m10a10_prod = m10 * a10;
    m11a11_prod = m11 * a11;

    j00_sum = $signed({m00a00_prod[47], m00a00_prod}) + $signed({m01a01_prod[47], m01a01_prod});
    j01_sum = $signed({m00a10_prod[47], m00a10_prod}) + $signed({m01a11_prod[47], m01a11_prod});
    j10_sum = $signed({m10a00_prod[47], m10a00_prod}) + $signed({m11a01_prod[47], m11a01_prod});
    j11_sum = $signed({m10a10_prod[47], m10a10_prod}) + $signed({m11a11_prod[47], m11a11_prod});

    j00_shr = j00_sum >>> 15;
    j01_shr = j01_sum >>> 15;
    j10_shr = j10_sum >>> 15;
    j11_shr = j11_sum >>> 15;

    j00 = sat32($signed({{15{j00_shr[48]}}, j00_shr}));
    j01 = sat32($signed({{15{j01_shr[48]}}, j01_shr}));
    j10 = sat32($signed({{15{j10_shr[48]}}, j10_shr}));
    j11 = sat32($signed({{15{j11_shr[48]}}, j11_shr}));

    k0r_prod = k0 * r_s;
    k1r_prod = k1 * r_s;
    kr0_shr = k0r_prod >>> 15;
    kr1_shr = k1r_prod >>> 15;
    kr0 = sat32($signed({{16{kr0_shr[47]}}, kr0_shr}));
    kr1 = sat32($signed({{16{kr1_shr[47]}}, kr1_shr}));

    kr0k0_prod = kr0 * k0;
    kr0k1_prod = kr0 * k1;
    kr1k0_prod = kr1 * k0;
    kr1k1_prod = kr1 * k1;

    n00_shr = kr0k0_prod >>> 15;
    n01_shr = kr0k1_prod >>> 15;
    n10_shr = kr1k0_prod >>> 15;
    n11_shr = kr1k1_prod >>> 15;

    n00 = sat32($signed({{16{n00_shr[47]}}, n00_shr}));
    n01 = sat32($signed({{16{n01_shr[47]}}, n01_shr}));
    n10 = sat32($signed({{16{n10_shr[47]}}, n10_shr}));
    n11 = sat32($signed({{16{n11_shr[47]}}, n11_shr}));

    p00_new_sum = $signed({j00[31], j00}) + $signed({n00[31], n00});
    p01_new_sum = $signed({j01[31], j01}) + $signed({n01[31], n01});
    p10_new_sum = $signed({j10[31], j10}) + $signed({n10[31], n10});
    p11_new_sum = $signed({j11[31], j11}) + $signed({n11[31], n11});

    p00_new = sat32($signed({{31{p00_new_sum[32]}}, p00_new_sum}));
    p01_new = sat32($signed({{31{p01_new_sum[32]}}, p01_new_sum}));
    p10_new = sat32($signed({{31{p10_new_sum[32]}}, p10_new_sum}));
    p11_new = sat32($signed({{31{p11_new_sum[32]}}, p11_new_sum}));
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy    <= 1'b0;
        out_x0  <= 16'h0000;
        out_x1  <= 16'h0000;
        out_p00 <= 32'h00000000;
        out_p01 <= 32'h00000000;
        out_p10 <= 32'h00000000;
        out_p11 <= 32'h00000000;
        out_k0  <= 16'h0000;
        out_k1  <= 16'h0000;
        out_y   <= 16'h0000;
        out_s   <= 32'h00000000;
    end else if (input_accepted) begin
        out_x0  <= x0_new[15:0];
        out_x1  <= x1_new[15:0];
        out_p00 <= p00_new[31:0];
        out_p01 <= p01_new[31:0];
        out_p10 <= p10_new[31:0];
        out_p11 <= p11_new[31:0];
        out_k0  <= k0[15:0];
        out_k1  <= k1[15:0];
        out_y   <= y[15:0];
        out_s   <= s_val[31:0];
        busy    <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule