module bitonic_sort16_fp32 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_dir,
    input  logic [31:0]  in_x0,
    input  logic [31:0]  in_x1,
    input  logic [31:0]  in_x2,
    input  logic [31:0]  in_x3,
    input  logic [31:0]  in_x4,
    input  logic [31:0]  in_x5,
    input  logic [31:0]  in_x6,
    input  logic [31:0]  in_x7,
    input  logic [31:0]  in_x8,
    input  logic [31:0]  in_x9,
    input  logic [31:0]  in_x10,
    input  logic [31:0]  in_x11,
    input  logic [31:0]  in_x12,
    input  logic [31:0]  in_x13,
    input  logic [31:0]  in_x14,
    input  logic [31:0]  in_x15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_x0,
    output logic [31:0]  out_x1,
    output logic [31:0]  out_x2,
    output logic [31:0]  out_x3,
    output logic [31:0]  out_x4,
    output logic [31:0]  out_x5,
    output logic [31:0]  out_x6,
    output logic [31:0]  out_x7,
    output logic [31:0]  out_x8,
    output logic [31:0]  out_x9,
    output logic [31:0]  out_x10,
    output logic [31:0]  out_x11,
    output logic [31:0]  out_x12,
    output logic [31:0]  out_x13,
    output logic [31:0]  out_x14,
    output logic [31:0]  out_x15
);

logic busy;
wire  input_accepted;
wire  output_consumed;

logic [31:0] s0 [0:15];
logic [31:0] s1 [0:15];
logic [31:0] s2 [0:15];
logic [31:0] s3 [0:15];
logic [31:0] sorted [0:15];

integer i;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic fp32_gt(input logic [31:0] a, input logic [31:0] b);
    logic a_sign;
    logic b_sign;
    logic [7:0] a_exp;
    logic [7:0] b_exp;
    logic [22:0] a_frac;
    logic [22:0] b_frac;
    logic a_nan;
    logic b_nan;
    logic a_zero;
    logic b_zero;
begin
    a_sign = a[31];
    b_sign = b[31];
    a_exp  = a[30:23];
    b_exp  = b[30:23];
    a_frac = a[22:0];
    b_frac = b[22:0];

    a_nan  = (a_exp == 8'hff) && (a_frac != 23'd0);
    b_nan  = (b_exp == 8'hff) && (b_frac != 23'd0);
    a_zero = (a[30:0] == 31'd0);
    b_zero = (b[30:0] == 31'd0);

    fp32_gt = 1'b0;

    if (a_nan || b_nan) begin
        fp32_gt = 1'b0;
    end else if (a_zero && b_zero) begin
        fp32_gt = 1'b0;
    end else if (a_sign != b_sign) begin
        if (a_sign == 1'b0) begin
            fp32_gt = 1'b1;
        end else begin
            fp32_gt = 1'b0;
        end
    end else if (a_sign == 1'b0) begin
        if (a[30:0] > b[30:0]) begin
            fp32_gt = 1'b1;
        end else begin
            fp32_gt = 1'b0;
        end
    end else begin
        if (a[30:0] < b[30:0]) begin
            fp32_gt = 1'b1;
        end else begin
            fp32_gt = 1'b0;
        end
    end
end
endfunction

task automatic core2(
    input  logic       dir,
    input  logic [31:0] in0,
    input  logic [31:0] in1,
    output logic [31:0] out0,
    output logic [31:0] out1
);
    logic gt;
    logic [31:0] t0;
    logic [31:0] t1;
begin
    t0 = in0;
    t1 = in1;
    gt = fp32_gt(t0, t1);
    if (dir ? gt : fp32_gt(t1, t0)) begin
        out0 = t1;
        out1 = t0;
    end else begin
        out0 = t0;
        out1 = t1;
    end
end
endtask

task automatic core4(
    input  logic       dir,
    input  logic [31:0] in0,
    input  logic [31:0] in1,
    input  logic [31:0] in2,
    input  logic [31:0] in3,
    output logic [31:0] out0,
    output logic [31:0] out1,
    output logic [31:0] out2,
    output logic [31:0] out3
);
    logic [31:0] a0;
    logic [31:0] a1;
    logic [31:0] a2;
    logic [31:0] a3;
    logic [31:0] t;
begin
    a0 = in0; a1 = in1; a2 = in2; a3 = in3;

    if (dir ? fp32_gt(a0, a3) : fp32_gt(a3, a0)) begin t = a0; a0 = a3; a3 = t; end
    if (dir ? fp32_gt(a1, a2) : fp32_gt(a2, a1)) begin t = a1; a1 = a2; a2 = t; end

    if (dir ? fp32_gt(a0, a1) : fp32_gt(a1, a0)) begin t = a0; a0 = a1; a1 = t; end
    if (dir ? fp32_gt(a2, a3) : fp32_gt(a3, a2)) begin t = a2; a2 = a3; a3 = t; end

    out0 = a0; out1 = a1; out2 = a2; out3 = a3;
end
endtask

task automatic core8(
    input  logic       dir,
    input  logic [31:0] in0,
    input  logic [31:0] in1,
    input  logic [31:0] in2,
    input  logic [31:0] in3,
    input  logic [31:0] in4,
    input  logic [31:0] in5,
    input  logic [31:0] in6,
    input  logic [31:0] in7,
    output logic [31:0] out0,
    output logic [31:0] out1,
    output logic [31:0] out2,
    output logic [31:0] out3,
    output logic [31:0] out4,
    output logic [31:0] out5,
    output logic [31:0] out6,
    output logic [31:0] out7
);
    logic [31:0] a0;
    logic [31:0] a1;
    logic [31:0] a2;
    logic [31:0] a3;
    logic [31:0] a4;
    logic [31:0] a5;
    logic [31:0] a6;
    logic [31:0] a7;
    logic [31:0] t;
begin
    a0 = in0; a1 = in1; a2 = in2; a3 = in3;
    a4 = in4; a5 = in5; a6 = in6; a7 = in7;

    if (dir ? fp32_gt(a0, a7) : fp32_gt(a7, a0)) begin t = a0; a0 = a7; a7 = t; end
    if (dir ? fp32_gt(a1, a6) : fp32_gt(a6, a1)) begin t = a1; a1 = a6; a6 = t; end
    if (dir ? fp32_gt(a2, a5) : fp32_gt(a5, a2)) begin t = a2; a2 = a5; a5 = t; end
    if (dir ? fp32_gt(a3, a4) : fp32_gt(a4, a3)) begin t = a3; a3 = a4; a4 = t; end

    if (dir ? fp32_gt(a0, a2) : fp32_gt(a2, a0)) begin t = a0; a0 = a2; a2 = t; end
    if (dir ? fp32_gt(a1, a3) : fp32_gt(a3, a1)) begin t = a1; a1 = a3; a3 = t; end
    if (dir ? fp32_gt(a4, a6) : fp32_gt(a6, a4)) begin t = a4; a4 = a6; a6 = t; end
    if (dir ? fp32_gt(a5, a7) : fp32_gt(a7, a5)) begin t = a5; a5 = a7; a7 = t; end

    if (dir ? fp32_gt(a0, a1) : fp32_gt(a1, a0)) begin t = a0; a0 = a1; a1 = t; end
    if (dir ? fp32_gt(a2, a3) : fp32_gt(a3, a2)) begin t = a2; a2 = a3; a3 = t; end
    if (dir ? fp32_gt(a4, a5) : fp32_gt(a5, a4)) begin t = a4; a4 = a5; a5 = t; end
    if (dir ? fp32_gt(a6, a7) : fp32_gt(a7, a6)) begin t = a6; a6 = a7; a7 = t; end

    out0 = a0; out1 = a1; out2 = a2; out3 = a3;
    out4 = a4; out5 = a5; out6 = a6; out7 = a7;
end
endtask

task automatic core16(
    input  logic       dir,
    input  logic [31:0] in0,
    input  logic [31:0] in1,
    input  logic [31:0] in2,
    input  logic [31:0] in3,
    input  logic [31:0] in4,
    input  logic [31:0] in5,
    input  logic [31:0] in6,
    input  logic [31:0] in7,
    input  logic [31:0] in8,
    input  logic [31:0] in9,
    input  logic [31:0] in10,
    input  logic [31:0] in11,
    input  logic [31:0] in12,
    input  logic [31:0] in13,
    input  logic [31:0] in14,
    input  logic [31:0] in15,
    output logic [31:0] out0,
    output logic [31:0] out1,
    output logic [31:0] out2,
    output logic [31:0] out3,
    output logic [31:0] out4,
    output logic [31:0] out5,
    output logic [31:0] out6,
    output logic [31:0] out7,
    output logic [31:0] out8,
    output logic [31:0] out9,
    output logic [31:0] out10,
    output logic [31:0] out11,
    output logic [31:0] out12,
    output logic [31:0] out13,
    output logic [31:0] out14,
    output logic [31:0] out15
);
    logic [31:0] a0;
    logic [31:0] a1;
    logic [31:0] a2;
    logic [31:0] a3;
    logic [31:0] a4;
    logic [31:0] a5;
    logic [31:0] a6;
    logic [31:0] a7;
    logic [31:0] a8;
    logic [31:0] a9;
    logic [31:0] a10;
    logic [31:0] a11;
    logic [31:0] a12;
    logic [31:0] a13;
    logic [31:0] a14;
    logic [31:0] a15;
    logic [31:0] t;
begin
    a0 = in0; a1 = in1; a2 = in2; a3 = in3;
    a4 = in4; a5 = in5; a6 = in6; a7 = in7;
    a8 = in8; a9 = in9; a10 = in10; a11 = in11;
    a12 = in12; a13 = in13; a14 = in14; a15 = in15;

    if (dir ? fp32_gt(a0, a15) : fp32_gt(a15, a0)) begin t = a0;  a0  = a15; a15 = t; end
    if (dir ? fp32_gt(a1, a14) : fp32_gt(a14, a1)) begin t = a1;  a1  = a14; a14 = t; end
    if (dir ? fp32_gt(a2, a13) : fp32_gt(a13, a2)) begin t = a2;  a2  = a13; a13 = t; end
    if (dir ? fp32_gt(a3, a12) : fp32_gt(a12, a3)) begin t = a3;  a3  = a12; a12 = t; end
    if (dir ? fp32_gt(a4, a11) : fp32_gt(a11, a4)) begin t = a4;  a4  = a11; a11 = t; end
    if (dir ? fp32_gt(a5, a10) : fp32_gt(a10, a5)) begin t = a5;  a5  = a10; a10 = t; end
    if (dir ? fp32_gt(a6, a9) : fp32_gt(a9, a6)) begin t = a6;  a6  = a9;  a9  = t; end
    if (dir ? fp32_gt(a7, a8) : fp32_gt(a8, a7)) begin t = a7;  a7  = a8;  a8  = t; end

    if (dir ? fp32_gt(a0, a4) : fp32_gt(a4, a0)) begin t = a0; a0 = a4; a4 = t; end
    if (dir ? fp32_gt(a1, a5) : fp32_gt(a5, a1)) begin t = a1; a1 = a5; a5 = t; end
    if (dir ? fp32_gt(a2, a6) : fp32_gt(a6, a2)) begin t = a2; a2 = a6; a6 = t; end
    if (dir ? fp32_gt(a3, a7) : fp32_gt(a7, a3)) begin t = a3; a3 = a7; a7 = t; end
    if (dir ? fp32_gt(a8, a12) : fp32_gt(a12, a8)) begin t = a8; a8 = a12; a12 = t; end
    if (dir ? fp32_gt(a9, a13) : fp32_gt(a13, a9)) begin t = a9; a9 = a13; a13 = t; end
    if (dir ? fp32_gt(a10, a14) : fp32_gt(a14, a10)) begin t = a10; a10 = a14; a14 = t; end
    if (dir ? fp32_gt(a11, a15) : fp32_gt(a15, a11)) begin t = a11; a11 = a15; a15 = t; end

    if (dir ? fp32_gt(a0, a2) : fp32_gt(a2, a0)) begin t = a0; a0 = a2; a2 = t; end
    if (dir ? fp32_gt(a1, a3) : fp32_gt(a3, a1)) begin t = a1; a1 = a3; a3 = t; end
    if (dir ? fp32_gt(a4, a6) : fp32_gt(a6, a4)) begin t = a4; a4 = a6; a6 = t; end
    if (dir ? fp32_gt(a5, a7) : fp32_gt(a7, a5)) begin t = a5; a5 = a7; a7 = t; end
    if (dir ? fp32_gt(a8, a10) : fp32_gt(a10, a8)) begin t = a8; a8 = a10; a10 = t; end
    if (dir ? fp32_gt(a9, a11) : fp32_gt(a11, a9)) begin t = a9; a9 = a11; a11 = t; end
    if (dir ? fp32_gt(a12, a14) : fp32_gt(a14, a12)) begin t = a12; a12 = a14; a14 = t; end
    if (dir ? fp32_gt(a13, a15) : fp32_gt(a15, a13)) begin t = a13; a13 = a15; a15 = t; end

    if (dir ? fp32_gt(a0, a1) : fp32_gt(a1, a0)) begin t = a0; a0 = a1; a1 = t; end
    if (dir ? fp32_gt(a2, a3) : fp32_gt(a3, a2)) begin t = a2; a2 = a3; a3 = t; end
    if (dir ? fp32_gt(a4, a5) : fp32_gt(a5, a4)) begin t = a4; a4 = a5; a5 = t; end
    if (dir ? fp32_gt(a6, a7) : fp32_gt(a7, a6)) begin t = a6; a6 = a7; a7 = t; end
    if (dir ? fp32_gt(a8, a9) : fp32_gt(a9, a8)) begin t = a8; a8 = a9; a9 = t; end
    if (dir ? fp32_gt(a10, a11) : fp32_gt(a11, a10)) begin t = a10; a10 = a11; a11 = t; end
    if (dir ? fp32_gt(a12, a13) : fp32_gt(a13, a12)) begin t = a12; a12 = a13; a13 = t; end
    if (dir ? fp32_gt(a14, a15) : fp32_gt(a15, a14)) begin t = a14; a14 = a15; a15 = t; end

    out0 = a0; out1 = a1; out2 = a2; out3 = a3;
    out4 = a4; out5 = a5; out6 = a6; out7 = a7;
    out8 = a8; out9 = a9; out10 = a10; out11 = a11;
    out12 = a12; out13 = a13; out14 = a14; out15 = a15;
end
endtask

always_comb begin
    core2(in_dir, in_x0,  in_x1,  s0[0],  s0[1]);
    core2(in_dir, in_x2,  in_x3,  s0[2],  s0[3]);
    core2(in_dir, in_x4,  in_x5,  s0[4],  s0[5]);
    core2(in_dir, in_x6,  in_x7,  s0[6],  s0[7]);
    core2(in_dir, in_x8,  in_x9,  s0[8],  s0[9]);
    core2(in_dir, in_x10, in_x11, s0[10], s0[11]);
    core2(in_dir, in_x12, in_x13, s0[12], s0[13]);
    core2(in_dir, in_x14, in_x15, s0[14], s0[15]);

    core4(in_dir, s0[0],  s0[1],  s0[2],  s0[3],  s1[0],  s1[1],  s1[2],  s1[3]);
    core4(in_dir, s0[4],  s0[5],  s0[6],  s0[7],  s1[4],  s1[5],  s1[6],  s1[7]);
    core4(in_dir, s0[8],  s0[9],  s0[10], s0[11], s1[8],  s1[9],  s1[10], s1[11]);
    core4(in_dir, s0[12], s0[13], s0[14], s0[15], s1[12], s1[13], s1[14], s1[15]);

    core8(in_dir, s1[0], s1[1], s1[2], s1[3], s1[4], s1[5], s1[6], s1[7],
          s2[0], s2[1], s2[2], s2[3], s2[4], s2[5], s2[6], s2[7]);
    core8(in_dir, s1[8], s1[9], s1[10], s1[11], s1[12], s1[13], s1[14], s1[15],
          s2[8], s2[9], s2[10], s2[11], s2[12], s2[13], s2[14], s2[15]);

    core16(in_dir,
           s2[0], s2[1], s2[2], s2[3], s2[4], s2[5], s2[6], s2[7],
           s2[8], s2[9], s2[10], s2[11], s2[12], s2[13], s2[14], s2[15],
           sorted[0], sorted[1], sorted[2], sorted[3], sorted[4], sorted[5], sorted[6], sorted[7],
           sorted[8], sorted[9], sorted[10], sorted[11], sorted[12], sorted[13], sorted[14], sorted[15]);
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy    <= 1'b0;
        out_x0  <= 32'h00000000;
        out_x1  <= 32'h00000000;
        out_x2  <= 32'h00000000;
        out_x3  <= 32'h00000000;
        out_x4  <= 32'h00000000;
        out_x5  <= 32'h00000000;
        out_x6  <= 32'h00000000;
        out_x7  <= 32'h00000000;
        out_x8  <= 32'h00000000;
        out_x9  <= 32'h00000000;
        out_x10 <= 32'h00000000;
        out_x11 <= 32'h00000000;
        out_x12 <= 32'h00000000;
        out_x13 <= 32'h00000000;
        out_x14 <= 32'h00000000;
        out_x15 <= 32'h00000000;
    end else if (input_accepted) begin
        out_x0  <= sorted[0];
        out_x1  <= sorted[1];
        out_x2  <= sorted[2];
        out_x3  <= sorted[3];
        out_x4  <= sorted[4];
        out_x5  <= sorted[5];
        out_x6  <= sorted[6];
        out_x7  <= sorted[7];
        out_x8  <= sorted[8];
        out_x9  <= sorted[9];
        out_x10 <= sorted[10];
        out_x11 <= sorted[11];
        out_x12 <= sorted[12];
        out_x13 <= sorted[13];
        out_x14 <= sorted[14];
        out_x15 <= sorted[15];
        busy    <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
