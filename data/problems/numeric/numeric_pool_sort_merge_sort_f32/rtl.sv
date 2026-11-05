module merge_sort8_f32 (
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
    input  logic        in_dir,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3,
    output logic [31:0] out_y4,
    output logic [31:0] out_y5,
    output logic [31:0] out_y6,
    output logic [31:0] out_y7
);

logic busy;
wire input_accepted;
wire output_consumed;

logic [31:0] sort_y0;
logic [31:0] sort_y1;
logic [31:0] sort_y2;
logic [31:0] sort_y3;
logic [31:0] sort_y4;
logic [31:0] sort_y5;
logic [31:0] sort_y6;
logic [31:0] sort_y7;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic fp32_le(input logic [31:0] a, input logic [31:0] b);
    logic [7:0]  a_exp;
    logic [22:0] a_frac;
    logic        a_sign;
    logic [7:0]  b_exp;
    logic [22:0] b_frac;
    logic        b_sign;
    logic        a_nan;
    logic        b_nan;
    logic        a_zero;
    logic        b_zero;
    begin
        a_exp  = a[30:23];
        a_frac = a[22:0];
        a_sign = a[31];
        b_exp  = b[30:23];
        b_frac = b[22:0];
        b_sign = b[31];
        a_nan  = (a_exp == 8'hFF) && (a_frac != 23'd0);
        b_nan  = (b_exp == 8'hFF) && (b_frac != 23'd0);
        a_zero = (a[30:0] == 31'd0);
        b_zero = (b[30:0] == 31'd0);

        if (a_nan || b_nan) begin
            fp32_le = 1'b0;
        end else if (a_zero && b_zero) begin
            fp32_le = 1'b1;
        end else if (a_sign != b_sign) begin
            fp32_le = a_sign;
        end else if (a_sign == 1'b0) begin
            fp32_le = (a[30:0] <= b[30:0]);
        end else begin
            fp32_le = (a[30:0] >= b[30:0]);
        end
    end
endfunction

function automatic logic take_left(
    input logic [31:0] left,
    input logic [31:0] right,
    input logic        direction
);
    begin
        take_left = (direction == fp32_le(left, right));
    end
endfunction

task automatic sort2(
    input  logic [31:0] a0,
    input  logic [31:0] a1,
    input  logic        direction,
    output logic [31:0] y0,
    output logic [31:0] y1
);
    begin
        if (take_left(a0, a1, direction)) begin
            y0 = a0;
            y1 = a1;
        end else begin
            y0 = a1;
            y1 = a0;
        end
    end
endtask

task automatic merge2x2(
    input  logic [31:0] s0,
    input  logic [31:0] s1,
    input  logic [31:0] s2,
    input  logic [31:0] s3,
    input  logic        direction,
    output logic [31:0] d0,
    output logic [31:0] d1,
    output logic [31:0] d2,
    output logic [31:0] d3
);
    logic [1:0] left_idx;
    logic [1:0] right_idx;
    logic [31:0] left_val;
    logic [31:0] right_val;
    begin
        left_idx = 2'd0;
        right_idx = 2'd0;

        left_val  = (left_idx == 2'd0) ? s0 : s1;
        right_val = (right_idx == 2'd0) ? s2 : s3;
        if ((left_idx < 2) && ((right_idx >= 2) || take_left(left_val, right_val, direction))) begin
            d0 = left_val;
            left_idx = left_idx + 2'd1;
        end else begin
            d0 = right_val;
            right_idx = right_idx + 2'd1;
        end

        left_val  = (left_idx == 2'd0) ? s0 : s1;
        right_val = (right_idx == 2'd0) ? s2 : s3;
        if ((left_idx < 2) && ((right_idx >= 2) || take_left(left_val, right_val, direction))) begin
            d1 = left_val;
            left_idx = left_idx + 2'd1;
        end else begin
            d1 = right_val;
            right_idx = right_idx + 2'd1;
        end

        left_val  = (left_idx == 2'd0) ? s0 : s1;
        right_val = (right_idx == 2'd0) ? s2 : s3;
        if ((left_idx < 2) && ((right_idx >= 2) || take_left(left_val, right_val, direction))) begin
            d2 = left_val;
            left_idx = left_idx + 2'd1;
        end else begin
            d2 = right_val;
            right_idx = right_idx + 2'd1;
        end

        left_val  = (left_idx == 2'd0) ? s0 : s1;
        right_val = (right_idx == 2'd0) ? s2 : s3;
        if ((left_idx < 2) && ((right_idx >= 2) || take_left(left_val, right_val, direction))) begin
            d3 = left_val;
            left_idx = left_idx + 2'd1;
        end else begin
            d3 = right_val;
            right_idx = right_idx + 2'd1;
        end
    end
endtask

task automatic merge4x4(
    input  logic [31:0] s0,
    input  logic [31:0] s1,
    input  logic [31:0] s2,
    input  logic [31:0] s3,
    input  logic [31:0] s4,
    input  logic [31:0] s5,
    input  logic [31:0] s6,
    input  logic [31:0] s7,
    input  logic        direction,
    output logic [31:0] d0,
    output logic [31:0] d1,
    output logic [31:0] d2,
    output logic [31:0] d3,
    output logic [31:0] d4,
    output logic [31:0] d5,
    output logic [31:0] d6,
    output logic [31:0] d7
);
    logic [2:0] left_idx;
    logic [2:0] right_idx;
    logic [31:0] left_val;
    logic [31:0] right_val;
    begin
        left_idx = 3'd0;
        right_idx = 3'd0;

        left_val  = (left_idx == 3'd0) ? s0 : (left_idx == 3'd1) ? s1 : (left_idx == 3'd2) ? s2 : s3;
        right_val = (right_idx == 3'd0) ? s4 : (right_idx == 3'd1) ? s5 : (right_idx == 3'd2) ? s6 : s7;
        if ((left_idx < 4) && ((right_idx >= 4) || take_left(left_val, right_val, direction))) begin
            d0 = left_val;
            left_idx = left_idx + 3'd1;
        end else begin
            d0 = right_val;
            right_idx = right_idx + 3'd1;
        end

        left_val  = (left_idx == 3'd0) ? s0 : (left_idx == 3'd1) ? s1 : (left_idx == 3'd2) ? s2 : s3;
        right_val = (right_idx == 3'd0) ? s4 : (right_idx == 3'd1) ? s5 : (right_idx == 3'd2) ? s6 : s7;
        if ((left_idx < 4) && ((right_idx >= 4) || take_left(left_val, right_val, direction))) begin
            d1 = left_val;
            left_idx = left_idx + 3'd1;
        end else begin
            d1 = right_val;
            right_idx = right_idx + 3'd1;
        end

        left_val  = (left_idx == 3'd0) ? s0 : (left_idx == 3'd1) ? s1 : (left_idx == 3'd2) ? s2 : s3;
        right_val = (right_idx == 3'd0) ? s4 : (right_idx == 3'd1) ? s5 : (right_idx == 3'd2) ? s6 : s7;
        if ((left_idx < 4) && ((right_idx >= 4) || take_left(left_val, right_val, direction))) begin
            d2 = left_val;
            left_idx = left_idx + 3'd1;
        end else begin
            d2 = right_val;
            right_idx = right_idx + 3'd1;
        end

        left_val  = (left_idx == 3'd0) ? s0 : (left_idx == 3'd1) ? s1 : (left_idx == 3'd2) ? s2 : s3;
        right_val = (right_idx == 3'd0) ? s4 : (right_idx == 3'd1) ? s5 : (right_idx == 3'd2) ? s6 : s7;
        if ((left_idx < 4) && ((right_idx >= 4) || take_left(left_val, right_val, direction))) begin
            d3 = left_val;
            left_idx = left_idx + 3'd1;
        end else begin
            d3 = right_val;
            right_idx = right_idx + 3'd1;
        end

        left_val  = (left_idx == 3'd0) ? s0 : (left_idx == 3'd1) ? s1 : (left_idx == 3'd2) ? s2 : s3;
        right_val = (right_idx == 3'd0) ? s4 : (right_idx == 3'd1) ? s5 : (right_idx == 3'd2) ? s6 : s7;
        if ((left_idx < 4) && ((right_idx >= 4) || take_left(left_val, right_val, direction))) begin
            d4 = left_val;
            left_idx = left_idx + 3'd1;
        end else begin
            d4 = right_val;
            right_idx = right_idx + 3'd1;
        end

        left_val  = (left_idx == 3'd0) ? s0 : (left_idx == 3'd1) ? s1 : (left_idx == 3'd2) ? s2 : s3;
        right_val = (right_idx == 3'd0) ? s4 : (right_idx == 3'd1) ? s5 : (right_idx == 3'd2) ? s6 : s7;
        if ((left_idx < 4) && ((right_idx >= 4) || take_left(left_val, right_val, direction))) begin
            d5 = left_val;
            left_idx = left_idx + 3'd1;
        end else begin
            d5 = right_val;
            right_idx = right_idx + 3'd1;
        end

        left_val  = (left_idx == 3'd0) ? s0 : (left_idx == 3'd1) ? s1 : (left_idx == 3'd2) ? s2 : s3;
        right_val = (right_idx == 3'd0) ? s4 : (right_idx == 3'd1) ? s5 : (right_idx == 3'd2) ? s6 : s7;
        if ((left_idx < 4) && ((right_idx >= 4) || take_left(left_val, right_val, direction))) begin
            d6 = left_val;
            left_idx = left_idx + 3'd1;
        end else begin
            d6 = right_val;
            right_idx = right_idx + 3'd1;
        end

        left_val  = (left_idx == 3'd0) ? s0 : (left_idx == 3'd1) ? s1 : (left_idx == 3'd2) ? s2 : s3;
        right_val = (right_idx == 3'd0) ? s4 : (right_idx == 3'd1) ? s5 : (right_idx == 3'd2) ? s6 : s7;
        if ((left_idx < 4) && ((right_idx >= 4) || take_left(left_val, right_val, direction))) begin
            d7 = left_val;
            left_idx = left_idx + 3'd1;
        end else begin
            d7 = right_val;
            right_idx = right_idx + 3'd1;
        end
    end
endtask

always_comb begin
    logic [31:0] a0_0;
    logic [31:0] a0_1;
    logic [31:0] a1_0;
    logic [31:0] a1_1;
    logic [31:0] b0_0;
    logic [31:0] b0_1;
    logic [31:0] b1_0;
    logic [31:0] b1_1;
    logic [31:0] left4_0;
    logic [31:0] left4_1;
    logic [31:0] left4_2;
    logic [31:0] left4_3;
    logic [31:0] right4_0;
    logic [31:0] right4_1;
    logic [31:0] right4_2;
    logic [31:0] right4_3;

    a0_0 = 32'd0; a0_1 = 32'd0;
    a1_0 = 32'd0; a1_1 = 32'd0;
    b0_0 = 32'd0; b0_1 = 32'd0;
    b1_0 = 32'd0; b1_1 = 32'd0;
    left4_0 = 32'd0; left4_1 = 32'd0; left4_2 = 32'd0; left4_3 = 32'd0;
    right4_0 = 32'd0; right4_1 = 32'd0; right4_2 = 32'd0; right4_3 = 32'd0;
    sort_y0 = 32'd0; sort_y1 = 32'd0; sort_y2 = 32'd0; sort_y3 = 32'd0;
    sort_y4 = 32'd0; sort_y5 = 32'd0; sort_y6 = 32'd0; sort_y7 = 32'd0;

    sort2(in_x0, in_x1, in_dir, a0_0, a0_1);
    sort2(in_x2, in_x3, in_dir, a1_0, a1_1);
    sort2(in_x4, in_x5, in_dir, b0_0, b0_1);
    sort2(in_x6, in_x7, in_dir, b1_0, b1_1);

    merge2x2(a0_0, a0_1, a1_0, a1_1, in_dir, left4_0, left4_1, left4_2, left4_3);
    merge2x2(b0_0, b0_1, b1_0, b1_1, in_dir, right4_0, right4_1, right4_2, right4_3);

    merge4x4(
        left4_0, left4_1, left4_2, left4_3,
        right4_0, right4_1, right4_2, right4_3,
        in_dir,
        sort_y0, sort_y1, sort_y2, sort_y3, sort_y4, sort_y5, sort_y6, sort_y7
    );
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy   <= 1'b0;
        out_y0 <= 32'h00000000;
        out_y1 <= 32'h00000000;
        out_y2 <= 32'h00000000;
        out_y3 <= 32'h00000000;
        out_y4 <= 32'h00000000;
        out_y5 <= 32'h00000000;
        out_y6 <= 32'h00000000;
        out_y7 <= 32'h00000000;
    end else if (input_accepted) begin
        out_y0 <= sort_y0;
        out_y1 <= sort_y1;
        out_y2 <= sort_y2;
        out_y3 <= sort_y3;
        out_y4 <= sort_y4;
        out_y5 <= sort_y5;
        out_y6 <= sort_y6;
        out_y7 <= sort_y7;
        busy   <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule