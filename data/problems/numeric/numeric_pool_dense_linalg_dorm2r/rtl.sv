module householder_qr_apply4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_trans,

    input  logic [63:0]  in_a00,
    input  logic [63:0]  in_a10,
    input  logic [63:0]  in_a20,
    input  logic [63:0]  in_a30,
    input  logic [63:0]  in_a01,
    input  logic [63:0]  in_a11,
    input  logic [63:0]  in_a21,
    input  logic [63:0]  in_a31,
    input  logic [63:0]  in_a02,
    input  logic [63:0]  in_a12,
    input  logic [63:0]  in_a22,
    input  logic [63:0]  in_a32,
    input  logic [63:0]  in_a03,
    input  logic [63:0]  in_a13,
    input  logic [63:0]  in_a23,
    input  logic [63:0]  in_a33,

    input  logic [63:0]  in_tau0,
    input  logic [63:0]  in_tau1,
    input  logic [63:0]  in_tau2,
    input  logic [63:0]  in_tau3,

    input  logic [63:0]  in_c00,
    input  logic [63:0]  in_c10,
    input  logic [63:0]  in_c20,
    input  logic [63:0]  in_c30,
    input  logic [63:0]  in_c01,
    input  logic [63:0]  in_c11,
    input  logic [63:0]  in_c21,
    input  logic [63:0]  in_c31,
    input  logic [63:0]  in_c02,
    input  logic [63:0]  in_c12,
    input  logic [63:0]  in_c22,
    input  logic [63:0]  in_c32,
    input  logic [63:0]  in_c03,
    input  logic [63:0]  in_c13,
    input  logic [63:0]  in_c23,
    input  logic [63:0]  in_c33,

    output logic         out_valid,
    input  logic         out_ready,

    output logic [63:0]  out_c00,
    output logic [63:0]  out_c10,
    output logic [63:0]  out_c20,
    output logic [63:0]  out_c30,
    output logic [63:0]  out_c01,
    output logic [63:0]  out_c11,
    output logic [63:0]  out_c21,
    output logic [63:0]  out_c31,
    output logic [63:0]  out_c02,
    output logic [63:0]  out_c12,
    output logic [63:0]  out_c22,
    output logic [63:0]  out_c32,
    output logic [63:0]  out_c03,
    output logic [63:0]  out_c13,
    output logic [63:0]  out_c23,
    output logic [63:0]  out_c33
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [63:0] mul64(input logic [63:0] a, input logic [63:0] b);
    logic [127:0] prod;
begin
    prod  = a * b;
    mul64 = prod[63:0];
end
endfunction

function automatic logic [63:0] add64(input logic [63:0] a, input logic [63:0] b);
begin
    add64 = a + b;
end
endfunction

function automatic logic [63:0] sub64(input logic [63:0] a, input logic [63:0] b);
begin
    sub64 = a - b;
end
endfunction

task automatic apply_h0(
    input  logic [63:0] a10,
    input  logic [63:0] a20,
    input  logic [63:0] a30,
    input  logic [63:0] tau0,
    inout  logic [63:0] r0,
    inout  logic [63:0] r1,
    inout  logic [63:0] r2,
    inout  logic [63:0] r3
);
    logic [63:0] t1, t2, t3, dot, w;
begin
    t1  = mul64(a10, r1);
    t2  = mul64(a20, r2);
    t3  = mul64(a30, r3);
    dot = add64(add64(add64(r0, t1), t2), t3);
    w   = mul64(tau0, dot);
    r0  = sub64(r0, w);
    r1  = sub64(r1, mul64(a10, w));
    r2  = sub64(r2, mul64(a20, w));
    r3  = sub64(r3, mul64(a30, w));
end
endtask

task automatic apply_h1(
    input  logic [63:0] a21,
    input  logic [63:0] a31,
    input  logic [63:0] tau1,
    inout  logic [63:0] r0,
    inout  logic [63:0] r1,
    inout  logic [63:0] r2,
    inout  logic [63:0] r3
);
    logic [63:0] t1, t2, dot, w;
begin
    t1  = mul64(a21, r2);
    t2  = mul64(a31, r3);
    dot = add64(add64(r1, t1), t2);
    w   = mul64(tau1, dot);
    r0  = r0;
    r1  = sub64(r1, w);
    r2  = sub64(r2, mul64(a21, w));
    r3  = sub64(r3, mul64(a31, w));
end
endtask

task automatic apply_h2(
    input  logic [63:0] a32,
    input  logic [63:0] tau2,
    inout  logic [63:0] r0,
    inout  logic [63:0] r1,
    inout  logic [63:0] r2,
    inout  logic [63:0] r3
);
    logic [63:0] t1, dot, w;
begin
    t1  = mul64(a32, r3);
    dot = add64(r2, t1);
    w   = mul64(tau2, dot);
    r0  = r0;
    r1  = r1;
    r2  = sub64(r2, w);
    r3  = sub64(r3, mul64(a32, w));
end
endtask

task automatic apply_h3(
    input  logic [63:0] tau3,
    inout  logic [63:0] r0,
    inout  logic [63:0] r1,
    inout  logic [63:0] r2,
    inout  logic [63:0] r3
);
    logic [63:0] dot, w;
begin
    dot = r3;
    w   = mul64(tau3, dot);
    r0  = r0;
    r1  = r1;
    r2  = r2;
    r3  = sub64(r3, w);
end
endtask

always_ff @(posedge clk) begin
    logic [63:0] r00, r10, r20, r30;
    logic [63:0] r01, r11, r21, r31;
    logic [63:0] r02, r12, r22, r32;
    logic [63:0] r03, r13, r23, r33;
    if (rst) begin
        busy    <= 1'b0;
        out_c00 <= 64'd0;
        out_c10 <= 64'd0;
        out_c20 <= 64'd0;
        out_c30 <= 64'd0;
        out_c01 <= 64'd0;
        out_c11 <= 64'd0;
        out_c21 <= 64'd0;
        out_c31 <= 64'd0;
        out_c02 <= 64'd0;
        out_c12 <= 64'd0;
        out_c22 <= 64'd0;
        out_c32 <= 64'd0;
        out_c03 <= 64'd0;
        out_c13 <= 64'd0;
        out_c23 <= 64'd0;
        out_c33 <= 64'd0;
    end else if (input_accepted) begin
        r00 = in_c00; r10 = in_c10; r20 = in_c20; r30 = in_c30;
        r01 = in_c01; r11 = in_c11; r21 = in_c21; r31 = in_c31;
        r02 = in_c02; r12 = in_c12; r22 = in_c22; r32 = in_c32;
        r03 = in_c03; r13 = in_c13; r23 = in_c23; r33 = in_c33;

        if (in_trans == 1'b1) begin
            apply_h0(in_a10, in_a20, in_a30, in_tau0, r00, r10, r20, r30);
            apply_h0(in_a10, in_a20, in_a30, in_tau0, r01, r11, r21, r31);
            apply_h0(in_a10, in_a20, in_a30, in_tau0, r02, r12, r22, r32);
            apply_h0(in_a10, in_a20, in_a30, in_tau0, r03, r13, r23, r33);

            apply_h1(in_a21, in_a31, in_tau1, r00, r10, r20, r30);
            apply_h1(in_a21, in_a31, in_tau1, r01, r11, r21, r31);
            apply_h1(in_a21, in_a31, in_tau1, r02, r12, r22, r32);
            apply_h1(in_a21, in_a31, in_tau1, r03, r13, r23, r33);

            apply_h2(in_a32, in_tau2, r00, r10, r20, r30);
            apply_h2(in_a32, in_tau2, r01, r11, r21, r31);
            apply_h2(in_a32, in_tau2, r02, r12, r22, r32);
            apply_h2(in_a32, in_tau2, r03, r13, r23, r33);

            apply_h3(in_tau3, r00, r10, r20, r30);
            apply_h3(in_tau3, r01, r11, r21, r31);
            apply_h3(in_tau3, r02, r12, r22, r32);
            apply_h3(in_tau3, r03, r13, r23, r33);
        end else begin
            apply_h3(in_tau3, r00, r10, r20, r30);
            apply_h3(in_tau3, r01, r11, r21, r31);
            apply_h3(in_tau3, r02, r12, r22, r32);
            apply_h3(in_tau3, r03, r13, r23, r33);

            apply_h2(in_a32, in_tau2, r00, r10, r20, r30);
            apply_h2(in_a32, in_tau2, r01, r11, r21, r31);
            apply_h2(in_a32, in_tau2, r02, r12, r22, r32);
            apply_h2(in_a32, in_tau2, r03, r13, r23, r33);

            apply_h1(in_a21, in_a31, in_tau1, r00, r10, r20, r30);
            apply_h1(in_a21, in_a31, in_tau1, r01, r11, r21, r31);
            apply_h1(in_a21, in_a31, in_tau1, r02, r12, r22, r32);
            apply_h1(in_a21, in_a31, in_tau1, r03, r13, r23, r33);

            apply_h0(in_a10, in_a20, in_a30, in_tau0, r00, r10, r20, r30);
            apply_h0(in_a10, in_a20, in_a30, in_tau0, r01, r11, r21, r31);
            apply_h0(in_a10, in_a20, in_a30, in_tau0, r02, r12, r22, r32);
            apply_h0(in_a10, in_a20, in_a30, in_tau0, r03, r13, r23, r33);
        end

        out_c00 <= r00; out_c10 <= r10; out_c20 <= r20; out_c30 <= r30;
        out_c01 <= r01; out_c11 <= r11; out_c21 <= r21; out_c31 <= r31;
        out_c02 <= r02; out_c12 <= r12; out_c22 <= r22; out_c32 <= r32;
        out_c03 <= r03; out_c13 <= r13; out_c23 <= r23; out_c33 <= r33;
        busy    <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
