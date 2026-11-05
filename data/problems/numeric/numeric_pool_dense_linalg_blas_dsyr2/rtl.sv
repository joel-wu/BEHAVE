module symmetric_rank2_update4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_uplo,
    input  logic [63:0]  in_alpha,
    input  logic [63:0]  in_x0,
    input  logic [63:0]  in_x1,
    input  logic [63:0]  in_x2,
    input  logic [63:0]  in_x3,
    input  logic [63:0]  in_y0,
    input  logic [63:0]  in_y1,
    input  logic [63:0]  in_y2,
    input  logic [63:0]  in_y3,
    input  logic [63:0]  in_a00,
    input  logic [63:0]  in_a01,
    input  logic [63:0]  in_a02,
    input  logic [63:0]  in_a03,
    input  logic [63:0]  in_a11,
    input  logic [63:0]  in_a12,
    input  logic [63:0]  in_a13,
    input  logic [63:0]  in_a22,
    input  logic [63:0]  in_a23,
    input  logic [63:0]  in_a33,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_a00,
    output logic [63:0]  out_a01,
    output logic [63:0]  out_a02,
    output logic [63:0]  out_a03,
    output logic [63:0]  out_a11,
    output logic [63:0]  out_a12,
    output logic [63:0]  out_a13,
    output logic [63:0]  out_a22,
    output logic [63:0]  out_a23,
    output logic [63:0]  out_a33
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic signed [63:0] mul_trunc64(
        input logic signed [63:0] a,
        input logic signed [63:0] b
    );
        logic signed [127:0] prod;
        begin
            prod = a * b;
            mul_trunc64 = prod[63:0];
        end
    endfunction

    function automatic logic signed [63:0] add_trunc64(
        input logic signed [63:0] a,
        input logic signed [63:0] b
    );
        logic signed [64:0] sum;
        begin
            sum = a + b;
            add_trunc64 = sum[63:0];
        end
    endfunction

    logic signed [63:0] alpha_s;
    logic signed [63:0] x0_s, x1_s, x2_s, x3_s;
    logic signed [63:0] y0_s, y1_s, y2_s, y3_s;
    logic signed [63:0] a00_s, a01_s, a02_s, a03_s;
    logic signed [63:0] a11_s, a12_s, a13_s, a22_s, a23_s, a33_s;

    logic signed [63:0] r_a00, r_a01, r_a02, r_a03;
    logic signed [63:0] r_a11, r_a12, r_a13, r_a22, r_a23, r_a33;

    logic signed [63:0] temp1, temp2, p1, p2, s1;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy    <= 1'b0;
            out_a00 <= 64'h0000000000000000;
            out_a01 <= 64'h0000000000000000;
            out_a02 <= 64'h0000000000000000;
            out_a03 <= 64'h0000000000000000;
            out_a11 <= 64'h0000000000000000;
            out_a12 <= 64'h0000000000000000;
            out_a13 <= 64'h0000000000000000;
            out_a22 <= 64'h0000000000000000;
            out_a23 <= 64'h0000000000000000;
            out_a33 <= 64'h0000000000000000;
        end else if (input_accepted) begin
            alpha_s = $signed(in_alpha);
            x0_s = $signed(in_x0);
            x1_s = $signed(in_x1);
            x2_s = $signed(in_x2);
            x3_s = $signed(in_x3);
            y0_s = $signed(in_y0);
            y1_s = $signed(in_y1);
            y2_s = $signed(in_y2);
            y3_s = $signed(in_y3);

            a00_s = $signed(in_a00);
            a01_s = $signed(in_a01);
            a02_s = $signed(in_a02);
            a03_s = $signed(in_a03);
            a11_s = $signed(in_a11);
            a12_s = $signed(in_a12);
            a13_s = $signed(in_a13);
            a22_s = $signed(in_a22);
            a23_s = $signed(in_a23);
            a33_s = $signed(in_a33);

            r_a00 = a00_s;
            r_a01 = a01_s;
            r_a02 = a02_s;
            r_a03 = a03_s;
            r_a11 = a11_s;
            r_a12 = a12_s;
            r_a13 = a13_s;
            r_a22 = a22_s;
            r_a23 = a23_s;
            r_a33 = a33_s;

            if (alpha_s != 64'sd0) begin
                if (in_uplo) begin
                    if ((x0_s != 64'sd0) || (y0_s != 64'sd0)) begin
                        temp1 = mul_trunc64(alpha_s, y0_s);
                        temp2 = mul_trunc64(alpha_s, x0_s);
                        p1 = mul_trunc64(x0_s, temp1);
                        p2 = mul_trunc64(y0_s, temp2);
                        s1 = add_trunc64(r_a00, p1);
                        r_a00 = add_trunc64(s1, p2);
                    end

                    if ((x1_s != 64'sd0) || (y1_s != 64'sd0)) begin
                        temp1 = mul_trunc64(alpha_s, y1_s);
                        temp2 = mul_trunc64(alpha_s, x1_s);

                        p1 = mul_trunc64(x0_s, temp1);
                        p2 = mul_trunc64(y0_s, temp2);
                        s1 = add_trunc64(r_a01, p1);
                        r_a01 = add_trunc64(s1, p2);

                        p1 = mul_trunc64(x1_s, temp1);
                        p2 = mul_trunc64(y1_s, temp2);
                        s1 = add_trunc64(r_a11, p1);
                        r_a11 = add_trunc64(s1, p2);
                    end

                    if ((x2_s != 64'sd0) || (y2_s != 64'sd0)) begin
                        temp1 = mul_trunc64(alpha_s, y2_s);
                        temp2 = mul_trunc64(alpha_s, x2_s);

                        p1 = mul_trunc64(x0_s, temp1);
                        p2 = mul_trunc64(y0_s, temp2);
                        s1 = add_trunc64(r_a02, p1);
                        r_a02 = add_trunc64(s1, p2);

                        p1 = mul_trunc64(x1_s, temp1);
                        p2 = mul_trunc64(y1_s, temp2);
                        s1 = add_trunc64(r_a12, p1);
                        r_a12 = add_trunc64(s1, p2);

                        p1 = mul_trunc64(x2_s, temp1);
                        p2 = mul_trunc64(y2_s, temp2);
                        s1 = add_trunc64(r_a22, p1);
                        r_a22 = add_trunc64(s1, p2);
                    end

                    if ((x3_s != 64'sd0) || (y3_s != 64'sd0)) begin
                        temp1 = mul_trunc64(alpha_s, y3_s);
                        temp2 = mul_trunc64(alpha_s, x3_s);

                        p1 = mul_trunc64(x0_s, temp1);
                        p2 = mul_trunc64(y0_s, temp2);
                        s1 = add_trunc64(r_a03, p1);
                        r_a03 = add_trunc64(s1, p2);

                        p1 = mul_trunc64(x1_s, temp1);
                        p2 = mul_trunc64(y1_s, temp2);
                        s1 = add_trunc64(r_a13, p1);
                        r_a13 = add_trunc64(s1, p2);

                        p1 = mul_trunc64(x2_s, temp1);
                        p2 = mul_trunc64(y2_s, temp2);
                        s1 = add_trunc64(r_a23, p1);
                        r_a23 = add_trunc64(s1, p2);

                        p1 = mul_trunc64(x3_s, temp1);
                        p2 = mul_trunc64(y3_s, temp2);
                        s1 = add_trunc64(r_a33, p1);
                        r_a33 = add_trunc64(s1, p2);
                    end
                end else begin
                    if ((x0_s != 64'sd0) || (y0_s != 64'sd0)) begin
                        temp1 = mul_trunc64(alpha_s, y0_s);
                        temp2 = mul_trunc64(alpha_s, x0_s);

                        p1 = mul_trunc64(x0_s, temp1);
                        p2 = mul_trunc64(y0_s, temp2);
                        s1 = add_trunc64(r_a00, p1);
                        r_a00 = add_trunc64(s1, p2);

                        p1 = mul_trunc64(x1_s, temp1);
                        p2 = mul_trunc64(y1_s, temp2);
                        s1 = add_trunc64(r_a01, p1);
                        r_a01 = add_trunc64(s1, p2);

                        p1 = mul_trunc64(x2_s, temp1);
                        p2 = mul_trunc64(y2_s, temp2);
                        s1 = add_trunc64(r_a02, p1);
                        r_a02 = add_trunc64(s1, p2);

                        p1 = mul_trunc64(x3_s, temp1);
                        p2 = mul_trunc64(y3_s, temp2);
                        s1 = add_trunc64(r_a03, p1);
                        r_a03 = add_trunc64(s1, p2);
                    end

                    if ((x1_s != 64'sd0) || (y1_s != 64'sd0)) begin
                        temp1 = mul_trunc64(alpha_s, y1_s);
                        temp2 = mul_trunc64(alpha_s, x1_s);

                        p1 = mul_trunc64(x1_s, temp1);
                        p2 = mul_trunc64(y1_s, temp2);
                        s1 = add_trunc64(r_a11, p1);
                        r_a11 = add_trunc64(s1, p2);

                        p1 = mul_trunc64(x2_s, temp1);
                        p2 = mul_trunc64(y2_s, temp2);
                        s1 = add_trunc64(r_a12, p1);
                        r_a12 = add_trunc64(s1, p2);

                        p1 = mul_trunc64(x3_s, temp1);
                        p2 = mul_trunc64(y3_s, temp2);
                        s1 = add_trunc64(r_a13, p1);
                        r_a13 = add_trunc64(s1, p2);
                    end

                    if ((x2_s != 64'sd0) || (y2_s != 64'sd0)) begin
                        temp1 = mul_trunc64(alpha_s, y2_s);
                        temp2 = mul_trunc64(alpha_s, x2_s);

                        p1 = mul_trunc64(x2_s, temp1);
                        p2 = mul_trunc64(y2_s, temp2);
                        s1 = add_trunc64(r_a22, p1);
                        r_a22 = add_trunc64(s1, p2);

                        p1 = mul_trunc64(x3_s, temp1);
                        p2 = mul_trunc64(y3_s, temp2);
                        s1 = add_trunc64(r_a23, p1);
                        r_a23 = add_trunc64(s1, p2);
                    end

                    if ((x3_s != 64'sd0) || (y3_s != 64'sd0)) begin
                        temp1 = mul_trunc64(alpha_s, y3_s);
                        temp2 = mul_trunc64(alpha_s, x3_s);

                        p1 = mul_trunc64(x3_s, temp1);
                        p2 = mul_trunc64(y3_s, temp2);
                        s1 = add_trunc64(r_a33, p1);
                        r_a33 = add_trunc64(s1, p2);
                    end
                end
            end

            out_a00 <= r_a00[63:0];
            out_a01 <= r_a01[63:0];
            out_a02 <= r_a02[63:0];
            out_a03 <= r_a03[63:0];
            out_a11 <= r_a11[63:0];
            out_a12 <= r_a12[63:0];
            out_a13 <= r_a13[63:0];
            out_a22 <= r_a22[63:0];
            out_a23 <= r_a23[63:0];
            out_a33 <= r_a33[63:0];
            busy    <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule