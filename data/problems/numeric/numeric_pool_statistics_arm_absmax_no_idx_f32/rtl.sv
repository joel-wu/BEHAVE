module abs_max_f32x16 (
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
    output logic [31:0] out_absmax
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [31:0] fp32_abs_bits(input logic [31:0] x);
        begin
            fp32_abs_bits = {1'b0, x[30:0]};
        end
    endfunction

    function automatic logic is_nan(input logic [31:0] x);
        begin
            is_nan = (&x[30:23]) & (|x[22:0]);
        end
    endfunction

    function automatic logic fp32_gt(input logic [31:0] a, input logic [31:0] b);
        logic a_nan;
        logic b_nan;
        logic [7:0] a_exp;
        logic [7:0] b_exp;
        logic [22:0] a_frac;
        logic [22:0] b_frac;
        logic a_is_zero;
        logic b_is_zero;
        begin
            a_nan = is_nan(a);
            b_nan = is_nan(b);
            a_exp = a[30:23];
            b_exp = b[30:23];
            a_frac = a[22:0];
            b_frac = b[22:0];
            a_is_zero = (a[30:0] == 31'd0);
            b_is_zero = (b[30:0] == 31'd0);

            fp32_gt = 1'b0;

            if (a_nan || b_nan) begin
                fp32_gt = 1'b0;
            end else if (a_is_zero && b_is_zero) begin
                fp32_gt = 1'b0;
            end else if (a_exp > b_exp) begin
                fp32_gt = 1'b1;
            end else if (a_exp < b_exp) begin
                fp32_gt = 1'b0;
            end else if (a_frac > b_frac) begin
                fp32_gt = 1'b1;
            end else begin
                fp32_gt = 1'b0;
            end
        end
    endfunction

    function automatic logic [31:0] max_abs2(input logic [31:0] new_val, input logic [31:0] cur_val);
        begin
            if (fp32_gt(new_val, cur_val)) begin
                max_abs2 = new_val;
            end else begin
                max_abs2 = cur_val;
            end
        end
    endfunction

    logic [31:0] a0, a1, a2, a3, a4, a5, a6, a7;
    logic [31:0] a8, a9, a10, a11, a12, a13, a14, a15;
    logic [31:0] r0, r1, r2, r3, r4, r5, r6, r7;
    logic [31:0] r8, r9, r10, r11, r12, r13, r14, r15;

    always_comb begin
        a0  = fp32_abs_bits(in_x0);
        a1  = fp32_abs_bits(in_x1);
        a2  = fp32_abs_bits(in_x2);
        a3  = fp32_abs_bits(in_x3);
        a4  = fp32_abs_bits(in_x4);
        a5  = fp32_abs_bits(in_x5);
        a6  = fp32_abs_bits(in_x6);
        a7  = fp32_abs_bits(in_x7);
        a8  = fp32_abs_bits(in_x8);
        a9  = fp32_abs_bits(in_x9);
        a10 = fp32_abs_bits(in_x10);
        a11 = fp32_abs_bits(in_x11);
        a12 = fp32_abs_bits(in_x12);
        a13 = fp32_abs_bits(in_x13);
        a14 = fp32_abs_bits(in_x14);
        a15 = fp32_abs_bits(in_x15);

        r0  = a0;
        r1  = max_abs2(a1,  r0);
        r2  = max_abs2(a2,  r1);
        r3  = max_abs2(a3,  r2);
        r4  = max_abs2(a4,  r3);
        r5  = max_abs2(a5,  r4);
        r6  = max_abs2(a6,  r5);
        r7  = max_abs2(a7,  r6);
        r8  = max_abs2(a8,  r7);
        r9  = max_abs2(a9,  r8);
        r10 = max_abs2(a10, r9);
        r11 = max_abs2(a11, r10);
        r12 = max_abs2(a12, r11);
        r13 = max_abs2(a13, r12);
        r14 = max_abs2(a14, r13);
        r15 = max_abs2(a15, r14);
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy       <= 1'b0;
            out_absmax <= 32'h00000000;
        end else if (input_accepted) begin
            busy       <= 1'b1;
            out_absmax <= r15;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
