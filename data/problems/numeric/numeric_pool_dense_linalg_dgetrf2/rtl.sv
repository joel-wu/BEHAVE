module lu_factor2x2 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a00,
    input  logic [31:0] in_a01,
    input  logic [31:0] in_a10,
    input  logic [31:0] in_a11,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_u00,
    output logic [31:0] out_u01,
    output logic [31:0] out_l10,
    output logic [31:0] out_u11,
    output logic [0:0]  out_pivot_row0,
    output logic [1:0]  out_info
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [31:0] abs_s32(input logic signed [31:0] x);
    begin
        if (x < 0)
            abs_s32 = ~x + 32'd1;
        else
            abs_s32 = x[31:0];
    end
endfunction

function automatic logic signed [31:0] ratio_q16(
    input logic signed [31:0] n, d
);
    logic signed [63:0] numerator;
    logic signed [63:0] denominator;
    begin
        numerator = {{32{n[31]}}, n};
        denominator = {{32{d[31]}}, d};
        ratio_q16 = (numerator <<< 16) / denominator;
    end
endfunction

always_ff @(posedge clk or posedge rst) begin
    logic signed [31:0] a00_s;
    logic signed [31:0] a01_s;
    logic signed [31:0] a10_s;
    logic signed [31:0] a11_s;
    logic [31:0] abs_a00;
    logic [31:0] abs_a10;
    logic signed [31:0] p00_s;
    logic signed [31:0] p01_s;
    logic signed [31:0] p10_s;
    logic signed [31:0] p11_s;
    logic signed [31:0] l10_s;
    logic signed [63:0] prod_s;
    logic signed [63:0] u11_wide_s;
    if (rst) begin
        busy           <= 1'b0;
        out_u00        <= 32'h00000000;
        out_u01        <= 32'h00000000;
        out_l10        <= 32'h00000000;
        out_u11        <= 32'h00000000;
        out_pivot_row0 <= 1'b0;
        out_info       <= 2'b00;
    end else if (input_accepted) begin
        a00_s = $signed(in_a00);
        a01_s = $signed(in_a01);
        a10_s = $signed(in_a10);
        a11_s = $signed(in_a11);

        abs_a00 = abs_s32(a00_s);
        abs_a10 = abs_s32(a10_s);

        if (abs_a00 >= abs_a10) begin
            out_pivot_row0 <= 1'b0;
            p00_s = a00_s;
            p01_s = a01_s;
            p10_s = a10_s;
            p11_s = a11_s;
        end else begin
            out_pivot_row0 <= 1'b1;
            p00_s = a10_s;
            p01_s = a11_s;
            p10_s = a00_s;
            p11_s = a01_s;
        end

        if (p00_s == 32'sd0) begin
            out_u00  <= p00_s[31:0];
            out_u01  <= p01_s[31:0];
            out_l10  <= p10_s[31:0];
            out_u11  <= p11_s[31:0];
            out_info <= 2'd1;
        end else begin
            l10_s      = ratio_q16(p10_s, p00_s);
            prod_s     = l10_s * p01_s;
            // Keep both operands signed: an unsigned concatenation would
            // turn the arithmetic shift into a logical shift in this context.
            u11_wide_s = $signed({{32{p11_s[31]}}, p11_s}) - (prod_s >>> 16);

            out_u00 <= p00_s[31:0];
            out_u01 <= p01_s[31:0];
            out_l10 <= l10_s[31:0];
            out_u11 <= u11_wide_s[31:0];

            if (u11_wide_s == 64'sd0)
                out_info <= 2'd2;
            else
                out_info <= 2'd0;
        end

        busy <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
