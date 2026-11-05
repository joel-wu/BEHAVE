module triangular_solve_2x2 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_side,
    input  logic         in_uplo,
    input  logic         in_transa,
    input  logic         in_diag,
    input  logic [31:0]  in_alpha,

    input  logic [31:0]  in_a00,
    input  logic [31:0]  in_a01,
    input  logic [31:0]  in_a10,
    input  logic [31:0]  in_a11,

    input  logic [31:0]  in_b00,
    input  logic [31:0]  in_b01,
    input  logic [31:0]  in_b10,
    input  logic [31:0]  in_b11,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_b00,
    output logic [31:0]  out_b01,
    output logic [31:0]  out_b10,
    output logic [31:0]  out_b11
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;
assign in_ready        = ~busy | output_consumed;
assign out_valid       = busy;

function automatic logic signed [31:0] to_signed32(input logic [31:0] x);
    begin
        to_signed32 = $signed(x);
    end
endfunction

function automatic logic signed [31:0] wrap32(input logic signed [63:0] x);
    begin
        wrap32 = x[31:0];
    end
endfunction

function automatic logic signed [31:0] mul_q16(
    input logic signed [31:0] x,
    input logic signed [31:0] y
);
    logic signed [63:0] p;
    logic signed [63:0] negp;
    logic signed [63:0] q64;
    begin
        p = x * y;
        if (p >= 0) begin
            q64 = p >>> 16;
        end else begin
            negp = -p;
            q64 = -(negp >>> 16);
        end
        mul_q16 = q64[31:0];
    end
endfunction

function automatic logic signed [31:0] div_q16(
    input logic signed [31:0] x,
    input logic signed [31:0] y
);
    logic signed [63:0] n;
    logic signed [63:0] an;
    logic signed [31:0] ay;
    logic [63:0] q_u;
    logic same_sign;
    logic signed [63:0] q64;
    begin
        if (y == 0) begin
            div_q16 = 32'sd0;
        end else begin
            n = x <<< 16;
            if (n >= 0)
                an = n;
            else
                an = -n;

            if (y >= 0)
                ay = y;
            else
                ay = -y;

            q_u = an / ay;
            same_sign = ((n >= 0) == (y >= 0));

            if (same_sign)
                q64 = $signed(q_u);
            else
                q64 = -$signed(q_u);

            div_q16 = q64[31:0];
        end
    end
endfunction

logic        calc_side;
logic        calc_uplo;
logic        calc_transa;
logic        calc_diag;
logic [31:0] calc_alpha;
logic [31:0] calc_a00;
logic [31:0] calc_a01;
logic [31:0] calc_a10;
logic [31:0] calc_a11;
logic [31:0] calc_b00_in;
logic [31:0] calc_b01_in;
logic [31:0] calc_b10_in;
logic [31:0] calc_b11_in;

logic signed [31:0] alpha_s;
logic signed [31:0] a00_s;
logic signed [31:0] a01_s;
logic signed [31:0] a10_s;
logic signed [31:0] a11_s;
logic signed [31:0] b00_s;
logic signed [31:0] b01_s;
logic signed [31:0] b10_s;
logic signed [31:0] b11_s;
logic signed [31:0] temp_s;
logic signed [31:0] temp0_s;
logic signed [31:0] temp1_s;

always_comb begin
    calc_side    = in_side;
    calc_uplo    = in_uplo;
    calc_transa  = in_transa;
    calc_diag    = in_diag;
    calc_alpha   = in_alpha;
    calc_a00     = in_a00;
    calc_a01     = in_a01;
    calc_a10     = in_a10;
    calc_a11     = in_a11;
    calc_b00_in  = in_b00;
    calc_b01_in  = in_b01;
    calc_b10_in  = in_b10;
    calc_b11_in  = in_b11;

    alpha_s = to_signed32(calc_alpha);
    a00_s   = to_signed32(calc_a00);
    a01_s   = to_signed32(calc_a01);
    a10_s   = to_signed32(calc_a10);
    a11_s   = to_signed32(calc_a11);
    b00_s   = to_signed32(calc_b00_in);
    b01_s   = to_signed32(calc_b01_in);
    b10_s   = to_signed32(calc_b10_in);
    b11_s   = to_signed32(calc_b11_in);
    temp_s  = 32'sd0;
    temp0_s = 32'sd0;
    temp1_s = 32'sd0;

    if (alpha_s == 0) begin
        b00_s = 32'sd0;
        b01_s = 32'sd0;
        b10_s = 32'sd0;
        b11_s = 32'sd0;
    end else if (calc_side == 1'b0) begin
        if (calc_transa == 1'b0) begin
            if (calc_uplo == 1'b0) begin
                if (alpha_s != 32'sh00010000) begin
                    b00_s = mul_q16(alpha_s, b00_s);
                    b10_s = mul_q16(alpha_s, b10_s);
                end
                if (b10_s != 0) begin
                    if (calc_diag == 1'b0) begin
                        b10_s = div_q16(b10_s, a11_s);
                    end
                    b00_s = wrap32($signed(b00_s) - $signed(mul_q16(b10_s, a01_s)));
                end
                if ((b00_s != 0) && (calc_diag == 1'b0)) begin
                    b00_s = div_q16(b00_s, a00_s);
                end

                if (alpha_s != 32'sh00010000) begin
                    b01_s = mul_q16(alpha_s, b01_s);
                    b11_s = mul_q16(alpha_s, b11_s);
                end
                if (b11_s != 0) begin
                    if (calc_diag == 1'b0) begin
                        b11_s = div_q16(b11_s, a11_s);
                    end
                    b01_s = wrap32($signed(b01_s) - $signed(mul_q16(b11_s, a01_s)));
                end
                if ((b01_s != 0) && (calc_diag == 1'b0)) begin
                    b01_s = div_q16(b01_s, a00_s);
                end
            end else begin
                if (alpha_s != 32'sh00010000) begin
                    b00_s = mul_q16(alpha_s, b00_s);
                    b10_s = mul_q16(alpha_s, b10_s);
                end
                if (b00_s != 0) begin
                    if (calc_diag == 1'b0) begin
                        b00_s = div_q16(b00_s, a00_s);
                    end
                    b10_s = wrap32($signed(b10_s) - $signed(mul_q16(b00_s, a10_s)));
                end
                if ((b10_s != 0) && (calc_diag == 1'b0)) begin
                    b10_s = div_q16(b10_s, a11_s);
                end

                if (alpha_s != 32'sh00010000) begin
                    b01_s = mul_q16(alpha_s, b01_s);
                    b11_s = mul_q16(alpha_s, b11_s);
                end
                if (b01_s != 0) begin
                    if (calc_diag == 1'b0) begin
                        b01_s = div_q16(b01_s, a00_s);
                    end
                    b11_s = wrap32($signed(b11_s) - $signed(mul_q16(b01_s, a10_s)));
                end
                if ((b11_s != 0) && (calc_diag == 1'b0)) begin
                    b11_s = div_q16(b11_s, a11_s);
                end
            end
        end else begin
            if (calc_uplo == 1'b0) begin
                temp0_s = mul_q16(alpha_s, b00_s);
                if (calc_diag == 1'b0) begin
                    temp0_s = div_q16(temp0_s, a00_s);
                end
                b00_s = temp0_s;
                temp1_s = wrap32($signed(mul_q16(alpha_s, b10_s)) - $signed(mul_q16(a01_s, b00_s)));
                if (calc_diag == 1'b0) begin
                    temp1_s = div_q16(temp1_s, a11_s);
                end
                b10_s = temp1_s;

                temp0_s = mul_q16(alpha_s, b01_s);
                if (calc_diag == 1'b0) begin
                    temp0_s = div_q16(temp0_s, a00_s);
                end
                b01_s = temp0_s;
                temp1_s = wrap32($signed(mul_q16(alpha_s, b11_s)) - $signed(mul_q16(a01_s, b01_s)));
                if (calc_diag == 1'b0) begin
                    temp1_s = div_q16(temp1_s, a11_s);
                end
                b11_s = temp1_s;
            end else begin
                temp1_s = mul_q16(alpha_s, b10_s);
                if (calc_diag == 1'b0) begin
                    temp1_s = div_q16(temp1_s, a11_s);
                end
                b10_s = temp1_s;
                temp0_s = wrap32($signed(mul_q16(alpha_s, b00_s)) - $signed(mul_q16(a10_s, b10_s)));
                if (calc_diag == 1'b0) begin
                    temp0_s = div_q16(temp0_s, a00_s);
                end
                b00_s = temp0_s;

                temp1_s = mul_q16(alpha_s, b11_s);
                if (calc_diag == 1'b0) begin
                    temp1_s = div_q16(temp1_s, a11_s);
                end
                b11_s = temp1_s;
                temp0_s = wrap32($signed(mul_q16(alpha_s, b01_s)) - $signed(mul_q16(a10_s, b11_s)));
                if (calc_diag == 1'b0) begin
                    temp0_s = div_q16(temp0_s, a00_s);
                end
                b01_s = temp0_s;
            end
        end
    end else begin
        if (calc_transa == 1'b0) begin
            if (calc_uplo == 1'b0) begin
                if (alpha_s != 32'sh00010000) begin
                    b00_s = mul_q16(alpha_s, b00_s);
                    b10_s = mul_q16(alpha_s, b10_s);
                end
                if (calc_diag == 1'b0) begin
                    temp_s = div_q16(32'sh00010000, a00_s);
                    b00_s = mul_q16(temp_s, b00_s);
                    b10_s = mul_q16(temp_s, b10_s);
                end

                if (alpha_s != 32'sh00010000) begin
                    b01_s = mul_q16(alpha_s, b01_s);
                    b11_s = mul_q16(alpha_s, b11_s);
                end
                if (a01_s != 0) begin
                    b01_s = wrap32($signed(b01_s) - $signed(mul_q16(a01_s, b00_s)));
                    b11_s = wrap32($signed(b11_s) - $signed(mul_q16(a01_s, b10_s)));
                end
                if (calc_diag == 1'b0) begin
                    temp_s = div_q16(32'sh00010000, a11_s);
                    b01_s = mul_q16(temp_s, b01_s);
                    b11_s = mul_q16(temp_s, b11_s);
                end
            end else begin
                if (alpha_s != 32'sh00010000) begin
                    b01_s = mul_q16(alpha_s, b01_s);
                    b11_s = mul_q16(alpha_s, b11_s);
                end
                if (calc_diag == 1'b0) begin
                    temp_s = div_q16(32'sh00010000, a11_s);
                    b01_s = mul_q16(temp_s, b01_s);
                    b11_s = mul_q16(temp_s, b11_s);
                end

                if (alpha_s != 32'sh00010000) begin
                    b00_s = mul_q16(alpha_s, b00_s);
                    b10_s = mul_q16(alpha_s, b10_s);
                end
                if (a10_s != 0) begin
                    b00_s = wrap32($signed(b00_s) - $signed(mul_q16(a10_s, b01_s)));
                    b10_s = wrap32($signed(b10_s) - $signed(mul_q16(a10_s, b11_s)));
                end
                if (calc_diag == 1'b0) begin
                    temp_s = div_q16(32'sh00010000, a00_s);
                    b00_s = mul_q16(temp_s, b00_s);
                    b10_s = mul_q16(temp_s, b10_s);
                end
            end
        end else begin
            if (calc_uplo == 1'b0) begin
                if (calc_diag == 1'b0) begin
                    temp_s = div_q16(32'sh00010000, a11_s);
                    b01_s = mul_q16(temp_s, b01_s);
                    b11_s = mul_q16(temp_s, b11_s);
                end
                if (a01_s != 0) begin
                    b00_s = wrap32($signed(b00_s) - $signed(mul_q16(a01_s, b01_s)));
                    b10_s = wrap32($signed(b10_s) - $signed(mul_q16(a01_s, b11_s)));
                end
                if (alpha_s != 32'sh00010000) begin
                    b01_s = mul_q16(alpha_s, b01_s);
                    b11_s = mul_q16(alpha_s, b11_s);
                end

                if (calc_diag == 1'b0) begin
                    temp_s = div_q16(32'sh00010000, a00_s);
                    b00_s = mul_q16(temp_s, b00_s);
                    b10_s = mul_q16(temp_s, b10_s);
                end
                if (alpha_s != 32'sh00010000) begin
                    b00_s = mul_q16(alpha_s, b00_s);
                    b10_s = mul_q16(alpha_s, b10_s);
                end
            end else begin
                if (calc_diag == 1'b0) begin
                    temp_s = div_q16(32'sh00010000, a00_s);
                    b00_s = mul_q16(temp_s, b00_s);
                    b10_s = mul_q16(temp_s, b10_s);
                end
                if (a10_s != 0) begin
                    b01_s = wrap32($signed(b01_s) - $signed(mul_q16(a10_s, b00_s)));
                    b11_s = wrap32($signed(b11_s) - $signed(mul_q16(a10_s, b10_s)));
                end
                if (alpha_s != 32'sh00010000) begin
                    b00_s = mul_q16(alpha_s, b00_s);
                    b10_s = mul_q16(alpha_s, b10_s);
                end

                if (calc_diag == 1'b0) begin
                    temp_s = div_q16(32'sh00010000, a11_s);
                    b01_s = mul_q16(temp_s, b01_s);
                    b11_s = mul_q16(temp_s, b11_s);
                end
                if (alpha_s != 32'sh00010000) begin
                    b01_s = mul_q16(alpha_s, b01_s);
                    b11_s = mul_q16(alpha_s, b11_s);
                end
            end
        end
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy    <= 1'b0;
        out_b00 <= 32'd0;
        out_b01 <= 32'd0;
        out_b10 <= 32'd0;
        out_b11 <= 32'd0;
    end else if (input_accepted) begin
        busy    <= 1'b1;
        out_b00 <= b00_s[31:0];
        out_b01 <= b01_s[31:0];
        out_b10 <= b10_s[31:0];
        out_b11 <= b11_s[31:0];
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule