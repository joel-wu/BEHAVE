module ghash_full_message (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_h,
    input  logic [127:0] in_y0,
    input  logic [7:0]   in_len_blocks,
    input  logic [127:0] in_block0,
    input  logic [127:0] in_block1,
    input  logic [127:0] in_block2,
    input  logic [127:0] in_block3,
    input  logic [127:0] in_block4,
    input  logic [127:0] in_block5,
    input  logic [127:0] in_block6,
    input  logic [127:0] in_block7,
    input  logic [127:0] in_block8,
    input  logic [127:0] in_block9,
    input  logic [127:0] in_block10,
    input  logic [127:0] in_block11,
    input  logic [127:0] in_block12,
    input  logic [127:0] in_block13,
    input  logic [127:0] in_block14,
    input  logic [127:0] in_block15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [127:0] out_y
);

    localparam logic [127:0] R = 128'hE1000000000000000000000000000000;

    logic busy;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [127:0] gf_mul_h(
        input logic [127:0] x,
        input logic [127:0] h
    );
        logic [127:0] v_cur;
        logic [127:0] z_cur;
        integer i;
        begin
            v_cur = h;
            z_cur = 128'h00000000000000000000000000000000;
            for (i = 0; i < 128; i = i + 1) begin
                if (x[127 - i]) begin
                    z_cur = z_cur ^ v_cur;
                end
                if (i != 127) begin
                    if (v_cur[0]) begin
                        v_cur = (v_cur >> 1) ^ R;
                    end else begin
                        v_cur = (v_cur >> 1);
                    end
                end
            end
            gf_mul_h = z_cur;
        end
    endfunction

    function automatic logic [127:0] compute_y(
        input logic [127:0] h,
        input logic [127:0] y0,
        input logic [7:0]   len_blocks,
        input logic [127:0] block0,
        input logic [127:0] block1,
        input logic [127:0] block2,
        input logic [127:0] block3,
        input logic [127:0] block4,
        input logic [127:0] block5,
        input logic [127:0] block6,
        input logic [127:0] block7,
        input logic [127:0] block8,
        input logic [127:0] block9,
        input logic [127:0] block10,
        input logic [127:0] block11,
        input logic [127:0] block12,
        input logic [127:0] block13,
        input logic [127:0] block14,
        input logic [127:0] block15
    );
        logic [127:0] y_cur;
        logic [7:0] n;
        begin
            if (len_blocks < 8'd16) begin
                n = len_blocks;
            end else begin
                n = 8'd16;
            end

            y_cur = y0;

            if (8'd0 < n)  y_cur = gf_mul_h(y_cur ^ block0,  h);
            if (8'd1 < n)  y_cur = gf_mul_h(y_cur ^ block1,  h);
            if (8'd2 < n)  y_cur = gf_mul_h(y_cur ^ block2,  h);
            if (8'd3 < n)  y_cur = gf_mul_h(y_cur ^ block3,  h);
            if (8'd4 < n)  y_cur = gf_mul_h(y_cur ^ block4,  h);
            if (8'd5 < n)  y_cur = gf_mul_h(y_cur ^ block5,  h);
            if (8'd6 < n)  y_cur = gf_mul_h(y_cur ^ block6,  h);
            if (8'd7 < n)  y_cur = gf_mul_h(y_cur ^ block7,  h);
            if (8'd8 < n)  y_cur = gf_mul_h(y_cur ^ block8,  h);
            if (8'd9 < n)  y_cur = gf_mul_h(y_cur ^ block9,  h);
            if (8'd10 < n) y_cur = gf_mul_h(y_cur ^ block10, h);
            if (8'd11 < n) y_cur = gf_mul_h(y_cur ^ block11, h);
            if (8'd12 < n) y_cur = gf_mul_h(y_cur ^ block12, h);
            if (8'd13 < n) y_cur = gf_mul_h(y_cur ^ block13, h);
            if (8'd14 < n) y_cur = gf_mul_h(y_cur ^ block14, h);
            if (8'd15 < n) y_cur = gf_mul_h(y_cur ^ block15, h);

            compute_y = y_cur;
        end
    endfunction

    always_ff @(posedge clk) begin
        if (rst) begin
            busy  <= 1'b0;
            out_y <= 128'h00000000000000000000000000000000;
        end else if (input_accepted) begin
            out_y <= compute_y(
                in_h,
                in_y0,
                in_len_blocks,
                in_block0,
                in_block1,
                in_block2,
                in_block3,
                in_block4,
                in_block5,
                in_block6,
                in_block7,
                in_block8,
                in_block9,
                in_block10,
                in_block11,
                in_block12,
                in_block13,
                in_block14,
                in_block15
            );
            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule