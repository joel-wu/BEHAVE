module biquad_iir_tdf2_q15 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x,
    input  logic [15:0] in_a0,
    input  logic [15:0] in_a1,
    input  logic [15:0] in_a2,
    input  logic [15:0] in_b1,
    input  logic [15:0] in_b2,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_y
);

logic busy;
logic signed [15:0] z1;
logic signed [15:0] z2;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [15:0] sat_q15(input logic signed [31:0] v);
    begin
        if (v > 32'sd32767)
            sat_q15 = 16'sd32767;
        else if (v < -32'sd32768)
            sat_q15 = -16'sd32768;
        else
            sat_q15 = v[15:0];
    end
endfunction

function automatic logic signed [15:0] q15_mul(
    input logic signed [15:0] a,
    input logic signed [15:0] b
);
    logic signed [31:0] prod_q30;
    logic signed [31:0] prod_q15;
    begin
        prod_q30 = a * b;
        prod_q15 = prod_q30 >>> 15;
        q15_mul = sat_q15(prod_q15);
    end
endfunction

logic signed [15:0] x_s;
logic signed [15:0] a0_s;
logic signed [15:0] a1_s;
logic signed [15:0] a2_s;
logic signed [15:0] b1_s;
logic signed [15:0] b2_s;

logic signed [15:0] p0;
logic signed [15:0] p1;
logic signed [15:0] p2;
logic signed [15:0] out_y_signed;
logic signed [15:0] f1;
logic signed [15:0] f2;
logic signed [31:0] out_sum;
logic signed [31:0] z1_sum;
logic signed [31:0] z2_sum;
logic signed [15:0] z1_next;
logic signed [15:0] z2_next;

always_comb begin
    x_s = $signed(in_x);
    a0_s = $signed(in_a0);
    a1_s = $signed(in_a1);
    a2_s = $signed(in_a2);
    b1_s = $signed(in_b1);
    b2_s = $signed(in_b2);

    p0 = q15_mul(x_s, a0_s);
    out_sum = $signed(p0) + $signed(z1);
    out_y_signed = sat_q15(out_sum);

    p1 = q15_mul(x_s, a1_s);
    p2 = q15_mul(x_s, a2_s);
    f1 = q15_mul(b1_s, out_y_signed);
    f2 = q15_mul(b2_s, out_y_signed);

    z1_sum = $signed(p1) + $signed(z2) - $signed(f1);
    z2_sum = $signed(p2) - $signed(f2);

    z1_next = sat_q15(z1_sum);
    z2_next = sat_q15(z2_sum);
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy  <= 1'b0;
        z1    <= 16'sd0;
        z2    <= 16'sd0;
        out_y <= 16'h0000;
    end else if (input_accepted) begin
        out_y <= out_y_signed[15:0];
        z1    <= z1_next;
        z2    <= z2_next;
        busy  <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
