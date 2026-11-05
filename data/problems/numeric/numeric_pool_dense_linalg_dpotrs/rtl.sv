module cholesky_solve4x2 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_uplo,

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

    input  logic [15:0]  in_b00,
    input  logic [15:0]  in_b01,
    input  logic [15:0]  in_b10,
    input  logic [15:0]  in_b11,
    input  logic [15:0]  in_b20,
    input  logic [15:0]  in_b21,
    input  logic [15:0]  in_b30,
    input  logic [15:0]  in_b31,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_x00,
    output logic [15:0]  out_x01,
    output logic [15:0]  out_x10,
    output logic [15:0]  out_x11,
    output logic [15:0]  out_x20,
    output logic [15:0]  out_x21,
    output logic [15:0]  out_x30,
    output logic [15:0]  out_x31
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [31:0] to_s32_from_u16(input logic [15:0] v);
    begin
        to_s32_from_u16 = {{16{v[15]}}, v};
    end
endfunction

function automatic logic signed [31:0] mul_s32(
    input logic signed [31:0] a,
    input logic signed [31:0] b
);
    logic signed [63:0] prod;
    begin
        prod = a * b;
        mul_s32 = prod[31:0];
    end
endfunction

function automatic logic signed [31:0] sub_s32(
    input logic signed [31:0] a,
    input logic signed [31:0] b
);
    logic signed [32:0] diff_ext;
    begin
        diff_ext = a - b;
        sub_s32 = diff_ext[31:0];
    end
endfunction

function automatic logic signed [31:0] div0_s32(
    input logic signed [31:0] n,
    input logic signed [31:0] d
);
    logic signed [31:0] abs_n;
    logic signed [31:0] abs_d;
    logic signed [31:0] q;
    begin
        div0_s32 = 32'sd0;
        abs_n = (n < 0) ? -n : n;
        abs_d = (d < 0) ? -d : d;
        q = 32'sd0;

        if (d == 0) begin
            div0_s32 = 32'sd0;
        end else begin
            q = abs_n / abs_d;
            if ((n < 0) ^ (d < 0))
                div0_s32 = -q;
            else
                div0_s32 = q;
        end
    end
endfunction

logic signed [31:0] t00, t01, t02, t03;
logic signed [31:0] t10, t11, t12, t13;
logic signed [31:0] t20, t21, t22, t23;
logic signed [31:0] t30, t31, t32, t33;
logic signed [31:0] b00, b01, b10, b11, b20, b21, b30, b31;

logic signed [31:0] y00, y01, y10, y11, y20, y21, y30, y31;
logic signed [31:0] x00, x01, x10, x11, x20, x21, x30, x31;

logic signed [31:0] tmp0, tmp1, tmp2;

always_ff @(posedge clk) begin
    if (rst) begin
        busy    <= 1'b0;
        out_x00 <= 16'h0000;
        out_x01 <= 16'h0000;
        out_x10 <= 16'h0000;
        out_x11 <= 16'h0000;
        out_x20 <= 16'h0000;
        out_x21 <= 16'h0000;
        out_x30 <= 16'h0000;
        out_x31 <= 16'h0000;
    end else if (input_accepted) begin
        t00 = to_s32_from_u16(in_a00);
        t01 = to_s32_from_u16(in_a01);
        t02 = to_s32_from_u16(in_a02);
        t03 = to_s32_from_u16(in_a03);
        t10 = to_s32_from_u16(in_a10);
        t11 = to_s32_from_u16(in_a11);
        t12 = to_s32_from_u16(in_a12);
        t13 = to_s32_from_u16(in_a13);
        t20 = to_s32_from_u16(in_a20);
        t21 = to_s32_from_u16(in_a21);
        t22 = to_s32_from_u16(in_a22);
        t23 = to_s32_from_u16(in_a23);
        t30 = to_s32_from_u16(in_a30);
        t31 = to_s32_from_u16(in_a31);
        t32 = to_s32_from_u16(in_a32);
        t33 = to_s32_from_u16(in_a33);

        b00 = to_s32_from_u16(in_b00);
        b01 = to_s32_from_u16(in_b01);
        b10 = to_s32_from_u16(in_b10);
        b11 = to_s32_from_u16(in_b11);
        b20 = to_s32_from_u16(in_b20);
        b21 = to_s32_from_u16(in_b21);
        b30 = to_s32_from_u16(in_b30);
        b31 = to_s32_from_u16(in_b31);

        if (in_uplo) begin
            y00 = div0_s32(b00, t00);

            tmp0 = mul_s32(t01, y00);
            tmp1 = sub_s32(b10, tmp0);
            y10 = div0_s32(tmp1, t11);

            tmp0 = mul_s32(t02, y00);
            tmp1 = mul_s32(t12, y10);
            tmp2 = sub_s32(b20, tmp0);
            tmp2 = sub_s32(tmp2, tmp1);
            y20 = div0_s32(tmp2, t22);

            tmp0 = mul_s32(t03, y00);
            tmp1 = mul_s32(t13, y10);
            tmp2 = mul_s32(t23, y20);
            tmp0 = sub_s32(b30, tmp0);
            tmp0 = sub_s32(tmp0, tmp1);
            tmp0 = sub_s32(tmp0, tmp2);
            y30 = div0_s32(tmp0, t33);

            y01 = div0_s32(b01, t00);

            tmp0 = mul_s32(t01, y01);
            tmp1 = sub_s32(b11, tmp0);
            y11 = div0_s32(tmp1, t11);

            tmp0 = mul_s32(t02, y01);
            tmp1 = mul_s32(t12, y11);
            tmp2 = sub_s32(b21, tmp0);
            tmp2 = sub_s32(tmp2, tmp1);
            y21 = div0_s32(tmp2, t22);

            tmp0 = mul_s32(t03, y01);
            tmp1 = mul_s32(t13, y11);
            tmp2 = mul_s32(t23, y21);
            tmp0 = sub_s32(b31, tmp0);
            tmp0 = sub_s32(tmp0, tmp1);
            tmp0 = sub_s32(tmp0, tmp2);
            y31 = div0_s32(tmp0, t33);

            x30 = div0_s32(y30, t33);

            tmp0 = mul_s32(t23, x30);
            tmp1 = sub_s32(y20, tmp0);
            x20 = div0_s32(tmp1, t22);

            tmp0 = mul_s32(t12, x20);
            tmp1 = mul_s32(t13, x30);
            tmp2 = sub_s32(y10, tmp0);
            tmp2 = sub_s32(tmp2, tmp1);
            x10 = div0_s32(tmp2, t11);

            tmp0 = mul_s32(t01, x10);
            tmp1 = mul_s32(t02, x20);
            tmp2 = mul_s32(t03, x30);
            tmp0 = sub_s32(y00, tmp0);
            tmp0 = sub_s32(tmp0, tmp1);
            tmp0 = sub_s32(tmp0, tmp2);
            x00 = div0_s32(tmp0, t00);

            x31 = div0_s32(y31, t33);

            tmp0 = mul_s32(t23, x31);
            tmp1 = sub_s32(y21, tmp0);
            x21 = div0_s32(tmp1, t22);

            tmp0 = mul_s32(t12, x21);
            tmp1 = mul_s32(t13, x31);
            tmp2 = sub_s32(y11, tmp0);
            tmp2 = sub_s32(tmp2, tmp1);
            x11 = div0_s32(tmp2, t11);

            tmp0 = mul_s32(t01, x11);
            tmp1 = mul_s32(t02, x21);
            tmp2 = mul_s32(t03, x31);
            tmp0 = sub_s32(y01, tmp0);
            tmp0 = sub_s32(tmp0, tmp1);
            tmp0 = sub_s32(tmp0, tmp2);
            x01 = div0_s32(tmp0, t00);
        end else begin
            y00 = div0_s32(b00, t00);

            tmp0 = mul_s32(t10, y00);
            tmp1 = sub_s32(b10, tmp0);
            y10 = div0_s32(tmp1, t11);

            tmp0 = mul_s32(t20, y00);
            tmp1 = mul_s32(t21, y10);
            tmp2 = sub_s32(b20, tmp0);
            tmp2 = sub_s32(tmp2, tmp1);
            y20 = div0_s32(tmp2, t22);

            tmp0 = mul_s32(t30, y00);
            tmp1 = mul_s32(t31, y10);
            tmp2 = mul_s32(t32, y20);
            tmp0 = sub_s32(b30, tmp0);
            tmp0 = sub_s32(tmp0, tmp1);
            tmp0 = sub_s32(tmp0, tmp2);
            y30 = div0_s32(tmp0, t33);

            y01 = div0_s32(b01, t00);

            tmp0 = mul_s32(t10, y01);
            tmp1 = sub_s32(b11, tmp0);
            y11 = div0_s32(tmp1, t11);

            tmp0 = mul_s32(t20, y01);
            tmp1 = mul_s32(t21, y11);
            tmp2 = sub_s32(b21, tmp0);
            tmp2 = sub_s32(tmp2, tmp1);
            y21 = div0_s32(tmp2, t22);

            tmp0 = mul_s32(t30, y01);
            tmp1 = mul_s32(t31, y11);
            tmp2 = mul_s32(t32, y21);
            tmp0 = sub_s32(b31, tmp0);
            tmp0 = sub_s32(tmp0, tmp1);
            tmp0 = sub_s32(tmp0, tmp2);
            y31 = div0_s32(tmp0, t33);

            x30 = div0_s32(y30, t33);

            tmp0 = mul_s32(t32, x30);
            tmp1 = sub_s32(y20, tmp0);
            x20 = div0_s32(tmp1, t22);

            tmp0 = mul_s32(t21, x20);
            tmp1 = mul_s32(t31, x30);
            tmp2 = sub_s32(y10, tmp0);
            tmp2 = sub_s32(tmp2, tmp1);
            x10 = div0_s32(tmp2, t11);

            tmp0 = mul_s32(t10, x10);
            tmp1 = mul_s32(t20, x20);
            tmp2 = mul_s32(t30, x30);
            tmp0 = sub_s32(y00, tmp0);
            tmp0 = sub_s32(tmp0, tmp1);
            tmp0 = sub_s32(tmp0, tmp2);
            x00 = div0_s32(tmp0, t00);

            x31 = div0_s32(y31, t33);

            tmp0 = mul_s32(t32, x31);
            tmp1 = sub_s32(y21, tmp0);
            x21 = div0_s32(tmp1, t22);

            tmp0 = mul_s32(t21, x21);
            tmp1 = mul_s32(t31, x31);
            tmp2 = sub_s32(y11, tmp0);
            tmp2 = sub_s32(tmp2, tmp1);
            x11 = div0_s32(tmp2, t11);

            tmp0 = mul_s32(t10, x11);
            tmp1 = mul_s32(t20, x21);
            tmp2 = mul_s32(t30, x31);
            tmp0 = sub_s32(y01, tmp0);
            tmp0 = sub_s32(tmp0, tmp1);
            tmp0 = sub_s32(tmp0, tmp2);
            x01 = div0_s32(tmp0, t00);
        end

        out_x00 <= x00[15:0];
        out_x01 <= x01[15:0];
        out_x10 <= x10[15:0];
        out_x11 <= x11[15:0];
        out_x20 <= x20[15:0];
        out_x21 <= x21[15:0];
        out_x30 <= x30[15:0];
        out_x31 <= x31[15:0];
        busy    <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule