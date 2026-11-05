module ormqr_reflector_step (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_side,
    input  logic [31:0] in_tau,
    input  logic [31:0] in_a1,
    input  logic [31:0] in_a2,
    input  logic [31:0] in_a3,
    input  logic [31:0] in_c00,
    input  logic [31:0] in_c01,
    input  logic [31:0] in_c02,
    input  logic [31:0] in_c03,
    input  logic [31:0] in_c10,
    input  logic [31:0] in_c11,
    input  logic [31:0] in_c12,
    input  logic [31:0] in_c13,
    input  logic [31:0] in_c20,
    input  logic [31:0] in_c21,
    input  logic [31:0] in_c22,
    input  logic [31:0] in_c23,
    input  logic [31:0] in_c30,
    input  logic [31:0] in_c31,
    input  logic [31:0] in_c32,
    input  logic [31:0] in_c33,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_c00,
    output logic [31:0] out_c01,
    output logic [31:0] out_c02,
    output logic [31:0] out_c03,
    output logic [31:0] out_c10,
    output logic [31:0] out_c11,
    output logic [31:0] out_c12,
    output logic [31:0] out_c13,
    output logic [31:0] out_c20,
    output logic [31:0] out_c21,
    output logic [31:0] out_c22,
    output logic [31:0] out_c23,
    output logic [31:0] out_c30,
    output logic [31:0] out_c31,
    output logic [31:0] out_c32,
    output logic [31:0] out_c33
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk) begin
        logic signed [31:0] tau;
        logic signed [31:0] v0, v1, v2, v3;
        logic signed [31:0] c00, c01, c02, c03;
        logic signed [31:0] c10, c11, c12, c13;
        logic signed [31:0] c20, c21, c22, c23;
        logic signed [31:0] c30, c31, c32, c33;

        logic signed [65:0] w0, w1, w2, w3;
        logic signed [98:0] res00, res01, res02, res03;
        logic signed [98:0] res10, res11, res12, res13;
        logic signed [98:0] res20, res21, res22, res23;
        logic signed [98:0] res30, res31, res32, res33;

        if (rst) begin
            busy    <= 1'b0;
            out_c00 <= 32'd0;
            out_c01 <= 32'd0;
            out_c02 <= 32'd0;
            out_c03 <= 32'd0;
            out_c10 <= 32'd0;
            out_c11 <= 32'd0;
            out_c12 <= 32'd0;
            out_c13 <= 32'd0;
            out_c20 <= 32'd0;
            out_c21 <= 32'd0;
            out_c22 <= 32'd0;
            out_c23 <= 32'd0;
            out_c30 <= 32'd0;
            out_c31 <= 32'd0;
            out_c32 <= 32'd0;
            out_c33 <= 32'd0;
        end else if (input_accepted) begin
            tau = $signed(in_tau);
            v0  = 32'sd1;
            v1  = $signed(in_a1);
            v2  = $signed(in_a2);
            v3  = $signed(in_a3);

            c00 = $signed(in_c00); c01 = $signed(in_c01); c02 = $signed(in_c02); c03 = $signed(in_c03);
            c10 = $signed(in_c10); c11 = $signed(in_c11); c12 = $signed(in_c12); c13 = $signed(in_c13);
            c20 = $signed(in_c20); c21 = $signed(in_c21); c22 = $signed(in_c22); c23 = $signed(in_c23);
            c30 = $signed(in_c30); c31 = $signed(in_c31); c32 = $signed(in_c32); c33 = $signed(in_c33);

            if (in_side == 1'b0) begin
                w0 = v0*c00 + v1*c10 + v2*c20 + v3*c30;
                w1 = v0*c01 + v1*c11 + v2*c21 + v3*c31;
                w2 = v0*c02 + v1*c12 + v2*c22 + v3*c32;
                w3 = v0*c03 + v1*c13 + v2*c23 + v3*c33;

                res00 = c00 - tau*v0*w0;
                res01 = c01 - tau*v0*w1;
                res02 = c02 - tau*v0*w2;
                res03 = c03 - tau*v0*w3;

                res10 = c10 - tau*v1*w0;
                res11 = c11 - tau*v1*w1;
                res12 = c12 - tau*v1*w2;
                res13 = c13 - tau*v1*w3;

                res20 = c20 - tau*v2*w0;
                res21 = c21 - tau*v2*w1;
                res22 = c22 - tau*v2*w2;
                res23 = c23 - tau*v2*w3;

                res30 = c30 - tau*v3*w0;
                res31 = c31 - tau*v3*w1;
                res32 = c32 - tau*v3*w2;
                res33 = c33 - tau*v3*w3;
            end else begin
                w0 = c00*v0 + c01*v1 + c02*v2 + c03*v3;
                w1 = c10*v0 + c11*v1 + c12*v2 + c13*v3;
                w2 = c20*v0 + c21*v1 + c22*v2 + c23*v3;
                w3 = c30*v0 + c31*v1 + c32*v2 + c33*v3;

                res00 = c00 - tau*w0*v0;
                res01 = c01 - tau*w0*v1;
                res02 = c02 - tau*w0*v2;
                res03 = c03 - tau*w0*v3;

                res10 = c10 - tau*w1*v0;
                res11 = c11 - tau*w1*v1;
                res12 = c12 - tau*w1*v2;
                res13 = c13 - tau*w1*v3;

                res20 = c20 - tau*w2*v0;
                res21 = c21 - tau*w2*v1;
                res22 = c22 - tau*w2*v2;
                res23 = c23 - tau*w2*v3;

                res30 = c30 - tau*w3*v0;
                res31 = c31 - tau*w3*v1;
                res32 = c32 - tau*w3*v2;
                res33 = c33 - tau*w3*v3;
            end

            out_c00 <= res00[31:0];
            out_c01 <= res01[31:0];
            out_c02 <= res02[31:0];
            out_c03 <= res03[31:0];
            out_c10 <= res10[31:0];
            out_c11 <= res11[31:0];
            out_c12 <= res12[31:0];
            out_c13 <= res13[31:0];
            out_c20 <= res20[31:0];
            out_c21 <= res21[31:0];
            out_c22 <= res22[31:0];
            out_c23 <= res23[31:0];
            out_c30 <= res30[31:0];
            out_c31 <= res31[31:0];
            out_c32 <= res32[31:0];
            out_c33 <= res33[31:0];

            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule