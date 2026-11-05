module ChaCha20Block (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [255:0] in_key,
    input  logic [1:0]   in_nonce_size,
    input  logic [127:0] in_nonce,
    input  logic [63:0]  in_block_counter,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [511:0] out_block,
    output logic [127:0] out_next_state_12_15,
    output logic         out_error
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    logic [31:0] result_y0;
    logic [31:0] result_y1;
    logic [31:0] result_y2;
    logic [31:0] result_y3;
    logic [31:0] result_y4;
    logic [31:0] result_y5;
    logic [31:0] result_y6;
    logic [31:0] result_y7;
    logic [31:0] result_y8;
    logic [31:0] result_y9;
    logic [31:0] result_y10;
    logic [31:0] result_y11;
    logic [31:0] result_y12;
    logic [31:0] result_y13;
    logic [31:0] result_y14;
    logic [31:0] result_y15;

    logic [31:0] next12_r;
    logic [31:0] next13_r;
    logic [31:0] next14_r;
    logic [31:0] next15_r;
    logic        error_r;

    logic [31:0] s0;
    logic [31:0] s1;
    logic [31:0] s2;
    logic [31:0] s3;
    logic [31:0] s4;
    logic [31:0] s5;
    logic [31:0] s6;
    logic [31:0] s7;
    logic [31:0] s8;
    logic [31:0] s9;
    logic [31:0] s10;
    logic [31:0] s11;
    logic [31:0] s12;
    logic [31:0] s13;
    logic [31:0] s14;
    logic [31:0] s15;

    logic [31:0] h0;
    logic [31:0] h1;
    logic [31:0] h2;
    logic [31:0] h3;
    logic [31:0] h4;
    logic [31:0] h5;
    logic [31:0] h6;
    logic [31:0] h7;
    logic [31:0] h8;
    logic [31:0] h9;
    logic [31:0] h10;
    logic [31:0] h11;
    logic [31:0] h12;
    logic [31:0] h13;
    logic [31:0] h14;
    logic [31:0] h15;

    logic [31:0] t0;
    logic [31:0] t1;
    logic [31:0] t2;
    logic [31:0] t3;
    logic [31:0] t4;
    logic [31:0] t5;
    logic [31:0] t6;
    logic [31:0] t7;
    logic [31:0] t8;
    logic [31:0] t9;
    logic [31:0] t10;
    logic [31:0] t11;
    logic [31:0] t12;
    logic [31:0] t13;
    logic [31:0] t14;
    logic [31:0] t15;

    logic [31:0] qra_a;
    logic [31:0] qra_b;
    logic [31:0] qra_c;
    logic [31:0] qra_d;

    function automatic logic [31:0] rotl32(input logic [31:0] x, input logic [4:0] n);
        logic [31:0] left_part;
        logic [31:0] right_part;
        begin
            left_part  = x << n;
            right_part = x >> (32 - n);
            rotl32 = left_part | right_part;
        end
    endfunction

    task automatic qr(
        input  logic [31:0] ia,
        input  logic [31:0] ib,
        input  logic [31:0] ic,
        input  logic [31:0] id,
        output logic [31:0] oa,
        output logic [31:0] ob,
        output logic [31:0] oc,
        output logic [31:0] od
    );
        logic [31:0] a;
        logic [31:0] b;
        logic [31:0] c;
        logic [31:0] d;
        begin
            a = ia;
            b = ib;
            c = ic;
            d = id;

            a = a + b;
            d = d ^ a;
            d = rotl32(d, 5'd16);
            c = c + d;
            b = b ^ c;
            b = rotl32(b, 5'd12);
            a = a + b;
            d = d ^ a;
            d = rotl32(d, 5'd8);
            c = c + d;
            b = b ^ c;
            b = rotl32(b, 5'd7);

            oa = a;
            ob = b;
            oc = c;
            od = d;
        end
    endtask

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy                 <= 1'b0;
            out_block            <= 512'h0;
            out_next_state_12_15 <= 128'h0;
            out_error            <= 1'b0;
        end else if (input_accepted) begin
            if (in_nonce_size == 2'd3) begin
                out_block            <= 512'h0;
                out_next_state_12_15 <= 128'h0;
                out_error            <= 1'b1;
                busy                 <= 1'b1;
            end else begin
                s0  = 32'h61707865;
                s1  = 32'h3320646e;
                s2  = 32'h79622d32;
                s3  = 32'h6b206574;

                s4  = in_key[31:0];
                s5  = in_key[63:32];
                s6  = in_key[95:64];
                s7  = in_key[127:96];
                s8  = in_key[159:128];
                s9  = in_key[191:160];
                s10 = in_key[223:192];
                s11 = in_key[255:224];

                if (in_nonce_size == 2'd0) begin
                    s12 = in_block_counter[31:0];
                    s13 = in_block_counter[63:32];
                    s14 = in_nonce[31:0];
                    s15 = in_nonce[63:32];

                    next12_r = in_block_counter[31:0] + 32'd1;
                    if ((in_block_counter[31:0] + 32'd1) != 32'd0) begin
                        next13_r = in_block_counter[63:32];
                    end else begin
                        next13_r = in_block_counter[63:32] + 32'd1;
                    end
                    next14_r = in_nonce[31:0];
                    next15_r = in_nonce[63:32];
                    error_r  = (((in_block_counter[31:0] + 32'd1) == 32'd0) && (next13_r == 32'd0));
                end else if (in_nonce_size == 2'd1) begin
                    s12 = in_block_counter[31:0];
                    s13 = in_nonce[31:0];
                    s14 = in_nonce[63:32];
                    s15 = in_nonce[95:64];

                    next12_r = in_block_counter[31:0] + 32'd1;
                    next13_r = in_nonce[31:0];
                    next14_r = in_nonce[63:32];
                    next15_r = in_nonce[95:64];
                    error_r  = ((in_block_counter[31:0] + 32'd1) == 32'd0);
                end else begin
                    s12 = in_nonce[31:0];
                    s13 = in_nonce[63:32];
                    s14 = in_nonce[95:64];
                    s15 = in_nonce[127:96];

                    next12_r = in_nonce[31:0];
                    next13_r = in_nonce[63:32];
                    next14_r = in_nonce[95:64];
                    next15_r = in_nonce[127:96];
                    error_r  = 1'b0;
                end

                h0  = s0;
                h1  = s1;
                h2  = s2;
                h3  = s3;
                h4  = s4;
                h5  = s5;
                h6  = s6;
                h7  = s7;
                h8  = s8;
                h9  = s9;
                h10 = s10;
                h11 = s11;
                h12 = s12;
                h13 = s13;
                h14 = s14;
                h15 = s15;

                qr(h0, h4, h8, h12, qra_a, qra_b, qra_c, qra_d);   t0  = qra_a; t4  = qra_b; t8  = qra_c; t12 = qra_d;
                qr(h1, h5, h9, h13, qra_a, qra_b, qra_c, qra_d);   t1  = qra_a; t5  = qra_b; t9  = qra_c; t13 = qra_d;
                qr(h2, h6, h10, h14, qra_a, qra_b, qra_c, qra_d);  t2  = qra_a; t6  = qra_b; t10 = qra_c; t14 = qra_d;
                qr(h3, h7, h11, h15, qra_a, qra_b, qra_c, qra_d);  t3  = qra_a; t7  = qra_b; t11 = qra_c; t15 = qra_d;
                qr(t0, t5, t10, t15, qra_a, qra_b, qra_c, qra_d);  h0  = qra_a; h5  = qra_b; h10 = qra_c; h15 = qra_d;
                qr(t1, t6, t11, t12, qra_a, qra_b, qra_c, qra_d);  h1  = qra_a; h6  = qra_b; h11 = qra_c; h12 = qra_d;
                qr(t2, t7, t8, t13, qra_a, qra_b, qra_c, qra_d);   h2  = qra_a; h7  = qra_b; h8  = qra_c; h13 = qra_d;
                qr(t3, t4, t9, t14, qra_a, qra_b, qra_c, qra_d);   h3  = qra_a; h4  = qra_b; h9  = qra_c; h14 = qra_d;

                qr(h0, h4, h8, h12, qra_a, qra_b, qra_c, qra_d);   t0  = qra_a; t4  = qra_b; t8  = qra_c; t12 = qra_d;
                qr(h1, h5, h9, h13, qra_a, qra_b, qra_c, qra_d);   t1  = qra_a; t5  = qra_b; t9  = qra_c; t13 = qra_d;
                qr(h2, h6, h10, h14, qra_a, qra_b, qra_c, qra_d);  t2  = qra_a; t6  = qra_b; t10 = qra_c; t14 = qra_d;
                qr(h3, h7, h11, h15, qra_a, qra_b, qra_c, qra_d);  t3  = qra_a; t7  = qra_b; t11 = qra_c; t15 = qra_d;
                qr(t0, t5, t10, t15, qra_a, qra_b, qra_c, qra_d);  h0  = qra_a; h5  = qra_b; h10 = qra_c; h15 = qra_d;
                qr(t1, t6, t11, t12, qra_a, qra_b, qra_c, qra_d);  h1  = qra_a; h6  = qra_b; h11 = qra_c; h12 = qra_d;
                qr(t2, t7, t8, t13, qra_a, qra_b, qra_c, qra_d);   h2  = qra_a; h7  = qra_b; h8  = qra_c; h13 = qra_d;
                qr(t3, t4, t9, t14, qra_a, qra_b, qra_c, qra_d);   h3  = qra_a; h4  = qra_b; h9  = qra_c; h14 = qra_d;

                qr(h0, h4, h8, h12, qra_a, qra_b, qra_c, qra_d);   t0  = qra_a; t4  = qra_b; t8  = qra_c; t12 = qra_d;
                qr(h1, h5, h9, h13, qra_a, qra_b, qra_c, qra_d);   t1  = qra_a; t5  = qra_b; t9  = qra_c; t13 = qra_d;
                qr(h2, h6, h10, h14, qra_a, qra_b, qra_c, qra_d);  t2  = qra_a; t6  = qra_b; t10 = qra_c; t14 = qra_d;
                qr(h3, h7, h11, h15, qra_a, qra_b, qra_c, qra_d);  t3  = qra_a; t7  = qra_b; t11 = qra_c; t15 = qra_d;
                qr(t0, t5, t10, t15, qra_a, qra_b, qra_c, qra_d);  h0  = qra_a; h5  = qra_b; h10 = qra_c; h15 = qra_d;
                qr(t1, t6, t11, t12, qra_a, qra_b, qra_c, qra_d);  h1  = qra_a; h6  = qra_b; h11 = qra_c; h12 = qra_d;
                qr(t2, t7, t8, t13, qra_a, qra_b, qra_c, qra_d);   h2  = qra_a; h7  = qra_b; h8  = qra_c; h13 = qra_d;
                qr(t3, t4, t9, t14, qra_a, qra_b, qra_c, qra_d);   h3  = qra_a; h4  = qra_b; h9  = qra_c; h14 = qra_d;

                qr(h0, h4, h8, h12, qra_a, qra_b, qra_c, qra_d);   t0  = qra_a; t4  = qra_b; t8  = qra_c; t12 = qra_d;
                qr(h1, h5, h9, h13, qra_a, qra_b, qra_c, qra_d);   t1  = qra_a; t5  = qra_b; t9  = qra_c; t13 = qra_d;
                qr(h2, h6, h10, h14, qra_a, qra_b, qra_c, qra_d);  t2  = qra_a; t6  = qra_b; t10 = qra_c; t14 = qra_d;
                qr(h3, h7, h11, h15, qra_a, qra_b, qra_c, qra_d);  t3  = qra_a; t7  = qra_b; t11 = qra_c; t15 = qra_d;
                qr(t0, t5, t10, t15, qra_a, qra_b, qra_c, qra_d);  h0  = qra_a; h5  = qra_b; h10 = qra_c; h15 = qra_d;
                qr(t1, t6, t11, t12, qra_a, qra_b, qra_c, qra_d);  h1  = qra_a; h6  = qra_b; h11 = qra_c; h12 = qra_d;
                qr(t2, t7, t8, t13, qra_a, qra_b, qra_c, qra_d);   h2  = qra_a; h7  = qra_b; h8  = qra_c; h13 = qra_d;
                qr(t3, t4, t9, t14, qra_a, qra_b, qra_c, qra_d);   h3  = qra_a; h4  = qra_b; h9  = qra_c; h14 = qra_d;

                qr(h0, h4, h8, h12, qra_a, qra_b, qra_c, qra_d);   t0  = qra_a; t4  = qra_b; t8  = qra_c; t12 = qra_d;
                qr(h1, h5, h9, h13, qra_a, qra_b, qra_c, qra_d);   t1  = qra_a; t5  = qra_b; t9  = qra_c; t13 = qra_d;
                qr(h2, h6, h10, h14, qra_a, qra_b, qra_c, qra_d);  t2  = qra_a; t6  = qra_b; t10 = qra_c; t14 = qra_d;
                qr(h3, h7, h11, h15, qra_a, qra_b, qra_c, qra_d);  t3  = qra_a; t7  = qra_b; t11 = qra_c; t15 = qra_d;
                qr(t0, t5, t10, t15, qra_a, qra_b, qra_c, qra_d);  h0  = qra_a; h5  = qra_b; h10 = qra_c; h15 = qra_d;
                qr(t1, t6, t11, t12, qra_a, qra_b, qra_c, qra_d);  h1  = qra_a; h6  = qra_b; h11 = qra_c; h12 = qra_d;
                qr(t2, t7, t8, t13, qra_a, qra_b, qra_c, qra_d);   h2  = qra_a; h7  = qra_b; h8  = qra_c; h13 = qra_d;
                qr(t3, t4, t9, t14, qra_a, qra_b, qra_c, qra_d);   h3  = qra_a; h4  = qra_b; h9  = qra_c; h14 = qra_d;

                qr(h0, h4, h8, h12, qra_a, qra_b, qra_c, qra_d);   t0  = qra_a; t4  = qra_b; t8  = qra_c; t12 = qra_d;
                qr(h1, h5, h9, h13, qra_a, qra_b, qra_c, qra_d);   t1  = qra_a; t5  = qra_b; t9  = qra_c; t13 = qra_d;
                qr(h2, h6, h10, h14, qra_a, qra_b, qra_c, qra_d);  t2  = qra_a; t6  = qra_b; t10 = qra_c; t14 = qra_d;
                qr(h3, h7, h11, h15, qra_a, qra_b, qra_c, qra_d);  t3  = qra_a; t7  = qra_b; t11 = qra_c; t15 = qra_d;
                qr(t0, t5, t10, t15, qra_a, qra_b, qra_c, qra_d);  h0  = qra_a; h5  = qra_b; h10 = qra_c; h15 = qra_d;
                qr(t1, t6, t11, t12, qra_a, qra_b, qra_c, qra_d);  h1  = qra_a; h6  = qra_b; h11 = qra_c; h12 = qra_d;
                qr(t2, t7, t8, t13, qra_a, qra_b, qra_c, qra_d);   h2  = qra_a; h7  = qra_b; h8  = qra_c; h13 = qra_d;
                qr(t3, t4, t9, t14, qra_a, qra_b, qra_c, qra_d);   h3  = qra_a; h4  = qra_b; h9  = qra_c; h14 = qra_d;

                qr(h0, h4, h8, h12, qra_a, qra_b, qra_c, qra_d);   t0  = qra_a; t4  = qra_b; t8  = qra_c; t12 = qra_d;
                qr(h1, h5, h9, h13, qra_a, qra_b, qra_c, qra_d);   t1  = qra_a; t5  = qra_b; t9  = qra_c; t13 = qra_d;
                qr(h2, h6, h10, h14, qra_a, qra_b, qra_c, qra_d);  t2  = qra_a; t6  = qra_b; t10 = qra_c; t14 = qra_d;
                qr(h3, h7, h11, h15, qra_a, qra_b, qra_c, qra_d);  t3  = qra_a; t7  = qra_b; t11 = qra_c; t15 = qra_d;
                qr(t0, t5, t10, t15, qra_a, qra_b, qra_c, qra_d);  h0  = qra_a; h5  = qra_b; h10 = qra_c; h15 = qra_d;
                qr(t1, t6, t11, t12, qra_a, qra_b, qra_c, qra_d);  h1  = qra_a; h6  = qra_b; h11 = qra_c; h12 = qra_d;
                qr(t2, t7, t8, t13, qra_a, qra_b, qra_c, qra_d);   h2  = qra_a; h7  = qra_b; h8  = qra_c; h13 = qra_d;
                qr(t3, t4, t9, t14, qra_a, qra_b, qra_c, qra_d);   h3  = qra_a; h4  = qra_b; h9  = qra_c; h14 = qra_d;

                qr(h0, h4, h8, h12, qra_a, qra_b, qra_c, qra_d);   t0  = qra_a; t4  = qra_b; t8  = qra_c; t12 = qra_d;
                qr(h1, h5, h9, h13, qra_a, qra_b, qra_c, qra_d);   t1  = qra_a; t5  = qra_b; t9  = qra_c; t13 = qra_d;
                qr(h2, h6, h10, h14, qra_a, qra_b, qra_c, qra_d);  t2  = qra_a; t6  = qra_b; t10 = qra_c; t14 = qra_d;
                qr(h3, h7, h11, h15, qra_a, qra_b, qra_c, qra_d);  t3  = qra_a; t7  = qra_b; t11 = qra_c; t15 = qra_d;
                qr(t0, t5, t10, t15, qra_a, qra_b, qra_c, qra_d);  h0  = qra_a; h5  = qra_b; h10 = qra_c; h15 = qra_d;
                qr(t1, t6, t11, t12, qra_a, qra_b, qra_c, qra_d);  h1  = qra_a; h6  = qra_b; h11 = qra_c; h12 = qra_d;
                qr(t2, t7, t8, t13, qra_a, qra_b, qra_c, qra_d);   h2  = qra_a; h7  = qra_b; h8  = qra_c; h13 = qra_d;
                qr(t3, t4, t9, t14, qra_a, qra_b, qra_c, qra_d);   h3  = qra_a; h4  = qra_b; h9  = qra_c; h14 = qra_d;

                qr(h0, h4, h8, h12, qra_a, qra_b, qra_c, qra_d);   t0  = qra_a; t4  = qra_b; t8  = qra_c; t12 = qra_d;
                qr(h1, h5, h9, h13, qra_a, qra_b, qra_c, qra_d);   t1  = qra_a; t5  = qra_b; t9  = qra_c; t13 = qra_d;
                qr(h2, h6, h10, h14, qra_a, qra_b, qra_c, qra_d);  t2  = qra_a; t6  = qra_b; t10 = qra_c; t14 = qra_d;
                qr(h3, h7, h11, h15, qra_a, qra_b, qra_c, qra_d);  t3  = qra_a; t7  = qra_b; t11 = qra_c; t15 = qra_d;
                qr(t0, t5, t10, t15, qra_a, qra_b, qra_c, qra_d);  h0  = qra_a; h5  = qra_b; h10 = qra_c; h15 = qra_d;
                qr(t1, t6, t11, t12, qra_a, qra_b, qra_c, qra_d);  h1  = qra_a; h6  = qra_b; h11 = qra_c; h12 = qra_d;
                qr(t2, t7, t8, t13, qra_a, qra_b, qra_c, qra_d);   h2  = qra_a; h7  = qra_b; h8  = qra_c; h13 = qra_d;
                qr(t3, t4, t9, t14, qra_a, qra_b, qra_c, qra_d);   h3  = qra_a; h4  = qra_b; h9  = qra_c; h14 = qra_d;

                qr(h0, h4, h8, h12, qra_a, qra_b, qra_c, qra_d);   t0  = qra_a; t4  = qra_b; t8  = qra_c; t12 = qra_d;
                qr(h1, h5, h9, h13, qra_a, qra_b, qra_c, qra_d);   t1  = qra_a; t5  = qra_b; t9  = qra_c; t13 = qra_d;
                qr(h2, h6, h10, h14, qra_a, qra_b, qra_c, qra_d);  t2  = qra_a; t6  = qra_b; t10 = qra_c; t14 = qra_d;
                qr(h3, h7, h11, h15, qra_a, qra_b, qra_c, qra_d);  t3  = qra_a; t7  = qra_b; t11 = qra_c; t15 = qra_d;
                qr(t0, t5, t10, t15, qra_a, qra_b, qra_c, qra_d);  h0  = qra_a; h5  = qra_b; h10 = qra_c; h15 = qra_d;
                qr(t1, t6, t11, t12, qra_a, qra_b, qra_c, qra_d);  h1  = qra_a; h6  = qra_b; h11 = qra_c; h12 = qra_d;
                qr(t2, t7, t8, t13, qra_a, qra_b, qra_c, qra_d);   h2  = qra_a; h7  = qra_b; h8  = qra_c; h13 = qra_d;
                qr(t3, t4, t9, t14, qra_a, qra_b, qra_c, qra_d);   h3  = qra_a; h4  = qra_b; h9  = qra_c; h14 = qra_d;

                result_y0  = h0  + s0;
                result_y1  = h1  + s1;
                result_y2  = h2  + s2;
                result_y3  = h3  + s3;
                result_y4  = h4  + s4;
                result_y5  = h5  + s5;
                result_y6  = h6  + s6;
                result_y7  = h7  + s7;
                result_y8  = h8  + s8;
                result_y9  = h9  + s9;
                result_y10 = h10 + s10;
                result_y11 = h11 + s11;
                result_y12 = h12 + s12;
                result_y13 = h13 + s13;
                result_y14 = h14 + s14;
                result_y15 = h15 + s15;

                out_block[31:0]      <= result_y0;
                out_block[63:32]     <= result_y1;
                out_block[95:64]     <= result_y2;
                out_block[127:96]    <= result_y3;
                out_block[159:128]   <= result_y4;
                out_block[191:160]   <= result_y5;
                out_block[223:192]   <= result_y6;
                out_block[255:224]   <= result_y7;
                out_block[287:256]   <= result_y8;
                out_block[319:288]   <= result_y9;
                out_block[351:320]   <= result_y10;
                out_block[383:352]   <= result_y11;
                out_block[415:384]   <= result_y12;
                out_block[447:416]   <= result_y13;
                out_block[479:448]   <= result_y14;
                out_block[511:480]   <= result_y15;

                out_next_state_12_15[31:0]    <= next12_r;
                out_next_state_12_15[63:32]   <= next13_r;
                out_next_state_12_15[95:64]   <= next14_r;
                out_next_state_12_15[127:96]  <= next15_r;
                out_error                      <= error_r;
                busy                           <= 1'b1;
            end
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule