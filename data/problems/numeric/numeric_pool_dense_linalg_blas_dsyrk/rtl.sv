module symmetric_rank_k4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_uplo,
    input  logic         in_trans,
    input  logic signed [31:0] in_alpha,
    input  logic signed [31:0] in_beta,

    input  logic signed [31:0] in_a00,
    input  logic signed [31:0] in_a01,
    input  logic signed [31:0] in_a02,
    input  logic signed [31:0] in_a03,
    input  logic signed [31:0] in_a10,
    input  logic signed [31:0] in_a11,
    input  logic signed [31:0] in_a12,
    input  logic signed [31:0] in_a13,
    input  logic signed [31:0] in_a20,
    input  logic signed [31:0] in_a21,
    input  logic signed [31:0] in_a22,
    input  logic signed [31:0] in_a23,
    input  logic signed [31:0] in_a30,
    input  logic signed [31:0] in_a31,
    input  logic signed [31:0] in_a32,
    input  logic signed [31:0] in_a33,

    input  logic signed [31:0] in_c00,
    input  logic signed [31:0] in_c01,
    input  logic signed [31:0] in_c02,
    input  logic signed [31:0] in_c03,
    input  logic signed [31:0] in_c10,
    input  logic signed [31:0] in_c11,
    input  logic signed [31:0] in_c12,
    input  logic signed [31:0] in_c13,
    input  logic signed [31:0] in_c20,
    input  logic signed [31:0] in_c21,
    input  logic signed [31:0] in_c22,
    input  logic signed [31:0] in_c23,
    input  logic signed [31:0] in_c30,
    input  logic signed [31:0] in_c31,
    input  logic signed [31:0] in_c32,
    input  logic signed [31:0] in_c33,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [31:0] out_c00,
    output logic signed [31:0] out_c01,
    output logic signed [31:0] out_c02,
    output logic signed [31:0] out_c03,
    output logic signed [31:0] out_c10,
    output logic signed [31:0] out_c11,
    output logic signed [31:0] out_c12,
    output logic signed [31:0] out_c13,
    output logic signed [31:0] out_c20,
    output logic signed [31:0] out_c21,
    output logic signed [31:0] out_c22,
    output logic signed [31:0] out_c23,
    output logic signed [31:0] out_c30,
    output logic signed [31:0] out_c31,
    output logic signed [31:0] out_c32,
    output logic signed [31:0] out_c33
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [31:0] low32(input logic signed [127:0] value);
    logic signed [127:0] masked;
    begin
        masked = value;
        low32 = masked[31:0];
    end
endfunction

function automatic logic signed [31:0] dsyrk_elem_notrans(
    input logic signed [31:0] alpha,
    input logic signed [31:0] beta,
    input logic signed [31:0] ai0,
    input logic signed [31:0] ai1,
    input logic signed [31:0] ai2,
    input logic signed [31:0] ai3,
    input logic signed [31:0] aj0,
    input logic signed [31:0] aj1,
    input logic signed [31:0] aj2,
    input logic signed [31:0] aj3,
    input logic signed [31:0] cij
);
    logic signed [63:0]  temp0;
    logic signed [63:0]  prod1;
    logic signed [63:0]  prod2;
    logic signed [63:0]  prod3;
    logic signed [64:0]  temp1;
    logic signed [65:0]  temp2;
    logic signed [66:0]  temp3;
    logic signed [98:0]  alpha_term;
    logic signed [63:0]  beta_term;
    logic signed [99:0]  value_full;
    begin
        temp0      = ai0 * aj0;
        prod1      = ai1 * aj1;
        prod2      = ai2 * aj2;
        prod3      = ai3 * aj3;
        temp1      = temp0 + prod1;
        temp2      = temp1 + prod2;
        temp3      = temp2 + prod3;
        alpha_term = alpha * temp3;
        beta_term  = beta * cij;
        value_full = alpha_term + beta_term;
        dsyrk_elem_notrans = low32(value_full);
    end
endfunction

function automatic logic signed [31:0] dsyrk_elem_trans(
    input logic signed [31:0] alpha,
    input logic signed [31:0] beta,
    input logic signed [31:0] a0i,
    input logic signed [31:0] a1i,
    input logic signed [31:0] a2i,
    input logic signed [31:0] a3i,
    input logic signed [31:0] a0j,
    input logic signed [31:0] a1j,
    input logic signed [31:0] a2j,
    input logic signed [31:0] a3j,
    input logic signed [31:0] cij
);
    logic signed [63:0]  temp0;
    logic signed [63:0]  prod1;
    logic signed [63:0]  prod2;
    logic signed [63:0]  prod3;
    logic signed [64:0]  temp1;
    logic signed [65:0]  temp2;
    logic signed [66:0]  temp3;
    logic signed [98:0]  alpha_term;
    logic signed [63:0]  beta_term;
    logic signed [99:0]  value_full;
    begin
        temp0      = a0i * a0j;
        prod1      = a1i * a1j;
        prod2      = a2i * a2j;
        prod3      = a3i * a3j;
        temp1      = temp0 + prod1;
        temp2      = temp1 + prod2;
        temp3      = temp2 + prod3;
        alpha_term = alpha * temp3;
        beta_term  = beta * cij;
        value_full = alpha_term + beta_term;
        dsyrk_elem_trans = low32(value_full);
    end
endfunction

always_ff @(posedge clk) begin
    if (rst) begin
        busy    <= 1'b0;
        out_c00 <= '0;
        out_c01 <= '0;
        out_c02 <= '0;
        out_c03 <= '0;
        out_c10 <= '0;
        out_c11 <= '0;
        out_c12 <= '0;
        out_c13 <= '0;
        out_c20 <= '0;
        out_c21 <= '0;
        out_c22 <= '0;
        out_c23 <= '0;
        out_c30 <= '0;
        out_c31 <= '0;
        out_c32 <= '0;
        out_c33 <= '0;
    end else if (input_accepted) begin
        busy <= 1'b1;

        if (in_uplo) begin
            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c00 <= 32'sd0;
                else                   out_c00 <= low32(in_beta * in_c00);
            end else if (!in_trans) begin
                out_c00 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a00, in_a01, in_a02, in_a03,
                                              in_a00, in_a01, in_a02, in_a03,
                                              in_c00);
            end else begin
                out_c00 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a00, in_a10, in_a20, in_a30,
                                            in_a00, in_a10, in_a20, in_a30,
                                            in_c00);
            end

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c01 <= 32'sd0;
                else                   out_c01 <= low32(in_beta * in_c01);
            end else if (!in_trans) begin
                out_c01 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a00, in_a01, in_a02, in_a03,
                                              in_a10, in_a11, in_a12, in_a13,
                                              in_c01);
            end else begin
                out_c01 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a00, in_a10, in_a20, in_a30,
                                            in_a01, in_a11, in_a21, in_a31,
                                            in_c01);
            end

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c02 <= 32'sd0;
                else                   out_c02 <= low32(in_beta * in_c02);
            end else if (!in_trans) begin
                out_c02 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a00, in_a01, in_a02, in_a03,
                                              in_a20, in_a21, in_a22, in_a23,
                                              in_c02);
            end else begin
                out_c02 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a00, in_a10, in_a20, in_a30,
                                            in_a02, in_a12, in_a22, in_a32,
                                            in_c02);
            end

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c03 <= 32'sd0;
                else                   out_c03 <= low32(in_beta * in_c03);
            end else if (!in_trans) begin
                out_c03 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a00, in_a01, in_a02, in_a03,
                                              in_a30, in_a31, in_a32, in_a33,
                                              in_c03);
            end else begin
                out_c03 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a00, in_a10, in_a20, in_a30,
                                            in_a03, in_a13, in_a23, in_a33,
                                            in_c03);
            end

            out_c10 <= in_c10;

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c11 <= 32'sd0;
                else                   out_c11 <= low32(in_beta * in_c11);
            end else if (!in_trans) begin
                out_c11 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a10, in_a11, in_a12, in_a13,
                                              in_a10, in_a11, in_a12, in_a13,
                                              in_c11);
            end else begin
                out_c11 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a01, in_a11, in_a21, in_a31,
                                            in_a01, in_a11, in_a21, in_a31,
                                            in_c11);
            end

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c12 <= 32'sd0;
                else                   out_c12 <= low32(in_beta * in_c12);
            end else if (!in_trans) begin
                out_c12 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a10, in_a11, in_a12, in_a13,
                                              in_a20, in_a21, in_a22, in_a23,
                                              in_c12);
            end else begin
                out_c12 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a01, in_a11, in_a21, in_a31,
                                            in_a02, in_a12, in_a22, in_a32,
                                            in_c12);
            end

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c13 <= 32'sd0;
                else                   out_c13 <= low32(in_beta * in_c13);
            end else if (!in_trans) begin
                out_c13 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a10, in_a11, in_a12, in_a13,
                                              in_a30, in_a31, in_a32, in_a33,
                                              in_c13);
            end else begin
                out_c13 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a01, in_a11, in_a21, in_a31,
                                            in_a03, in_a13, in_a23, in_a33,
                                            in_c13);
            end

            out_c20 <= in_c20;
            out_c21 <= in_c21;

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c22 <= 32'sd0;
                else                   out_c22 <= low32(in_beta * in_c22);
            end else if (!in_trans) begin
                out_c22 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a20, in_a21, in_a22, in_a23,
                                              in_a20, in_a21, in_a22, in_a23,
                                              in_c22);
            end else begin
                out_c22 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a02, in_a12, in_a22, in_a32,
                                            in_a02, in_a12, in_a22, in_a32,
                                            in_c22);
            end

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c23 <= 32'sd0;
                else                   out_c23 <= low32(in_beta * in_c23);
            end else if (!in_trans) begin
                out_c23 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a20, in_a21, in_a22, in_a23,
                                              in_a30, in_a31, in_a32, in_a33,
                                              in_c23);
            end else begin
                out_c23 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a02, in_a12, in_a22, in_a32,
                                            in_a03, in_a13, in_a23, in_a33,
                                            in_c23);
            end

            out_c30 <= in_c30;
            out_c31 <= in_c31;
            out_c32 <= in_c32;

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c33 <= 32'sd0;
                else                   out_c33 <= low32(in_beta * in_c33);
            end else if (!in_trans) begin
                out_c33 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a30, in_a31, in_a32, in_a33,
                                              in_a30, in_a31, in_a32, in_a33,
                                              in_c33);
            end else begin
                out_c33 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a03, in_a13, in_a23, in_a33,
                                            in_a03, in_a13, in_a23, in_a33,
                                            in_c33);
            end
        end else begin
            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c00 <= 32'sd0;
                else                   out_c00 <= low32(in_beta * in_c00);
            end else if (!in_trans) begin
                out_c00 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a00, in_a01, in_a02, in_a03,
                                              in_a00, in_a01, in_a02, in_a03,
                                              in_c00);
            end else begin
                out_c00 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a00, in_a10, in_a20, in_a30,
                                            in_a00, in_a10, in_a20, in_a30,
                                            in_c00);
            end

            out_c01 <= in_c01;
            out_c02 <= in_c02;
            out_c03 <= in_c03;

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c10 <= 32'sd0;
                else                   out_c10 <= low32(in_beta * in_c10);
            end else if (!in_trans) begin
                out_c10 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a10, in_a11, in_a12, in_a13,
                                              in_a00, in_a01, in_a02, in_a03,
                                              in_c10);
            end else begin
                out_c10 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a01, in_a11, in_a21, in_a31,
                                            in_a00, in_a10, in_a20, in_a30,
                                            in_c10);
            end

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c11 <= 32'sd0;
                else                   out_c11 <= low32(in_beta * in_c11);
            end else if (!in_trans) begin
                out_c11 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a10, in_a11, in_a12, in_a13,
                                              in_a10, in_a11, in_a12, in_a13,
                                              in_c11);
            end else begin
                out_c11 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a01, in_a11, in_a21, in_a31,
                                            in_a01, in_a11, in_a21, in_a31,
                                            in_c11);
            end

            out_c12 <= in_c12;
            out_c13 <= in_c13;

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c20 <= 32'sd0;
                else                   out_c20 <= low32(in_beta * in_c20);
            end else if (!in_trans) begin
                out_c20 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a20, in_a21, in_a22, in_a23,
                                              in_a00, in_a01, in_a02, in_a03,
                                              in_c20);
            end else begin
                out_c20 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a02, in_a12, in_a22, in_a32,
                                            in_a00, in_a10, in_a20, in_a30,
                                            in_c20);
            end

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c21 <= 32'sd0;
                else                   out_c21 <= low32(in_beta * in_c21);
            end else if (!in_trans) begin
                out_c21 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a20, in_a21, in_a22, in_a23,
                                              in_a10, in_a11, in_a12, in_a13,
                                              in_c21);
            end else begin
                out_c21 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a02, in_a12, in_a22, in_a32,
                                            in_a01, in_a11, in_a21, in_a31,
                                            in_c21);
            end

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c22 <= 32'sd0;
                else                   out_c22 <= low32(in_beta * in_c22);
            end else if (!in_trans) begin
                out_c22 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a20, in_a21, in_a22, in_a23,
                                              in_a20, in_a21, in_a22, in_a23,
                                              in_c22);
            end else begin
                out_c22 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a02, in_a12, in_a22, in_a32,
                                            in_a02, in_a12, in_a22, in_a32,
                                            in_c22);
            end

            out_c23 <= in_c23;

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c30 <= 32'sd0;
                else                   out_c30 <= low32(in_beta * in_c30);
            end else if (!in_trans) begin
                out_c30 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a30, in_a31, in_a32, in_a33,
                                              in_a00, in_a01, in_a02, in_a03,
                                              in_c30);
            end else begin
                out_c30 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a03, in_a13, in_a23, in_a33,
                                            in_a00, in_a10, in_a20, in_a30,
                                            in_c30);
            end

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c31 <= 32'sd0;
                else                   out_c31 <= low32(in_beta * in_c31);
            end else if (!in_trans) begin
                out_c31 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a30, in_a31, in_a32, in_a33,
                                              in_a10, in_a11, in_a12, in_a13,
                                              in_c31);
            end else begin
                out_c31 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a03, in_a13, in_a23, in_a33,
                                            in_a01, in_a11, in_a21, in_a31,
                                            in_c31);
            end

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c32 <= 32'sd0;
                else                   out_c32 <= low32(in_beta * in_c32);
            end else if (!in_trans) begin
                out_c32 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a30, in_a31, in_a32, in_a33,
                                              in_a20, in_a21, in_a22, in_a23,
                                              in_c32);
            end else begin
                out_c32 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a03, in_a13, in_a23, in_a33,
                                            in_a02, in_a12, in_a22, in_a32,
                                            in_c32);
            end

            if (in_alpha == 32'sd0) begin
                if (in_beta == 32'sd0) out_c33 <= 32'sd0;
                else                   out_c33 <= low32(in_beta * in_c33);
            end else if (!in_trans) begin
                out_c33 <= dsyrk_elem_notrans(in_alpha, in_beta,
                                              in_a30, in_a31, in_a32, in_a33,
                                              in_a30, in_a31, in_a32, in_a33,
                                              in_c33);
            end else begin
                out_c33 <= dsyrk_elem_trans(in_alpha, in_beta,
                                            in_a03, in_a13, in_a23, in_a33,
                                            in_a03, in_a13, in_a23, in_a33,
                                            in_c33);
            end
        end
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule