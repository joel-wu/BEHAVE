module lu_solve4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_a00,
    input  logic [15:0]  in_a01,
    input  logic [15:0]  in_a02,
    input  logic [15:0]  in_a03,
    input  logic [15:0]  in_a10,
    input  logic [15:0]  in_a11,
    input  logic [15:0]  in_a12,
    input  logic [15:0]  in_a13,
    input  logic [15:0]  in_a20,
    input  logic [15:0]  in_a21,
    input  logic [15:0]  in_a22,
    input  logic [15:0]  in_a23,
    input  logic [15:0]  in_a30,
    input  logic [15:0]  in_a31,
    input  logic [15:0]  in_a32,
    input  logic [15:0]  in_a33,
    input  logic [15:0]  in_b0,
    input  logic [15:0]  in_b1,
    input  logic [15:0]  in_b2,
    input  logic [15:0]  in_b3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_x0,
    output logic [15:0]  out_x1,
    output logic [15:0]  out_x2,
    output logic [15:0]  out_x3,
    output logic [2:0]   out_info
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;
assign in_ready        = ~busy | output_consumed;
assign out_valid       = busy;

logic signed [15:0] M [0:3][0:3];
logic signed [15:0] rhs [0:3];
logic [1:0] ipiv [0:3];
logic signed [15:0] y [0:3];
logic signed [15:0] x [0:3];
logic [2:0] info_reg;

function automatic logic signed [15:0] wrap16(input logic signed [31:0] v);
    logic signed [31:0] tmp;
    begin
        tmp = v;
        wrap16 = tmp[15:0];
    end
endfunction

function automatic logic signed [31:0] abs32(input logic signed [31:0] v);
    begin
        if (v < 0)
            abs32 = -v;
        else
            abs32 = v;
    end
endfunction

function automatic logic signed [31:0] trunc_div_toward_zero_32(
    input logic signed [31:0] n,
    input logic signed [31:0] d
);
    logic signed [31:0] an;
    logic signed [31:0] ad;
    logic signed [31:0] q;
    begin
        if (n < 0)
            an = -n;
        else
            an = n;

        if (d < 0)
            ad = -d;
        else
            ad = d;

        q = an / ad;

        if ((n < 0) ^ (d < 0))
            trunc_div_toward_zero_32 = -q;
        else
            trunc_div_toward_zero_32 = q;
    end
endfunction

function automatic logic signed [15:0] mul_q8_8(
    input logic signed [15:0] p,
    input logic signed [15:0] q
);
    logic signed [31:0] prod;
    logic signed [31:0] quot;
    begin
        prod = p * q;
        quot = trunc_div_toward_zero_32(prod, 32'sd256);
        mul_q8_8 = wrap16(quot);
    end
endfunction

function automatic logic signed [15:0] div_q8_8(
    input logic signed [15:0] n,
    input logic signed [15:0] d
);
    logic signed [31:0] num;
    logic signed [31:0] den;
    logic signed [31:0] quot;
    begin
        num = n * 32'sd256;
        den = d;
        quot = trunc_div_toward_zero_32(num, den);
        div_q8_8 = wrap16(quot);
    end
endfunction

integer i;
integer j;
integer k;
integer p;
integer ii;
logic signed [31:0] best_abs;
logic signed [31:0] mag_abs;
logic signed [15:0] best_val;
logic signed [15:0] t16;
logic signed [15:0] t2;
logic signed [15:0] t1;
logic signed [15:0] t0;
logic signed [15:0] row_tmp0;
logic signed [15:0] row_tmp1;
logic signed [15:0] row_tmp2;
logic signed [15:0] row_tmp3;
logic signed [15:0] rhs_tmp;
logic signed [15:0] matrix_local [0:3][0:3];
logic signed [15:0] rhs_local [0:3];
logic [1:0] ipiv_local [0:3];
logic signed [15:0] y_local [0:3];
logic signed [15:0] x_local [0:3];
logic [2:0] info_local;

always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;
        out_x0 <= 16'h0000;
        out_x1 <= 16'h0000;
        out_x2 <= 16'h0000;
        out_x3 <= 16'h0000;
        out_info <= 3'b000;
        info_reg <= 3'b000;
        for (i = 0; i < 4; i = i + 1) begin
            rhs[i] <= 16'sd0;
            y[i] <= 16'sd0;
            x[i] <= 16'sd0;
            ipiv[i] <= 2'd0;
            for (j = 0; j < 4; j = j + 1) begin
                M[i][j] <= 16'sd0;
            end
        end
    end else if (input_accepted) begin
        matrix_local[0][0] = $signed(in_a00);
        matrix_local[0][1] = $signed(in_a01);
        matrix_local[0][2] = $signed(in_a02);
        matrix_local[0][3] = $signed(in_a03);
        matrix_local[1][0] = $signed(in_a10);
        matrix_local[1][1] = $signed(in_a11);
        matrix_local[1][2] = $signed(in_a12);
        matrix_local[1][3] = $signed(in_a13);
        matrix_local[2][0] = $signed(in_a20);
        matrix_local[2][1] = $signed(in_a21);
        matrix_local[2][2] = $signed(in_a22);
        matrix_local[2][3] = $signed(in_a23);
        matrix_local[3][0] = $signed(in_a30);
        matrix_local[3][1] = $signed(in_a31);
        matrix_local[3][2] = $signed(in_a32);
        matrix_local[3][3] = $signed(in_a33);

        rhs_local[0] = $signed(in_b0);
        rhs_local[1] = $signed(in_b1);
        rhs_local[2] = $signed(in_b2);
        rhs_local[3] = $signed(in_b3);

        ipiv_local[0] = 2'd0;
        ipiv_local[1] = 2'd0;
        ipiv_local[2] = 2'd0;
        ipiv_local[3] = 2'd0;

        y_local[0] = 16'sd0;
        y_local[1] = 16'sd0;
        y_local[2] = 16'sd0;
        y_local[3] = 16'sd0;

        x_local[0] = 16'sd0;
        x_local[1] = 16'sd0;
        x_local[2] = 16'sd0;
        x_local[3] = 16'sd0;

        info_local = 3'd0;

        for (k = 0; k < 4; k = k + 1) begin
            if (info_local == 3'd0) begin
                p = k;
                best_val = matrix_local[k][k];
                best_abs = abs32(best_val);
                for (ii = k + 1; ii < 4; ii = ii + 1) begin
                    mag_abs = abs32(matrix_local[ii][k]);
                    if (mag_abs > best_abs) begin
                        p = ii;
                        best_abs = mag_abs;
                    end
                end
                ipiv_local[k] = p[1:0];

                if (p != k) begin
                    row_tmp0 = matrix_local[k][0];
                    row_tmp1 = matrix_local[k][1];
                    row_tmp2 = matrix_local[k][2];
                    row_tmp3 = matrix_local[k][3];

                    matrix_local[k][0] = matrix_local[p][0];
                    matrix_local[k][1] = matrix_local[p][1];
                    matrix_local[k][2] = matrix_local[p][2];
                    matrix_local[k][3] = matrix_local[p][3];

                    matrix_local[p][0] = row_tmp0;
                    matrix_local[p][1] = row_tmp1;
                    matrix_local[p][2] = row_tmp2;
                    matrix_local[p][3] = row_tmp3;

                    rhs_tmp = rhs_local[k];
                    rhs_local[k] = rhs_local[p];
                    rhs_local[p] = rhs_tmp;
                end

                if (matrix_local[k][k] == 16'sd0) begin
                    info_local = k + 1;
                end else begin
                    for (i = k + 1; i < 4; i = i + 1) begin
                        matrix_local[i][k] = div_q8_8(matrix_local[i][k], matrix_local[k][k]);
                        for (j = k + 1; j < 4; j = j + 1) begin
                            matrix_local[i][j] = wrap16(matrix_local[i][j] - mul_q8_8(matrix_local[i][k], matrix_local[k][j]));
                        end
                    end
                end
            end
        end

        if (info_local == 3'd0) begin
            y_local[0] = wrap16(rhs_local[0]);
            y_local[1] = wrap16(rhs_local[1] - mul_q8_8(matrix_local[1][0], y_local[0]));
            y_local[2] = wrap16(rhs_local[2] - mul_q8_8(matrix_local[2][0], y_local[0]) - mul_q8_8(matrix_local[2][1], y_local[1]));
            y_local[3] = wrap16(rhs_local[3] - mul_q8_8(matrix_local[3][0], y_local[0]) - mul_q8_8(matrix_local[3][1], y_local[1]) - mul_q8_8(matrix_local[3][2], y_local[2]));

            if (matrix_local[3][3] == 16'sd0) begin
                info_local = 3'd4;
            end else begin
                x_local[3] = div_q8_8(y_local[3], matrix_local[3][3]);
                t2 = wrap16(y_local[2] - mul_q8_8(matrix_local[2][3], x_local[3]));
                if (matrix_local[2][2] == 16'sd0) begin
                    info_local = 3'd3;
                end else begin
                    x_local[2] = div_q8_8(t2, matrix_local[2][2]);
                    t1 = wrap16(y_local[1] - mul_q8_8(matrix_local[1][2], x_local[2]) - mul_q8_8(matrix_local[1][3], x_local[3]));
                    if (matrix_local[1][1] == 16'sd0) begin
                        info_local = 3'd2;
                    end else begin
                        x_local[1] = div_q8_8(t1, matrix_local[1][1]);
                        t0 = wrap16(y_local[0] - mul_q8_8(matrix_local[0][1], x_local[1]) - mul_q8_8(matrix_local[0][2], x_local[2]) - mul_q8_8(matrix_local[0][3], x_local[3]));
                        if (matrix_local[0][0] == 16'sd0) begin
                            info_local = 3'd1;
                        end else begin
                            x_local[0] = div_q8_8(t0, matrix_local[0][0]);
                        end
                    end
                end
            end
        end

        for (i = 0; i < 4; i = i + 1) begin
            rhs[i] <= rhs_local[i];
            y[i] <= y_local[i];
            x[i] <= x_local[i];
            ipiv[i] <= ipiv_local[i];
            for (j = 0; j < 4; j = j + 1) begin
                M[i][j] <= matrix_local[i][j];
            end
        end
        info_reg <= info_local;

        if (info_local != 3'd0) begin
            out_x0 <= 16'h0000;
            out_x1 <= 16'h0000;
            out_x2 <= 16'h0000;
            out_x3 <= 16'h0000;
            out_info <= info_local;
        end else begin
            out_x0 <= x_local[0][15:0];
            out_x1 <= x_local[1][15:0];
            out_x2 <= x_local[2][15:0];
            out_x3 <= x_local[3][15:0];
            out_info <= 3'b000;
        end

        busy <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule