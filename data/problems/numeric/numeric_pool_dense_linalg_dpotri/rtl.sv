module cholesky_inverse_3x3 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_uplo,
    input  logic [31:0]  in_a00,
    input  logic [31:0]  in_a01,
    input  logic [31:0]  in_a02,
    input  logic [31:0]  in_a10,
    input  logic [31:0]  in_a11,
    input  logic [31:0]  in_a12,
    input  logic [31:0]  in_a20,
    input  logic [31:0]  in_a21,
    input  logic [31:0]  in_a22,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_info,
    output logic [31:0]  out_a00,
    output logic [31:0]  out_a01,
    output logic [31:0]  out_a02,
    output logic [31:0]  out_a10,
    output logic [31:0]  out_a11,
    output logic [31:0]  out_a12,
    output logic [31:0]  out_a20,
    output logic [31:0]  out_a21,
    output logic [31:0]  out_a22
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [63:0] abs64(input logic signed [63:0] v);
    begin
        if (v < 0)
            abs64 = -v;
        else
            abs64 = v;
    end
endfunction

function automatic logic signed [63:0] trunc_div_toward_zero_64(
    input logic signed [63:0] n,
    input logic signed [63:0] d
);
    logic signed [63:0] an;
    logic signed [63:0] ad;
    logic signed [63:0] q;
    begin
        an = abs64(n);
        ad = abs64(d);
        q  = an / ad;
        if ((n < 0) ^ (d < 0))
            trunc_div_toward_zero_64 = -q;
        else
            trunc_div_toward_zero_64 = q;
    end
endfunction

function automatic logic signed [63:0] mul_q64(
    input logic signed [31:0] a,
    input logic signed [63:0] b
);
    logic signed [63:0] a64;
    logic signed [63:0] prod;
    begin
        a64  = a;
        prod = a64 * b;
        mul_q64 = trunc_div_toward_zero_64(prod, 64'sd65536);
    end
endfunction

function automatic logic signed [63:0] mul_q64_64(
    input logic signed [63:0] a,
    input logic signed [63:0] b
);
    logic signed [127:0] prod128;
    logic signed [127:0] aprod_abs;
    logic signed [127:0] q128;
    begin
        prod128 = a * b;
        if (prod128 < 0)
            aprod_abs = -prod128;
        else
            aprod_abs = prod128;
        q128 = aprod_abs / 128'sd65536;
        if ((a < 0) ^ (b < 0))
            mul_q64_64 = -$signed(q128[63:0]);
        else
            mul_q64_64 = $signed(q128[63:0]);
    end
endfunction

function automatic logic signed [63:0] div_q64(
    input logic signed [31:0] a,
    input logic signed [31:0] b
);
    logic signed [63:0] n;
    logic signed [63:0] d;
    begin
        n = $signed(a) <<< 16;
        d = b;
        div_q64 = trunc_div_toward_zero_64(n, d);
    end
endfunction

logic [31:0] out_info_r;
logic [31:0] out_a00_r;
logic [31:0] out_a01_r;
logic [31:0] out_a02_r;
logic [31:0] out_a10_r;
logic [31:0] out_a11_r;
logic [31:0] out_a12_r;
logic [31:0] out_a20_r;
logic [31:0] out_a21_r;
logic [31:0] out_a22_r;

assign out_info = out_info_r;
assign out_a00  = out_a00_r;
assign out_a01  = out_a01_r;
assign out_a02  = out_a02_r;
assign out_a10  = out_a10_r;
assign out_a11  = out_a11_r;
assign out_a12  = out_a12_r;
assign out_a20  = out_a20_r;
assign out_a21  = out_a21_r;
assign out_a22  = out_a22_r;

always_ff @(posedge clk) begin
    logic signed [31:0] u00, u01, u02, u11, u12, u22;
    logic signed [31:0] l00, l10, l11, l20, l21, l22;
    logic [31:0] info;
    logic signed [63:0] r22, r11, r00;
    logic signed [63:0] r12, r01, r02;
    logic signed [63:0] r21, r10, r20;
    logic signed [63:0] t0, t1;
    logic signed [63:0] c00, c01, c02, c11, c12, c22;

    if (rst) begin
        busy     <= 1'b0;
        out_info_r <= 32'd0;
        out_a00_r  <= 32'd0;
        out_a01_r  <= 32'd0;
        out_a02_r  <= 32'd0;
        out_a10_r  <= 32'd0;
        out_a11_r  <= 32'd0;
        out_a12_r  <= 32'd0;
        out_a20_r  <= 32'd0;
        out_a21_r  <= 32'd0;
        out_a22_r  <= 32'd0;
    end else if (input_accepted) begin
        info = 32'd0;
        r22 = 64'sd0; r11 = 64'sd0; r00 = 64'sd0;
        r12 = 64'sd0; r01 = 64'sd0; r02 = 64'sd0;
        r21 = 64'sd0; r10 = 64'sd0; r20 = 64'sd0;
        t0  = 64'sd0; t1  = 64'sd0;
        c00 = 64'sd0; c01 = 64'sd0; c02 = 64'sd0;
        c11 = 64'sd0; c12 = 64'sd0; c22 = 64'sd0;

        if (in_uplo) begin
            u00 = $signed(in_a00);
            u01 = $signed(in_a01);
            u02 = $signed(in_a02);
            u11 = $signed(in_a11);
            u12 = $signed(in_a12);
            u22 = $signed(in_a22);

            if (u00 == 32'sd0)
                info = 32'd1;
            else if (u11 == 32'sd0)
                info = 32'd2;
            else if (u22 == 32'sd0)
                info = 32'd3;
            else
                info = 32'd0;

            if (info != 32'd0) begin
                c00 = 64'sd0;
                c01 = 64'sd0;
                c02 = 64'sd0;
                c11 = 64'sd0;
                c12 = 64'sd0;
                c22 = 64'sd0;
            end else begin
                r22 = div_q64(32'sd65536, u22);
                r11 = div_q64(32'sd65536, u11);
                r00 = div_q64(32'sd65536, u00);
                r12 = -trunc_div_toward_zero_64(mul_q64(u12, r22) <<< 16, u11);
                r01 = -trunc_div_toward_zero_64(mul_q64(u01, r11) <<< 16, u00);
                t0  = mul_q64(u01, r12);
                t1  = mul_q64(u02, r22);
                r02 = -trunc_div_toward_zero_64((t0 + t1) <<< 16, u00);

                c00 = mul_q64_64(r00, r00) + mul_q64_64(r01, r01) + mul_q64_64(r02, r02);
                c01 = mul_q64_64(r01, r11) + mul_q64_64(r02, r12);
                c02 = mul_q64_64(r02, r22);
                c11 = mul_q64_64(r11, r11) + mul_q64_64(r12, r12);
                c12 = mul_q64_64(r12, r22);
                c22 = mul_q64_64(r22, r22);
            end
        end else begin
            l00 = $signed(in_a00);
            l10 = $signed(in_a10);
            l11 = $signed(in_a11);
            l20 = $signed(in_a20);
            l21 = $signed(in_a21);
            l22 = $signed(in_a22);

            if (l00 == 32'sd0)
                info = 32'd1;
            else if (l11 == 32'sd0)
                info = 32'd2;
            else if (l22 == 32'sd0)
                info = 32'd3;
            else
                info = 32'd0;

            if (info != 32'd0) begin
                c00 = 64'sd0;
                c01 = 64'sd0;
                c02 = 64'sd0;
                c11 = 64'sd0;
                c12 = 64'sd0;
                c22 = 64'sd0;
            end else begin
                r22 = div_q64(32'sd65536, l22);
                r11 = div_q64(32'sd65536, l11);
                r00 = div_q64(32'sd65536, l00);
                r21 = -trunc_div_toward_zero_64(mul_q64(l21, r22) <<< 16, l11);
                r10 = -trunc_div_toward_zero_64(mul_q64(l10, r11) <<< 16, l00);
                t0  = mul_q64(l21, r10);
                t1  = mul_q64(l20, r00);
                r20 = -trunc_div_toward_zero_64((t0 + t1) <<< 16, l22);

                c00 = mul_q64_64(r00, r00) + mul_q64_64(r10, r10) + mul_q64_64(r20, r20);
                c01 = mul_q64_64(r10, r11) + mul_q64_64(r20, r21);
                c02 = mul_q64_64(r20, r22);
                c11 = mul_q64_64(r11, r11) + mul_q64_64(r21, r21);
                c12 = mul_q64_64(r21, r22);
                c22 = mul_q64_64(r22, r22);
            end
        end

        out_info_r <= info;
        out_a00_r  <= c00[31:0];
        out_a01_r  <= c01[31:0];
        out_a02_r  <= c02[31:0];
        out_a10_r  <= c01[31:0];
        out_a11_r  <= c11[31:0];
        out_a12_r  <= c12[31:0];
        out_a20_r  <= c02[31:0];
        out_a21_r  <= c12[31:0];
        out_a22_r  <= c22[31:0];
        busy <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
        out_info_r <= 32'd0;
        out_a00_r  <= 32'd0;
        out_a01_r  <= 32'd0;
        out_a02_r  <= 32'd0;
        out_a10_r  <= 32'd0;
        out_a11_r  <= 32'd0;
        out_a12_r  <= 32'd0;
        out_a20_r  <= 32'd0;
        out_a21_r  <= 32'd0;
        out_a22_r  <= 32'd0;
    end
end

endmodule