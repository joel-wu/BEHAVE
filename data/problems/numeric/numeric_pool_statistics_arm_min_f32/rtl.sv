module fp32_vector_min16 (
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
    output logic [31:0] out_min,
    output logic [3:0]  out_index
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic is_nan(input logic [31:0] a);
        is_nan = (&a[30:23]) && (|a[22:0]);
    endfunction

    function automatic logic is_zero(input logic [31:0] a);
        // Flush-to-zero: exponent field all-zero means zero OR subnormal.
        // fp_lib.fp32_cmp flushes subnormal inputs to zero, and the spec
        // requires this comparator be bit-equivalent to fp32_cmp, so
        // subnormals must compare as zero too.
        is_zero = (a[30:0] == 31'd0);
    endfunction

    function automatic logic fp32_lt(input logic [31:0] a, input logic [31:0] b);
        logic a_nan;
        logic b_nan;
        logic a_zero;
        logic b_zero;
        logic a_sign;
        logic b_sign;
        logic [30:0] a_mag;
        logic [30:0] b_mag;
    begin
        a_nan  = is_nan(a);
        b_nan  = is_nan(b);
        a_zero = is_zero(a);
        b_zero = is_zero(b);
        a_sign = a[31];
        b_sign = b[31];
        a_mag  = a[30:0];
        b_mag  = b[30:0];

        fp32_lt = 1'b0;

        if (a_nan || b_nan) begin
            fp32_lt = 1'b0;
        end else if (a_zero && b_zero) begin
            fp32_lt = 1'b0;
        end else if (a == b) begin
            fp32_lt = 1'b0;
        end else if (a_sign != b_sign) begin
            fp32_lt = a_sign;
        end else if (!a_sign) begin
            fp32_lt = (a_mag < b_mag);
        end else begin
            fp32_lt = (a_mag > b_mag);
        end
    end
    endfunction

    function automatic logic cmp_gt_zero(input logic [31:0] current_min, input logic [31:0] candidate);
    begin
        cmp_gt_zero = fp32_lt(candidate, current_min);
    end
    endfunction

    logic [31:0] next_min;
    logic [3:0]  next_index;
    logic [31:0] tmin1;
    logic [31:0] tmin2;
    logic [31:0] tmin3;
    logic [31:0] tmin4;
    logic [31:0] tmin5;
    logic [31:0] tmin6;
    logic [31:0] tmin7;
    logic [31:0] tmin8;
    logic [31:0] tmin9;
    logic [31:0] tmin10;
    logic [31:0] tmin11;
    logic [31:0] tmin12;
    logic [31:0] tmin13;
    logic [31:0] tmin14;
    logic [31:0] tmin15;
    logic [3:0]  tidx1;
    logic [3:0]  tidx2;
    logic [3:0]  tidx3;
    logic [3:0]  tidx4;
    logic [3:0]  tidx5;
    logic [3:0]  tidx6;
    logic [3:0]  tidx7;
    logic [3:0]  tidx8;
    logic [3:0]  tidx9;
    logic [3:0]  tidx10;
    logic [3:0]  tidx11;
    logic [3:0]  tidx12;
    logic [3:0]  tidx13;
    logic [3:0]  tidx14;
    logic [3:0]  tidx15;

    always_comb begin
        tmin1 = in_x0;
        tidx1 = 4'd0;
        if (cmp_gt_zero(tmin1, in_x1)) begin
            tmin1 = in_x1;
            tidx1 = 4'd1;
        end

        tmin2 = tmin1;
        tidx2 = tidx1;
        if (cmp_gt_zero(tmin2, in_x2)) begin
            tmin2 = in_x2;
            tidx2 = 4'd2;
        end

        tmin3 = tmin2;
        tidx3 = tidx2;
        if (cmp_gt_zero(tmin3, in_x3)) begin
            tmin3 = in_x3;
            tidx3 = 4'd3;
        end

        tmin4 = tmin3;
        tidx4 = tidx3;
        if (cmp_gt_zero(tmin4, in_x4)) begin
            tmin4 = in_x4;
            tidx4 = 4'd4;
        end

        tmin5 = tmin4;
        tidx5 = tidx4;
        if (cmp_gt_zero(tmin5, in_x5)) begin
            tmin5 = in_x5;
            tidx5 = 4'd5;
        end

        tmin6 = tmin5;
        tidx6 = tidx5;
        if (cmp_gt_zero(tmin6, in_x6)) begin
            tmin6 = in_x6;
            tidx6 = 4'd6;
        end

        tmin7 = tmin6;
        tidx7 = tidx6;
        if (cmp_gt_zero(tmin7, in_x7)) begin
            tmin7 = in_x7;
            tidx7 = 4'd7;
        end

        tmin8 = tmin7;
        tidx8 = tidx7;
        if (cmp_gt_zero(tmin8, in_x8)) begin
            tmin8 = in_x8;
            tidx8 = 4'd8;
        end

        tmin9 = tmin8;
        tidx9 = tidx8;
        if (cmp_gt_zero(tmin9, in_x9)) begin
            tmin9 = in_x9;
            tidx9 = 4'd9;
        end

        tmin10 = tmin9;
        tidx10 = tidx9;
        if (cmp_gt_zero(tmin10, in_x10)) begin
            tmin10 = in_x10;
            tidx10 = 4'd10;
        end

        tmin11 = tmin10;
        tidx11 = tidx10;
        if (cmp_gt_zero(tmin11, in_x11)) begin
            tmin11 = in_x11;
            tidx11 = 4'd11;
        end

        tmin12 = tmin11;
        tidx12 = tidx11;
        if (cmp_gt_zero(tmin12, in_x12)) begin
            tmin12 = in_x12;
            tidx12 = 4'd12;
        end

        tmin13 = tmin12;
        tidx13 = tidx12;
        if (cmp_gt_zero(tmin13, in_x13)) begin
            tmin13 = in_x13;
            tidx13 = 4'd13;
        end

        tmin14 = tmin13;
        tidx14 = tidx13;
        if (cmp_gt_zero(tmin14, in_x14)) begin
            tmin14 = in_x14;
            tidx14 = 4'd14;
        end

        tmin15 = tmin14;
        tidx15 = tidx14;
        if (cmp_gt_zero(tmin15, in_x15)) begin
            tmin15 = in_x15;
            tidx15 = 4'd15;
        end

        next_min   = tmin15;
        next_index = tidx15;
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy      <= 1'b0;
            out_min   <= 32'h00000000;
            out_index <= 4'h0;
        end else if (input_accepted) begin
            busy      <= 1'b1;
            out_min   <= next_min;
            out_index <= next_index;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
