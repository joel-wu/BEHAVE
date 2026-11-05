module fp32_clip4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,
    input  logic [31:0] in_low,
    input  logic [31:0] in_high,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3
);

logic busy;
wire  input_accepted;
wire  output_consumed;

logic [31:0] clip_y0;
logic [31:0] clip_y1;
logic [31:0] clip_y2;
logic [31:0] clip_y3;

function automatic logic fp32_is_nan(input logic [31:0] a);
    logic [7:0] exp_field;
    logic [22:0] frac_field;
    begin
        exp_field  = a[30:23];
        frac_field = a[22:0];
        fp32_is_nan = (exp_field == 8'hFF) && (frac_field != 23'd0);
    end
endfunction

function automatic logic fp32_lt(input logic [31:0] a, input logic [31:0] b);
    logic sign_a;
    logic sign_b;
    logic [30:0] mag_a;
    logic [30:0] mag_b;
    logic a_is_zero;
    logic b_is_zero;
    begin
        sign_a = a[31];
        sign_b = b[31];
        mag_a  = a[30:0];
        mag_b  = b[30:0];

        a_is_zero = (mag_a == 31'd0);
        b_is_zero = (mag_b == 31'd0);

        fp32_lt = 1'b0;
        if (a_is_zero && b_is_zero) begin
            fp32_lt = 1'b0;
        end else if (sign_a != sign_b) begin
            fp32_lt = sign_a;
        end else if (sign_a == 1'b0) begin
            fp32_lt = (mag_a < mag_b);
        end else begin
            fp32_lt = (mag_a > mag_b);
        end
    end
endfunction

function automatic logic fp32_gt(input logic [31:0] a, input logic [31:0] b);
    logic sign_a;
    logic sign_b;
    logic [30:0] mag_a;
    logic [30:0] mag_b;
    logic a_is_zero;
    logic b_is_zero;
    begin
        sign_a = a[31];
        sign_b = b[31];
        mag_a  = a[30:0];
        mag_b  = b[30:0];

        a_is_zero = (mag_a == 31'd0);
        b_is_zero = (mag_b == 31'd0);

        fp32_gt = 1'b0;
        if (a_is_zero && b_is_zero) begin
            fp32_gt = 1'b0;
        end else if (sign_a != sign_b) begin
            fp32_gt = sign_b;
        end else if (sign_a == 1'b0) begin
            fp32_gt = (mag_a > mag_b);
        end else begin
            fp32_gt = (mag_a < mag_b);
        end
    end
endfunction

function automatic logic [31:0] clip_one(
    input logic [31:0] x,
    input logic [31:0] low,
    input logic [31:0] high
);
    logic x_nan;
    logic low_nan;
    logic high_nan;
    begin
        x_nan    = fp32_is_nan(x);
        low_nan  = fp32_is_nan(low);
        high_nan = fp32_is_nan(high);

        clip_one = x;
        if ((!x_nan) && (!high_nan) && fp32_gt(x, high)) begin
            clip_one = high;
        end else if ((!x_nan) && (!low_nan) && fp32_lt(x, low)) begin
            clip_one = low;
        end
    end
endfunction

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    clip_y0 = clip_one(in_x0, in_low, in_high);
    clip_y1 = clip_one(in_x1, in_low, in_high);
    clip_y2 = clip_one(in_x2, in_low, in_high);
    clip_y3 = clip_one(in_x3, in_low, in_high);
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy   <= 1'b0;
        out_y0 <= 32'd0;
        out_y1 <= 32'd0;
        out_y2 <= 32'd0;
        out_y3 <= 32'd0;
    end else if (input_accepted) begin
        busy   <= 1'b1;
        out_y0 <= clip_y0;
        out_y1 <= clip_y1;
        out_y2 <= clip_y2;
        out_y3 <= clip_y3;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
