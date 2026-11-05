module block_reflector_apply4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_side,
    input  logic         in_trans,
    input  logic         in_direct,
    input  logic         in_storev,

    input  logic [15:0]  in_v00,
    input  logic [15:0]  in_v01,
    input  logic [15:0]  in_v10,
    input  logic [15:0]  in_v11,
    input  logic [15:0]  in_v20,
    input  logic [15:0]  in_v21,
    input  logic [15:0]  in_v30,
    input  logic [15:0]  in_v31,

    input  logic [15:0]  in_t00,
    input  logic [15:0]  in_t01,
    input  logic [15:0]  in_t10,
    input  logic [15:0]  in_t11,

    input  logic [15:0]  in_c00,
    input  logic [15:0]  in_c01,
    input  logic [15:0]  in_c02,
    input  logic [15:0]  in_c03,
    input  logic [15:0]  in_c10,
    input  logic [15:0]  in_c11,
    input  logic [15:0]  in_c12,
    input  logic [15:0]  in_c13,
    input  logic [15:0]  in_c20,
    input  logic [15:0]  in_c21,
    input  logic [15:0]  in_c22,
    input  logic [15:0]  in_c23,
    input  logic [15:0]  in_c30,
    input  logic [15:0]  in_c31,
    input  logic [15:0]  in_c32,
    input  logic [15:0]  in_c33,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_c00,
    output logic [15:0]  out_c01,
    output logic [15:0]  out_c02,
    output logic [15:0]  out_c03,
    output logic [15:0]  out_c10,
    output logic [15:0]  out_c11,
    output logic [15:0]  out_c12,
    output logic [15:0]  out_c13,
    output logic [15:0]  out_c20,
    output logic [15:0]  out_c21,
    output logic [15:0]  out_c22,
    output logic [15:0]  out_c23,
    output logic [15:0]  out_c30,
    output logic [15:0]  out_c31,
    output logic [15:0]  out_c32,
    output logic [15:0]  out_c33
);

    function automatic logic [15:0] add16(input logic [15:0] a, input logic [15:0] b);
        logic signed [16:0] sum;
        begin
            sum = $signed(a) + $signed(b);
            add16 = sum[15:0];
        end
    endfunction

    function automatic logic [15:0] sub16(input logic [15:0] a, input logic [15:0] b);
        logic signed [16:0] diff;
        begin
            diff = $signed(a) - $signed(b);
            sub16 = diff[15:0];
        end
    endfunction

    function automatic logic [15:0] dot2(
        input logic [15:0] a0,
        input logic [15:0] a1,
        input logic [15:0] b0,
        input logic [15:0] b1
    );
        logic signed [31:0] p0;
        logic signed [31:0] p1;
        logic signed [32:0] s;
        begin
            p0 = $signed(a0) * $signed(b0);
            p1 = $signed(a1) * $signed(b1);
            s  = p0 + p1;
            dot2 = s[15:0];
        end
    endfunction

    logic busy;
    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk) begin
        logic [15:0] a00, a01, a10, a11;
        logic [15:0] b00, b01, b10, b11;
        logic [15:0] tm00, tm01, tm10, tm11;

        logic [15:0] w10, w11, x0, x1, w20, w21, w30, w31;
        logic [15:0] y0, y1, z0, z1;

        if (rst) begin
            busy    <= 1'b0;
            out_c00 <= 16'h0000;
            out_c01 <= 16'h0000;
            out_c02 <= 16'h0000;
            out_c03 <= 16'h0000;
            out_c10 <= 16'h0000;
            out_c11 <= 16'h0000;
            out_c12 <= 16'h0000;
            out_c13 <= 16'h0000;
            out_c20 <= 16'h0000;
            out_c21 <= 16'h0000;
            out_c22 <= 16'h0000;
            out_c23 <= 16'h0000;
            out_c30 <= 16'h0000;
            out_c31 <= 16'h0000;
            out_c32 <= 16'h0000;
            out_c33 <= 16'h0000;
        end else if (input_accepted) begin
            if (in_direct == 1'b0) begin
                tm00 = in_t00;
                tm01 = in_t01;
                tm10 = 16'h0000;
                tm11 = in_t11;
            end else begin
                tm00 = in_t00;
                tm01 = 16'h0000;
                tm10 = in_t10;
                tm11 = in_t11;
            end

            if (((in_side == 1'b0) && (in_trans == 1'b0)) || ((in_side == 1'b1) && (in_trans == 1'b1))) begin
                a00 = tm00;
                a01 = tm10;
                a10 = tm01;
                a11 = tm11;
                tm00 = a00;
                tm01 = a01;
                tm10 = a10;
                tm11 = a11;
            end

            if ((in_side == 1'b0) && (in_direct == 1'b0)) begin
                if (in_storev == 1'b0) begin
                    a00 = 16'h0001; a01 = 16'h0000;
                    a10 = in_v10;   a11 = 16'h0001;
                    b00 = in_v20;   b01 = in_v21;
                    b10 = in_v30;   b11 = in_v31;
                end else begin
                    a00 = 16'h0001; a01 = in_v10;
                    a10 = 16'h0000; a11 = 16'h0001;
                    b00 = in_v20;   b01 = in_v30;
                    b10 = in_v21;   b11 = in_v31;
                end

                w10 = dot2(in_c00, in_c10, a00, a10);
                w11 = dot2(in_c00, in_c10, a01, a11);
                x0  = dot2(in_c20, in_c30, b00, b10);
                x1  = dot2(in_c20, in_c30, b01, b11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(b00, b01, w30, w31);
                y1  = dot2(b10, b11, w30, w31);
                z0  = dot2(w30, w31, a00, a01);
                z1  = dot2(w30, w31, a10, a11);
                out_c00 <= sub16(in_c00, z0);
                out_c10 <= sub16(in_c10, z1);
                out_c20 <= sub16(in_c20, y0);
                out_c30 <= sub16(in_c30, y1);

                w10 = dot2(in_c01, in_c11, a00, a10);
                w11 = dot2(in_c01, in_c11, a01, a11);
                x0  = dot2(in_c21, in_c31, b00, b10);
                x1  = dot2(in_c21, in_c31, b01, b11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(b00, b01, w30, w31);
                y1  = dot2(b10, b11, w30, w31);
                z0  = dot2(w30, w31, a00, a01);
                z1  = dot2(w30, w31, a10, a11);
                out_c01 <= sub16(in_c01, z0);
                out_c11 <= sub16(in_c11, z1);
                out_c21 <= sub16(in_c21, y0);
                out_c31 <= sub16(in_c31, y1);

                w10 = dot2(in_c02, in_c12, a00, a10);
                w11 = dot2(in_c02, in_c12, a01, a11);
                x0  = dot2(in_c22, in_c32, b00, b10);
                x1  = dot2(in_c22, in_c32, b01, b11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(b00, b01, w30, w31);
                y1  = dot2(b10, b11, w30, w31);
                z0  = dot2(w30, w31, a00, a01);
                z1  = dot2(w30, w31, a10, a11);
                out_c02 <= sub16(in_c02, z0);
                out_c12 <= sub16(in_c12, z1);
                out_c22 <= sub16(in_c22, y0);
                out_c32 <= sub16(in_c32, y1);

                w10 = dot2(in_c03, in_c13, a00, a10);
                w11 = dot2(in_c03, in_c13, a01, a11);
                x0  = dot2(in_c23, in_c33, b00, b10);
                x1  = dot2(in_c23, in_c33, b01, b11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(b00, b01, w30, w31);
                y1  = dot2(b10, b11, w30, w31);
                z0  = dot2(w30, w31, a00, a01);
                z1  = dot2(w30, w31, a10, a11);
                out_c03 <= sub16(in_c03, z0);
                out_c13 <= sub16(in_c13, z1);
                out_c23 <= sub16(in_c23, y0);
                out_c33 <= sub16(in_c33, y1);
            end else if ((in_side == 1'b0) && (in_direct == 1'b1)) begin
                if (in_storev == 1'b0) begin
                    a00 = in_v00;   a01 = in_v01;
                    a10 = in_v10;   a11 = in_v11;
                    b00 = 16'h0001; b01 = in_v21;
                    b10 = 16'h0000; b11 = 16'h0001;
                end else begin
                    a00 = in_v00;   a01 = in_v10;
                    a10 = in_v01;   a11 = in_v11;
                    b00 = 16'h0001; b01 = 16'h0000;
                    b10 = in_v21;   b11 = 16'h0001;
                end

                w10 = dot2(in_c20, in_c30, b00, b10);
                w11 = dot2(in_c20, in_c30, b01, b11);
                x0  = dot2(in_c00, in_c10, a00, a10);
                x1  = dot2(in_c00, in_c10, a01, a11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(a00, a01, w30, w31);
                y1  = dot2(a10, a11, w30, w31);
                z0  = dot2(w30, w31, b00, b01);
                z1  = dot2(w30, w31, b10, b11);
                out_c00 <= sub16(in_c00, y0);
                out_c10 <= sub16(in_c10, y1);
                out_c20 <= sub16(in_c20, z0);
                out_c30 <= sub16(in_c30, z1);

                w10 = dot2(in_c21, in_c31, b00, b10);
                w11 = dot2(in_c21, in_c31, b01, b11);
                x0  = dot2(in_c01, in_c11, a00, a10);
                x1  = dot2(in_c01, in_c11, a01, a11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(a00, a01, w30, w31);
                y1  = dot2(a10, a11, w30, w31);
                z0  = dot2(w30, w31, b00, b01);
                z1  = dot2(w30, w31, b10, b11);
                out_c01 <= sub16(in_c01, y0);
                out_c11 <= sub16(in_c11, y1);
                out_c21 <= sub16(in_c21, z0);
                out_c31 <= sub16(in_c31, z1);

                w10 = dot2(in_c22, in_c32, b00, b10);
                w11 = dot2(in_c22, in_c32, b01, b11);
                x0  = dot2(in_c02, in_c12, a00, a10);
                x1  = dot2(in_c02, in_c12, a01, a11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(a00, a01, w30, w31);
                y1  = dot2(a10, a11, w30, w31);
                z0  = dot2(w30, w31, b00, b01);
                z1  = dot2(w30, w31, b10, b11);
                out_c02 <= sub16(in_c02, y0);
                out_c12 <= sub16(in_c12, y1);
                out_c22 <= sub16(in_c22, z0);
                out_c32 <= sub16(in_c32, z1);

                w10 = dot2(in_c23, in_c33, b00, b10);
                w11 = dot2(in_c23, in_c33, b01, b11);
                x0  = dot2(in_c03, in_c13, a00, a10);
                x1  = dot2(in_c03, in_c13, a01, a11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(a00, a01, w30, w31);
                y1  = dot2(a10, a11, w30, w31);
                z0  = dot2(w30, w31, b00, b01);
                z1  = dot2(w30, w31, b10, b11);
                out_c03 <= sub16(in_c03, y0);
                out_c13 <= sub16(in_c13, y1);
                out_c23 <= sub16(in_c23, z0);
                out_c33 <= sub16(in_c33, z1);
            end else if ((in_side == 1'b1) && (in_direct == 1'b0)) begin
                if (in_storev == 1'b0) begin
                    a00 = 16'h0001; a01 = 16'h0000;
                    a10 = in_v10;   a11 = 16'h0001;
                    b00 = in_v20;   b01 = in_v21;
                    b10 = in_v30;   b11 = in_v31;
                end else begin
                    a00 = 16'h0001; a01 = in_v10;
                    a10 = 16'h0000; a11 = 16'h0001;
                    b00 = in_v20;   b01 = in_v30;
                    b10 = in_v21;   b11 = in_v31;
                end

                w10 = dot2(in_c00, in_c01, a00, a10);
                w11 = dot2(in_c00, in_c01, a01, a11);
                x0  = dot2(in_c02, in_c03, b00, b10);
                x1  = dot2(in_c02, in_c03, b01, b11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(w30, w31, b00, b01);
                y1  = dot2(w30, w31, b10, b11);
                z0  = dot2(w30, w31, a00, a01);
                z1  = dot2(w30, w31, a10, a11);
                out_c00 <= sub16(in_c00, z0);
                out_c01 <= sub16(in_c01, z1);
                out_c02 <= sub16(in_c02, y0);
                out_c03 <= sub16(in_c03, y1);

                w10 = dot2(in_c10, in_c11, a00, a10);
                w11 = dot2(in_c10, in_c11, a01, a11);
                x0  = dot2(in_c12, in_c13, b00, b10);
                x1  = dot2(in_c12, in_c13, b01, b11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(w30, w31, b00, b01);
                y1  = dot2(w30, w31, b10, b11);
                z0  = dot2(w30, w31, a00, a01);
                z1  = dot2(w30, w31, a10, a11);
                out_c10 <= sub16(in_c10, z0);
                out_c11 <= sub16(in_c11, z1);
                out_c12 <= sub16(in_c12, y0);
                out_c13 <= sub16(in_c13, y1);

                w10 = dot2(in_c20, in_c21, a00, a10);
                w11 = dot2(in_c20, in_c21, a01, a11);
                x0  = dot2(in_c22, in_c23, b00, b10);
                x1  = dot2(in_c22, in_c23, b01, b11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(w30, w31, b00, b01);
                y1  = dot2(w30, w31, b10, b11);
                z0  = dot2(w30, w31, a00, a01);
                z1  = dot2(w30, w31, a10, a11);
                out_c20 <= sub16(in_c20, z0);
                out_c21 <= sub16(in_c21, z1);
                out_c22 <= sub16(in_c22, y0);
                out_c23 <= sub16(in_c23, y1);

                w10 = dot2(in_c30, in_c31, a00, a10);
                w11 = dot2(in_c30, in_c31, a01, a11);
                x0  = dot2(in_c32, in_c33, b00, b10);
                x1  = dot2(in_c32, in_c33, b01, b11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(w30, w31, b00, b01);
                y1  = dot2(w30, w31, b10, b11);
                z0  = dot2(w30, w31, a00, a01);
                z1  = dot2(w30, w31, a10, a11);
                out_c30 <= sub16(in_c30, z0);
                out_c31 <= sub16(in_c31, z1);
                out_c32 <= sub16(in_c32, y0);
                out_c33 <= sub16(in_c33, y1);
            end else begin
                if (in_storev == 1'b0) begin
                    a00 = in_v00;   a01 = in_v01;
                    a10 = in_v10;   a11 = in_v11;
                    b00 = 16'h0001; b01 = in_v21;
                    b10 = 16'h0000; b11 = 16'h0001;
                end else begin
                    a00 = in_v00;   a01 = in_v10;
                    a10 = in_v01;   a11 = in_v11;
                    b00 = 16'h0001; b01 = 16'h0000;
                    b10 = in_v21;   b11 = 16'h0001;
                end

                w10 = dot2(in_c02, in_c03, b00, b10);
                w11 = dot2(in_c02, in_c03, b01, b11);
                x0  = dot2(in_c00, in_c01, a00, a10);
                x1  = dot2(in_c00, in_c01, a01, a11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(a00, a01, w30, w31);
                y1  = dot2(a10, a11, w30, w31);
                z0  = dot2(w30, w31, b00, b01);
                z1  = dot2(w30, w31, b10, b11);
                out_c00 <= sub16(in_c00, y0);
                out_c01 <= sub16(in_c01, y1);
                out_c02 <= sub16(in_c02, z0);
                out_c03 <= sub16(in_c03, z1);

                w10 = dot2(in_c12, in_c13, b00, b10);
                w11 = dot2(in_c12, in_c13, b01, b11);
                x0  = dot2(in_c10, in_c11, a00, a10);
                x1  = dot2(in_c10, in_c11, a01, a11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(a00, a01, w30, w31);
                y1  = dot2(a10, a11, w30, w31);
                z0  = dot2(w30, w31, b00, b01);
                z1  = dot2(w30, w31, b10, b11);
                out_c10 <= sub16(in_c10, y0);
                out_c11 <= sub16(in_c11, y1);
                out_c12 <= sub16(in_c12, z0);
                out_c13 <= sub16(in_c13, z1);

                w10 = dot2(in_c22, in_c23, b00, b10);
                w11 = dot2(in_c22, in_c23, b01, b11);
                x0  = dot2(in_c20, in_c21, a00, a10);
                x1  = dot2(in_c20, in_c21, a01, a11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(a00, a01, w30, w31);
                y1  = dot2(a10, a11, w30, w31);
                z0  = dot2(w30, w31, b00, b01);
                z1  = dot2(w30, w31, b10, b11);
                out_c20 <= sub16(in_c20, y0);
                out_c21 <= sub16(in_c21, y1);
                out_c22 <= sub16(in_c22, z0);
                out_c23 <= sub16(in_c23, z1);

                w10 = dot2(in_c32, in_c33, b00, b10);
                w11 = dot2(in_c32, in_c33, b01, b11);
                x0  = dot2(in_c30, in_c31, a00, a10);
                x1  = dot2(in_c30, in_c31, a01, a11);
                w20 = add16(w10, x0);
                w21 = add16(w11, x1);
                w30 = dot2(w20, w21, tm00, tm10);
                w31 = dot2(w20, w21, tm01, tm11);
                y0  = dot2(a00, a01, w30, w31);
                y1  = dot2(a10, a11, w30, w31);
                z0  = dot2(w30, w31, b00, b01);
                z1  = dot2(w30, w31, b10, b11);
                out_c30 <= sub16(in_c30, y0);
                out_c31 <= sub16(in_c31, y1);
                out_c32 <= sub16(in_c32, z0);
                out_c33 <= sub16(in_c33, z1);
            end

            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule