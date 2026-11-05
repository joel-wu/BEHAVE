module absmin_no_idx_f32_16 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,
    input  logic [31:0] in_x4,
    input  logic [31:0] in_x5,
    input  logic [31:0] in_x6,
    input  logic [31:0] in_x7,
    input  logic [31:0] in_x8,
    input  logic [31:0] in_x9,
    input  logic [31:0] in_x10,
    input  logic [31:0] in_x11,
    input  logic [31:0] in_x12,
    input  logic [31:0] in_x13,
    input  logic [31:0] in_x14,
    input  logic [31:0] in_x15,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_result
);

logic busy;
logic [31:0] result_next;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [31:0] fp32_abs_bits(input logic [31:0] x);
    fp32_abs_bits = {1'b0, x[30:0]};
endfunction

function automatic logic fp32_is_nan_bits(input logic [31:0] x);
    logic exp_all_ones;
    logic frac_nonzero;
    begin
        exp_all_ones = (x[30:23] == 8'hFF);
        frac_nonzero = (x[22:0] != 23'h0);
        fp32_is_nan_bits = exp_all_ones & frac_nonzero;
    end
endfunction

function automatic logic fp32_lt_ordered(input logic [31:0] a, input logic [31:0] b);
    logic a_nan;
    logic b_nan;
    logic [7:0] a_exp;
    logic [7:0] b_exp;
    logic [22:0] a_frac;
    logic [22:0] b_frac;
    begin
        a_nan  = fp32_is_nan_bits(a);
        b_nan  = fp32_is_nan_bits(b);
        a_exp  = a[30:23];
        b_exp  = b[30:23];
        a_frac = a[22:0];
        b_frac = b[22:0];

        if (a_nan || b_nan) begin
            fp32_lt_ordered = 1'b0;
        end else if (a_exp < b_exp) begin
            fp32_lt_ordered = 1'b1;
        end else if (a_exp > b_exp) begin
            fp32_lt_ordered = 1'b0;
        end else if (a_frac < b_frac) begin
            fp32_lt_ordered = 1'b1;
        end else begin
            fp32_lt_ordered = 1'b0;
        end
    end
endfunction

always_comb begin
    logic [31:0] current;
    logic [31:0] candidate;

    current = fp32_abs_bits(in_x0);

    candidate = fp32_abs_bits(in_x1);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    candidate = fp32_abs_bits(in_x2);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    candidate = fp32_abs_bits(in_x3);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    candidate = fp32_abs_bits(in_x4);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    candidate = fp32_abs_bits(in_x5);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    candidate = fp32_abs_bits(in_x6);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    candidate = fp32_abs_bits(in_x7);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    candidate = fp32_abs_bits(in_x8);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    candidate = fp32_abs_bits(in_x9);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    candidate = fp32_abs_bits(in_x10);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    candidate = fp32_abs_bits(in_x11);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    candidate = fp32_abs_bits(in_x12);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    candidate = fp32_abs_bits(in_x13);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    candidate = fp32_abs_bits(in_x14);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    candidate = fp32_abs_bits(in_x15);
    if (fp32_lt_ordered(candidate, current)) current = candidate;

    result_next = current;
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_result <= 32'h00000000;
    end else if (input_accepted) begin
        busy       <= 1'b1;
        out_result <= result_next;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
