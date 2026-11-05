module control_kalman_kalman_1d_gain_update (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_p,
    input  logic [31:0]  in_h,
    input  logic [31:0]  in_r,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_s,
    output logic [31:0]  out_si,
    output logic [31:0]  out_k
);

function automatic logic signed [31:0] sat32_from_65(input logic signed [64:0] v);
    begin
        if (v[64] == 1'b0) begin
            if (|v[63:31])
                sat32_from_65 = 32'sd2147483647;
            else
                sat32_from_65 = v[31:0];
        end else begin
            if (~&v[63:31])
                sat32_from_65 = -32'sd2147483648;
            else
                sat32_from_65 = v[31:0];
        end
    end
endfunction

function automatic logic signed [31:0] sat32_from_asr31_64(input logic signed [63:0] v);
    logic signed [63:0] shifted;
    begin
        shifted = v >>> 31;
        if (shifted[63] == 1'b0) begin
            if (|shifted[62:31])
                sat32_from_asr31_64 = 32'sd2147483647;
            else
                sat32_from_asr31_64 = shifted[31:0];
        end else begin
            sat32_from_asr31_64 = shifted[31:0];
        end
    end
endfunction

function automatic logic signed [31:0] sdiv64_by_32_trunc_zero_sat32(
    input logic signed [63:0] n,
    input logic signed [31:0] d
);
    logic        neg;
    logic [63:0] an;
    logic [31:0] ad;
    logic [63:0] qmag;
    logic signed [64:0] qs;
    begin
        if (d == 32'sd0) begin
            sdiv64_by_32_trunc_zero_sat32 = 32'sd0;
        end else begin
            neg = (n[63] ^ d[31]);

            if (n[63])
                an = (~n) + 64'd1;
            else
                an = n;

            if (d[31])
                ad = (~d) + 32'd1;
            else
                ad = d;

            qmag = an / ad;

            if (neg)
                qs = -$signed({1'b0, qmag});
            else
                qs =  $signed({1'b0, qmag});

            sdiv64_by_32_trunc_zero_sat32 = sat32_from_65(qs);
        end
    end
endfunction

logic busy;

wire input_accepted;
wire output_consumed;

logic signed [31:0] p_s;
logic signed [31:0] h_s;
logic signed [31:0] r_s;

logic signed [63:0] prod_hp;
logic signed [31:0] hp_q31;

logic signed [63:0] prod_hph;
logic signed [31:0] hph_q31;

logic signed [32:0] s_ext;
logic signed [31:0] s_val;

logic signed [63:0] si_num;
logic signed [63:0] k_num;
logic signed [31:0] si_val;
logic signed [31:0] k_val;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = rst ? 1'b0 : ~busy;
assign out_valid = busy;

always_comb begin
    p_s = $signed(in_p);
    h_s = $signed(in_h);
    r_s = $signed(in_r);

    prod_hp = h_s * p_s;
    hp_q31  = sat32_from_asr31_64(prod_hp);

    prod_hph = h_s * hp_q31;
    hph_q31  = sat32_from_asr31_64(prod_hph);

    s_ext = $signed({hph_q31[31], hph_q31}) + $signed({r_s[31], r_s});
    s_val = sat32_from_65(s_ext);

    si_num = 64'sh4000000000000000;
    k_num  = $signed(hp_q31) <<< 31;

    if (s_val == 32'sd0) begin
        si_val = 32'sd0;
        k_val  = 32'sd0;
    end else begin
        si_val = sdiv64_by_32_trunc_zero_sat32(si_num, s_val);
        k_val  = sdiv64_by_32_trunc_zero_sat32(k_num,  s_val);
    end
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy   <= 1'b0;
        out_s  <= 32'd0;
        out_si <= 32'd0;
        out_k  <= 32'd0;
    end else if (input_accepted) begin
        busy   <= 1'b1;
        out_s  <= $unsigned(s_val);
        out_si <= $unsigned(si_val);
        out_k  <= $unsigned(k_val);
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
