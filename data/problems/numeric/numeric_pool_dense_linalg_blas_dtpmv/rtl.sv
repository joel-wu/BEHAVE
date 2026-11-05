module triangular_packed_matrix_vector4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_uplo,
    input  logic        in_trans,
    input  logic        in_diag,
    input  logic [63:0] in_ap0,
    input  logic [63:0] in_ap1,
    input  logic [63:0] in_ap2,
    input  logic [63:0] in_ap3,
    input  logic [63:0] in_ap4,
    input  logic [63:0] in_ap5,
    input  logic [63:0] in_ap6,
    input  logic [63:0] in_ap7,
    input  logic [63:0] in_ap8,
    input  logic [63:0] in_ap9,
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
wire  output_consumed;
wire  input_accepted;

assign output_consumed = out_valid & out_ready;
assign input_accepted  = in_valid & in_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [63:0] wrap_addmul(
    input logic signed [63:0] a,
    input logic signed [63:0] b,
    input logic signed [63:0] c
);
    logic signed [127:0] prod;
    logic signed [127:0] sum;
begin
    prod = b * c;
    sum  = a + prod;
    wrap_addmul = sum[63:0];
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

logic signed [63:0] x0_s, x1_s, x2_s, x3_s;
logic signed [63:0] ap0_s, ap1_s, ap2_s, ap3_s, ap4_s, ap5_s, ap6_s, ap7_s, ap8_s, ap9_s;
logic signed [63:0] nx0, nx1, nx2, nx3;
logic signed [63:0] temp0, temp1, temp2, temp3;

always_comb begin
    ap0_s = $signed(in_ap0);
    ap1_s = $signed(in_ap1);
    ap2_s = $signed(in_ap2);
    ap3_s = $signed(in_ap3);
    ap4_s = $signed(in_ap4);
    ap5_s = $signed(in_ap5);
    ap6_s = $signed(in_ap6);
    ap7_s = $signed(in_ap7);
    ap8_s = $signed(in_ap8);
    ap9_s = $signed(in_ap9);

    x0_s = $signed(in_x0);
    x1_s = $signed(in_x1);
    x2_s = $signed(in_x2);
    x3_s = $signed(in_x3);

    nx0 = x0_s;
    nx1 = x1_s;
    nx2 = x2_s;
    nx3 = x3_s;

    temp0 = 64'sd0;
    temp1 = 64'sd0;
    temp2 = 64'sd0;
    temp3 = 64'sd0;

    if (in_trans == 1'b0) begin
        if (in_uplo == 1'b0) begin
            if (nx0 != 64'sd0) begin
                if (in_diag == 1'b0) begin
                    nx0 = wrap_mul(nx0, ap0_s);
                end
            end
            if (nx1 != 64'sd0) begin
                temp1 = nx1;
                nx0 = wrap_addmul(nx0, temp1, ap1_s);
                if (in_diag == 1'b0) begin
                    nx1 = wrap_mul(nx1, ap2_s);
                end
            end
            if (nx2 != 64'sd0) begin
                temp2 = nx2;
                nx0 = wrap_addmul(nx0, temp2, ap3_s);
                nx1 = wrap_addmul(nx1, temp2, ap4_s);
                if (in_diag == 1'b0) begin
                    nx2 = wrap_mul(nx2, ap5_s);
                end
            end
            if (nx3 != 64'sd0) begin
                temp3 = nx3;
                nx0 = wrap_addmul(nx0, temp3, ap6_s);
                nx1 = wrap_addmul(nx1, temp3, ap7_s);
                nx2 = wrap_addmul(nx2, temp3, ap8_s);
                if (in_diag == 1'b0) begin
                    nx3 = wrap_mul(nx3, ap9_s);
                end
            end
        end else begin
            if (nx3 != 64'sd0) begin
                if (in_diag == 1'b0) begin
                    nx3 = wrap_mul(nx3, ap9_s);
                end
            end
            if (nx2 != 64'sd0) begin
                temp2 = nx2;
                nx3 = wrap_addmul(nx3, temp2, ap8_s);
                if (in_diag == 1'b0) begin
                    nx2 = wrap_mul(nx2, ap7_s);
                end
            end
            if (nx1 != 64'sd0) begin
                temp1 = nx1;
                nx3 = wrap_addmul(nx3, temp1, ap6_s);
                nx2 = wrap_addmul(nx2, temp1, ap5_s);
                if (in_diag == 1'b0) begin
                    nx1 = wrap_mul(nx1, ap4_s);
                end
            end
            if (nx0 != 64'sd0) begin
                temp0 = nx0;
                nx3 = wrap_addmul(nx3, temp0, ap3_s);
                nx2 = wrap_addmul(nx2, temp0, ap2_s);
                nx1 = wrap_addmul(nx1, temp0, ap1_s);
                if (in_diag == 1'b0) begin
                    nx0 = wrap_mul(nx0, ap0_s);
                end
            end
        end
    end else begin
        if (in_uplo == 1'b0) begin
            temp3 = nx3;
            if (in_diag == 1'b0) begin
                temp3 = wrap_mul(temp3, ap9_s);
            end
            temp3 = wrap_addmul(temp3, ap8_s, nx2);
            temp3 = wrap_addmul(temp3, ap7_s, nx1);
            temp3 = wrap_addmul(temp3, ap6_s, nx0);
            nx3 = temp3;

            temp2 = nx2;
            if (in_diag == 1'b0) begin
                temp2 = wrap_mul(temp2, ap5_s);
            end
            temp2 = wrap_addmul(temp2, ap4_s, nx1);
            temp2 = wrap_addmul(temp2, ap3_s, nx0);
            nx2 = temp2;

            temp1 = nx1;
            if (in_diag == 1'b0) begin
                temp1 = wrap_mul(temp1, ap2_s);
            end
            temp1 = wrap_addmul(temp1, ap1_s, nx0);
            nx1 = temp1;

            temp0 = nx0;
            if (in_diag == 1'b0) begin
                temp0 = wrap_mul(temp0, ap0_s);
            end
            nx0 = temp0;
        end else begin
            temp0 = nx0;
            if (in_diag == 1'b0) begin
                temp0 = wrap_mul(temp0, ap0_s);
            end
            temp0 = wrap_addmul(temp0, ap1_s, nx1);
            temp0 = wrap_addmul(temp0, ap2_s, nx2);
            temp0 = wrap_addmul(temp0, ap3_s, nx3);
            nx0 = temp0;

            temp1 = nx1;
            if (in_diag == 1'b0) begin
                temp1 = wrap_mul(temp1, ap4_s);
            end
            temp1 = wrap_addmul(temp1, ap5_s, nx2);
            temp1 = wrap_addmul(temp1, ap6_s, nx3);
            nx1 = temp1;

            temp2 = nx2;
            if (in_diag == 1'b0) begin
                temp2 = wrap_mul(temp2, ap7_s);
            end
            temp2 = wrap_addmul(temp2, ap8_s, nx3);
            nx2 = temp2;

            temp3 = nx3;
            if (in_diag == 1'b0) begin
                temp3 = wrap_mul(temp3, ap9_s);
            end
            nx3 = temp3;
        end
    end
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy   <= 1'b0;
        out_x0 <= 64'd0;
        out_x1 <= 64'd0;
        out_x2 <= 64'd0;
        out_x3 <= 64'd0;
    end else if (input_accepted) begin
        busy   <= 1'b1;
        out_x0 <= nx0;
        out_x1 <= nx1;
        out_x2 <= nx2;
        out_x3 <= nx3;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule