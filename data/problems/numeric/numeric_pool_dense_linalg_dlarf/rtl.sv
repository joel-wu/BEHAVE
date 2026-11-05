module householder_reflector_4x4_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_side_left,
    input  logic [31:0]  in_tau,
    input  logic [31:0]  in_v0,
    input  logic [31:0]  in_v1,
    input  logic [31:0]  in_v2,
    input  logic [31:0]  in_v3,
    input  logic [31:0]  in_c00,
    input  logic [31:0]  in_c01,
    input  logic [31:0]  in_c02,
    input  logic [31:0]  in_c03,
    input  logic [31:0]  in_c10,
    input  logic [31:0]  in_c11,
    input  logic [31:0]  in_c12,
    input  logic [31:0]  in_c13,
    input  logic [31:0]  in_c20,
    input  logic [31:0]  in_c21,
    input  logic [31:0]  in_c22,
    input  logic [31:0]  in_c23,
    input  logic [31:0]  in_c30,
    input  logic [31:0]  in_c31,
    input  logic [31:0]  in_c32,
    input  logic [31:0]  in_c33,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_c00,
    output logic [31:0]  out_c01,
    output logic [31:0]  out_c02,
    output logic [31:0]  out_c03,
    output logic [31:0]  out_c10,
    output logic [31:0]  out_c11,
    output logic [31:0]  out_c12,
    output logic [31:0]  out_c13,
    output logic [31:0]  out_c20,
    output logic [31:0]  out_c21,
    output logic [31:0]  out_c22,
    output logic [31:0]  out_c23,
    output logic [31:0]  out_c30,
    output logic [31:0]  out_c31,
    output logic [31:0]  out_c32,
    output logic [31:0]  out_c33
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [31:0] add32(input logic [31:0] a, input logic [31:0] b);
    logic [31:0] sum;
    begin
        sum = a + b;
        add32 = sum;
    end
endfunction

function automatic logic [31:0] sub32(input logic [31:0] a, input logic [31:0] b);
    logic [31:0] diff;
    begin
        diff = a - b;
        sub32 = diff;
    end
endfunction

function automatic logic [31:0] mul32(input logic [31:0] a, input logic [31:0] b);
    logic signed [31:0] as;
    logic signed [31:0] bs;
    logic signed [63:0] prod;
    begin
        as = a;
        bs = b;
        prod = as * bs;
        mul32 = prod[31:0];
    end
endfunction

always_ff @(posedge clk) begin
    logic [31:0] tau;
    logic [31:0] v0;
    logic [31:0] v1;
    logic [31:0] v2;
    logic [31:0] v3;

    logic [31:0] c00;
    logic [31:0] c01;
    logic [31:0] c02;
    logic [31:0] c03;
    logic [31:0] c10;
    logic [31:0] c11;
    logic [31:0] c12;
    logic [31:0] c13;
    logic [31:0] c20;
    logic [31:0] c21;
    logic [31:0] c22;
    logic [31:0] c23;
    logic [31:0] c30;
    logic [31:0] c31;
    logic [31:0] c32;
    logic [31:0] c33;

    logic [31:0] r00;
    logic [31:0] r01;
    logic [31:0] r02;
    logic [31:0] r03;
    logic [31:0] r10;
    logic [31:0] r11;
    logic [31:0] r12;
    logic [31:0] r13;
    logic [31:0] r20;
    logic [31:0] r21;
    logic [31:0] r22;
    logic [31:0] r23;
    logic [31:0] r30;
    logic [31:0] r31;
    logic [31:0] r32;
    logic [31:0] r33;

    logic [2:0] lastv;
    logic [2:0] lastc;

    logic col_nz0;
    logic col_nz1;
    logic col_nz2;
    logic col_nz3;
    logic row_nz0;
    logic row_nz1;
    logic row_nz2;
    logic row_nz3;

    logic [31:0] work0;
    logic [31:0] work1;
    logic [31:0] work2;
    logic [31:0] work3;

    logic [31:0] tau_v0;
    logic [31:0] tau_v1;
    logic [31:0] tau_v2;
    logic [31:0] tau_v3;

    if (rst) begin
        busy    <= 1'b0;
        out_c00 <= 32'h00000000;
        out_c01 <= 32'h00000000;
        out_c02 <= 32'h00000000;
        out_c03 <= 32'h00000000;
        out_c10 <= 32'h00000000;
        out_c11 <= 32'h00000000;
        out_c12 <= 32'h00000000;
        out_c13 <= 32'h00000000;
        out_c20 <= 32'h00000000;
        out_c21 <= 32'h00000000;
        out_c22 <= 32'h00000000;
        out_c23 <= 32'h00000000;
        out_c30 <= 32'h00000000;
        out_c31 <= 32'h00000000;
        out_c32 <= 32'h00000000;
        out_c33 <= 32'h00000000;
    end else if (input_accepted) begin
        tau = in_tau;
        v0  = in_v0;
        v1  = in_v1;
        v2  = in_v2;
        v3  = in_v3;

        c00 = in_c00; c01 = in_c01; c02 = in_c02; c03 = in_c03;
        c10 = in_c10; c11 = in_c11; c12 = in_c12; c13 = in_c13;
        c20 = in_c20; c21 = in_c21; c22 = in_c22; c23 = in_c23;
        c30 = in_c30; c31 = in_c31; c32 = in_c32; c33 = in_c33;

        r00 = c00; r01 = c01; r02 = c02; r03 = c03;
        r10 = c10; r11 = c11; r12 = c12; r13 = c13;
        r20 = c20; r21 = c21; r22 = c22; r23 = c23;
        r30 = c30; r31 = c31; r32 = c32; r33 = c33;

        lastv = 3'd0;
        if (v3 != 32'h00000000) begin
            lastv = 3'd4;
        end else if (v2 != 32'h00000000) begin
            lastv = 3'd3;
        end else if (v1 != 32'h00000000) begin
            lastv = 3'd2;
        end else if (v0 != 32'h00000000) begin
            lastv = 3'd1;
        end

        lastc = 3'd0;
        if ((tau != 32'h00000000) && (lastv != 3'd0)) begin
            if (in_side_left) begin
                col_nz0 = 1'b0;
                col_nz1 = 1'b0;
                col_nz2 = 1'b0;
                col_nz3 = 1'b0;

                if (lastv > 3'd0) begin
                    if (c00 != 32'h00000000) col_nz0 = 1'b1;
                    if (c01 != 32'h00000000) col_nz1 = 1'b1;
                    if (c02 != 32'h00000000) col_nz2 = 1'b1;
                    if (c03 != 32'h00000000) col_nz3 = 1'b1;
                end
                if (lastv > 3'd1) begin
                    if (c10 != 32'h00000000) col_nz0 = 1'b1;
                    if (c11 != 32'h00000000) col_nz1 = 1'b1;
                    if (c12 != 32'h00000000) col_nz2 = 1'b1;
                    if (c13 != 32'h00000000) col_nz3 = 1'b1;
                end
                if (lastv > 3'd2) begin
                    if (c20 != 32'h00000000) col_nz0 = 1'b1;
                    if (c21 != 32'h00000000) col_nz1 = 1'b1;
                    if (c22 != 32'h00000000) col_nz2 = 1'b1;
                    if (c23 != 32'h00000000) col_nz3 = 1'b1;
                end
                if (lastv > 3'd3) begin
                    if (c30 != 32'h00000000) col_nz0 = 1'b1;
                    if (c31 != 32'h00000000) col_nz1 = 1'b1;
                    if (c32 != 32'h00000000) col_nz2 = 1'b1;
                    if (c33 != 32'h00000000) col_nz3 = 1'b1;
                end

                if (col_nz3) lastc = 3'd4;
                else if (col_nz2) lastc = 3'd3;
                else if (col_nz1) lastc = 3'd2;
                else if (col_nz0) lastc = 3'd1;
            end else begin
                row_nz0 = 1'b0;
                row_nz1 = 1'b0;
                row_nz2 = 1'b0;
                row_nz3 = 1'b0;

                if (lastv > 3'd0) begin
                    if (c00 != 32'h00000000) row_nz0 = 1'b1;
                    if (c10 != 32'h00000000) row_nz1 = 1'b1;
                    if (c20 != 32'h00000000) row_nz2 = 1'b1;
                    if (c30 != 32'h00000000) row_nz3 = 1'b1;
                end
                if (lastv > 3'd1) begin
                    if (c01 != 32'h00000000) row_nz0 = 1'b1;
                    if (c11 != 32'h00000000) row_nz1 = 1'b1;
                    if (c21 != 32'h00000000) row_nz2 = 1'b1;
                    if (c31 != 32'h00000000) row_nz3 = 1'b1;
                end
                if (lastv > 3'd2) begin
                    if (c02 != 32'h00000000) row_nz0 = 1'b1;
                    if (c12 != 32'h00000000) row_nz1 = 1'b1;
                    if (c22 != 32'h00000000) row_nz2 = 1'b1;
                    if (c32 != 32'h00000000) row_nz3 = 1'b1;
                end
                if (lastv > 3'd3) begin
                    if (c03 != 32'h00000000) row_nz0 = 1'b1;
                    if (c13 != 32'h00000000) row_nz1 = 1'b1;
                    if (c23 != 32'h00000000) row_nz2 = 1'b1;
                    if (c33 != 32'h00000000) row_nz3 = 1'b1;
                end

                if (row_nz3) lastc = 3'd4;
                else if (row_nz2) lastc = 3'd3;
                else if (row_nz1) lastc = 3'd2;
                else if (row_nz0) lastc = 3'd1;
            end
        end

        if ((tau != 32'h00000000) && (lastv != 3'd0) && (lastc != 3'd0)) begin
            if (in_side_left) begin
                work0 = 32'h00000000;
                work1 = 32'h00000000;
                work2 = 32'h00000000;
                work3 = 32'h00000000;

                if (lastc > 3'd0) begin
                    work0 = mul32(c00, v0);
                    if (lastv > 3'd1) work0 = add32(work0, mul32(c10, v1));
                    if (lastv > 3'd2) work0 = add32(work0, mul32(c20, v2));
                    if (lastv > 3'd3) work0 = add32(work0, mul32(c30, v3));
                end
                if (lastc > 3'd1) begin
                    work1 = mul32(c01, v0);
                    if (lastv > 3'd1) work1 = add32(work1, mul32(c11, v1));
                    if (lastv > 3'd2) work1 = add32(work1, mul32(c21, v2));
                    if (lastv > 3'd3) work1 = add32(work1, mul32(c31, v3));
                end
                if (lastc > 3'd2) begin
                    work2 = mul32(c02, v0);
                    if (lastv > 3'd1) work2 = add32(work2, mul32(c12, v1));
                    if (lastv > 3'd2) work2 = add32(work2, mul32(c22, v2));
                    if (lastv > 3'd3) work2 = add32(work2, mul32(c32, v3));
                end
                if (lastc > 3'd3) begin
                    work3 = mul32(c03, v0);
                    if (lastv > 3'd1) work3 = add32(work3, mul32(c13, v1));
                    if (lastv > 3'd2) work3 = add32(work3, mul32(c23, v2));
                    if (lastv > 3'd3) work3 = add32(work3, mul32(c33, v3));
                end

                tau_v0 = 32'h00000000;
                tau_v1 = 32'h00000000;
                tau_v2 = 32'h00000000;
                tau_v3 = 32'h00000000;

                if (lastv > 3'd0) tau_v0 = mul32(tau, v0);
                if (lastv > 3'd1) tau_v1 = mul32(tau, v1);
                if (lastv > 3'd2) tau_v2 = mul32(tau, v2);
                if (lastv > 3'd3) tau_v3 = mul32(tau, v3);

                if ((lastv > 3'd0) && (lastc > 3'd0)) r00 = sub32(c00, mul32(tau_v0, work0));
                if ((lastv > 3'd0) && (lastc > 3'd1)) r01 = sub32(c01, mul32(tau_v0, work1));
                if ((lastv > 3'd0) && (lastc > 3'd2)) r02 = sub32(c02, mul32(tau_v0, work2));
                if ((lastv > 3'd0) && (lastc > 3'd3)) r03 = sub32(c03, mul32(tau_v0, work3));

                if ((lastv > 3'd1) && (lastc > 3'd0)) r10 = sub32(c10, mul32(tau_v1, work0));
                if ((lastv > 3'd1) && (lastc > 3'd1)) r11 = sub32(c11, mul32(tau_v1, work1));
                if ((lastv > 3'd1) && (lastc > 3'd2)) r12 = sub32(c12, mul32(tau_v1, work2));
                if ((lastv > 3'd1) && (lastc > 3'd3)) r13 = sub32(c13, mul32(tau_v1, work3));

                if ((lastv > 3'd2) && (lastc > 3'd0)) r20 = sub32(c20, mul32(tau_v2, work0));
                if ((lastv > 3'd2) && (lastc > 3'd1)) r21 = sub32(c21, mul32(tau_v2, work1));
                if ((lastv > 3'd2) && (lastc > 3'd2)) r22 = sub32(c22, mul32(tau_v2, work2));
                if ((lastv > 3'd2) && (lastc > 3'd3)) r23 = sub32(c23, mul32(tau_v2, work3));

                if ((lastv > 3'd3) && (lastc > 3'd0)) r30 = sub32(c30, mul32(tau_v3, work0));
                if ((lastv > 3'd3) && (lastc > 3'd1)) r31 = sub32(c31, mul32(tau_v3, work1));
                if ((lastv > 3'd3) && (lastc > 3'd2)) r32 = sub32(c32, mul32(tau_v3, work2));
                if ((lastv > 3'd3) && (lastc > 3'd3)) r33 = sub32(c33, mul32(tau_v3, work3));
            end else begin
                work0 = 32'h00000000;
                work1 = 32'h00000000;
                work2 = 32'h00000000;
                work3 = 32'h00000000;

                if (lastc > 3'd0) begin
                    work0 = mul32(c00, v0);
                    if (lastv > 3'd1) work0 = add32(work0, mul32(c01, v1));
                    if (lastv > 3'd2) work0 = add32(work0, mul32(c02, v2));
                    if (lastv > 3'd3) work0 = add32(work0, mul32(c03, v3));
                end
                if (lastc > 3'd1) begin
                    work1 = mul32(c10, v0);
                    if (lastv > 3'd1) work1 = add32(work1, mul32(c11, v1));
                    if (lastv > 3'd2) work1 = add32(work1, mul32(c12, v2));
                    if (lastv > 3'd3) work1 = add32(work1, mul32(c13, v3));
                end
                if (lastc > 3'd2) begin
                    work2 = mul32(c20, v0);
                    if (lastv > 3'd1) work2 = add32(work2, mul32(c21, v1));
                    if (lastv > 3'd2) work2 = add32(work2, mul32(c22, v2));
                    if (lastv > 3'd3) work2 = add32(work2, mul32(c23, v3));
                end
                if (lastc > 3'd3) begin
                    work3 = mul32(c30, v0);
                    if (lastv > 3'd1) work3 = add32(work3, mul32(c31, v1));
                    if (lastv > 3'd2) work3 = add32(work3, mul32(c32, v2));
                    if (lastv > 3'd3) work3 = add32(work3, mul32(c33, v3));
                end

                tau_v0 = 32'h00000000;
                tau_v1 = 32'h00000000;
                tau_v2 = 32'h00000000;
                tau_v3 = 32'h00000000;

                if (lastv > 3'd0) tau_v0 = mul32(tau, v0);
                if (lastv > 3'd1) tau_v1 = mul32(tau, v1);
                if (lastv > 3'd2) tau_v2 = mul32(tau, v2);
                if (lastv > 3'd3) tau_v3 = mul32(tau, v3);

                if ((lastc > 3'd0) && (lastv > 3'd0)) r00 = sub32(c00, mul32(work0, tau_v0));
                if ((lastc > 3'd0) && (lastv > 3'd1)) r01 = sub32(c01, mul32(work0, tau_v1));
                if ((lastc > 3'd0) && (lastv > 3'd2)) r02 = sub32(c02, mul32(work0, tau_v2));
                if ((lastc > 3'd0) && (lastv > 3'd3)) r03 = sub32(c03, mul32(work0, tau_v3));

                if ((lastc > 3'd1) && (lastv > 3'd0)) r10 = sub32(c10, mul32(work1, tau_v0));
                if ((lastc > 3'd1) && (lastv > 3'd1)) r11 = sub32(c11, mul32(work1, tau_v1));
                if ((lastc > 3'd1) && (lastv > 3'd2)) r12 = sub32(c12, mul32(work1, tau_v2));
                if ((lastc > 3'd1) && (lastv > 3'd3)) r13 = sub32(c13, mul32(work1, tau_v3));

                if ((lastc > 3'd2) && (lastv > 3'd0)) r20 = sub32(c20, mul32(work2, tau_v0));
                if ((lastc > 3'd2) && (lastv > 3'd1)) r21 = sub32(c21, mul32(work2, tau_v1));
                if ((lastc > 3'd2) && (lastv > 3'd2)) r22 = sub32(c22, mul32(work2, tau_v2));
                if ((lastc > 3'd2) && (lastv > 3'd3)) r23 = sub32(c23, mul32(work2, tau_v3));

                if ((lastc > 3'd3) && (lastv > 3'd0)) r30 = sub32(c30, mul32(work3, tau_v0));
                if ((lastc > 3'd3) && (lastv > 3'd1)) r31 = sub32(c31, mul32(work3, tau_v1));
                if ((lastc > 3'd3) && (lastv > 3'd2)) r32 = sub32(c32, mul32(work3, tau_v2));
                if ((lastc > 3'd3) && (lastv > 3'd3)) r33 = sub32(c33, mul32(work3, tau_v3));
            end
        end

        out_c00 <= r00;
        out_c01 <= r01;
        out_c02 <= r02;
        out_c03 <= r03;
        out_c10 <= r10;
        out_c11 <= r11;
        out_c12 <= r12;
        out_c13 <= r13;
        out_c20 <= r20;
        out_c21 <= r21;
        out_c22 <= r22;
        out_c23 <= r23;
        out_c30 <= r30;
        out_c31 <= r31;
        out_c32 <= r32;
        out_c33 <= r33;
        busy    <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
