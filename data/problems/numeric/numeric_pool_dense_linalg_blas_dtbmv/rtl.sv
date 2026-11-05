module triangular_band_matrix_vector_step (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_uplo,
    input  logic        in_trans,
    input  logic        in_diag,
    input  logic [63:0] in_a00,
    input  logic [63:0] in_a01,
    input  logic [63:0] in_a10,
    input  logic [63:0] in_a11,
    input  logic [63:0] in_a20,
    input  logic [63:0] in_a21,
    input  logic [63:0] in_a30,
    input  logic [63:0] in_a31,
    input  logic [63:0] in_x0,
    input  logic [63:0] in_x1,
    input  logic [63:0] in_x2,
    input  logic [63:0] in_x3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_x0,
    output logic [63:0] out_x1,
    output logic [63:0] out_x2,
    output logic [63:0] out_x3
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [63:0] wrap_add_mul(
    input logic signed [63:0] a,
    input logic signed [63:0] b,
    input logic signed [63:0] c
);
    logic signed [127:0] prod;
    logic signed [127:0] sum;
begin
    prod = b * c;
    sum  = a + prod;
    wrap_add_mul = sum[63:0];
end
endfunction

function automatic logic signed [63:0] wrap_mul(
    input logic signed [63:0] a,
    input logic signed [63:0] b
);
    logic signed [127:0] prod;
begin
    prod = a * b;
    wrap_mul = prod[63:0];
end
endfunction

logic signed [63:0] a0_0, a0_1, a0_2, a0_3;
logic signed [63:0] a1_0, a1_1, a1_2, a1_3;
logic signed [63:0] x0_s, x1_s, x2_s, x3_s;

logic signed [63:0] nx0, nx1, nx2, nx3;
logic signed [63:0] t0, t1, t2, t3;

always_comb begin
    a0_0 = in_a00;
    a0_1 = in_a10;
    a0_2 = in_a20;
    a0_3 = in_a30;
    a1_0 = in_a01;
    a1_1 = in_a11;
    a1_2 = in_a21;
    a1_3 = in_a31;

    x0_s = in_x0;
    x1_s = in_x1;
    x2_s = in_x2;
    x3_s = in_x3;

    nx0 = x0_s;
    nx1 = x1_s;
    nx2 = x2_s;
    nx3 = x3_s;

    t0 = 64'sd0;
    t1 = 64'sd0;
    t2 = 64'sd0;
    t3 = 64'sd0;

    if (in_trans == 1'b0) begin
        if (in_uplo != 1'b0) begin
            if (nx0 != 64'sd0) begin
                if (in_diag == 1'b0) begin
                    nx0 = wrap_mul(nx0, a1_0);
                end
            end

            if (nx1 != 64'sd0) begin
                t1  = nx1;
                nx0 = wrap_add_mul(nx0, t1, a0_1);
                if (in_diag == 1'b0) begin
                    nx1 = wrap_mul(nx1, a1_1);
                end
            end

            if (nx2 != 64'sd0) begin
                t2  = nx2;
                nx1 = wrap_add_mul(nx1, t2, a0_2);
                if (in_diag == 1'b0) begin
                    nx2 = wrap_mul(nx2, a1_2);
                end
            end

            if (nx3 != 64'sd0) begin
                t3  = nx3;
                nx2 = wrap_add_mul(nx2, t3, a0_3);
                if (in_diag == 1'b0) begin
                    nx3 = wrap_mul(nx3, a1_3);
                end
            end
        end else begin
            if (nx3 != 64'sd0) begin
                if (in_diag == 1'b0) begin
                    nx3 = wrap_mul(nx3, a0_3);
                end
            end

            if (nx2 != 64'sd0) begin
                t2  = nx2;
                nx3 = wrap_add_mul(nx3, t2, a1_2);
                if (in_diag == 1'b0) begin
                    nx2 = wrap_mul(nx2, a0_2);
                end
            end

            if (nx1 != 64'sd0) begin
                t1  = nx1;
                nx2 = wrap_add_mul(nx2, t1, a1_1);
                if (in_diag == 1'b0) begin
                    nx1 = wrap_mul(nx1, a0_1);
                end
            end

            if (nx0 != 64'sd0) begin
                t0  = nx0;
                nx1 = wrap_add_mul(nx1, t0, a1_0);
                if (in_diag == 1'b0) begin
                    nx0 = wrap_mul(nx0, a0_0);
                end
            end
        end
    end else begin
        if (in_uplo != 1'b0) begin
            t3 = nx3;
            if (in_diag == 1'b0) begin
                t3 = wrap_mul(t3, a1_3);
            end
            t3  = wrap_add_mul(t3, a0_3, nx2);
            nx3 = t3;

            t2 = nx2;
            if (in_diag == 1'b0) begin
                t2 = wrap_mul(t2, a1_2);
            end
            t2  = wrap_add_mul(t2, a0_2, nx1);
            nx2 = t2;

            t1 = nx1;
            if (in_diag == 1'b0) begin
                t1 = wrap_mul(t1, a1_1);
            end
            t1  = wrap_add_mul(t1, a0_1, nx0);
            nx1 = t1;

            t0 = nx0;
            if (in_diag == 1'b0) begin
                t0 = wrap_mul(t0, a1_0);
            end
            nx0 = t0;
        end else begin
            t0 = nx0;
            if (in_diag == 1'b0) begin
                t0 = wrap_mul(t0, a0_0);
            end
            t0  = wrap_add_mul(t0, a1_0, nx1);
            nx0 = t0;

            t1 = nx1;
            if (in_diag == 1'b0) begin
                t1 = wrap_mul(t1, a0_1);
            end
            t1  = wrap_add_mul(t1, a1_1, nx2);
            nx1 = t1;

            t2 = nx2;
            if (in_diag == 1'b0) begin
                t2 = wrap_mul(t2, a0_2);
            end
            t2  = wrap_add_mul(t2, a1_2, nx3);
            nx2 = t2;

            t3 = nx3;
            if (in_diag == 1'b0) begin
                t3 = wrap_mul(t3, a0_3);
            end
            nx3 = t3;
        end
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy    <= 1'b0;
        out_x0  <= 64'h0000000000000000;
        out_x1  <= 64'h0000000000000000;
        out_x2  <= 64'h0000000000000000;
        out_x3  <= 64'h0000000000000000;
    end else if (input_accepted) begin
        busy    <= 1'b1;
        out_x0  <= nx0;
        out_x1  <= nx1;
        out_x2  <= nx2;
        out_x3  <= nx3;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule