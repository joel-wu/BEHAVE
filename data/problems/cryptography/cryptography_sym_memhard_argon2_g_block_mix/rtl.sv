module cryptography_sym_memhard_argon2_g_block_mix (
    input  logic           clk,
    input  logic           rst,

    input  logic           in_valid,
    output logic           in_ready,
    input  logic           in_with_xor,
    input  logic [8191:0]  in_prev_block,
    input  logic [8191:0]  in_ref_block,
    input  logic [8191:0]  in_next_block,

    output logic           out_valid,
    input  logic           out_ready,
    output logic [8191:0]  out_next_block
);

    logic busy;
    logic [8191:0] computed_out;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [63:0] rotr64(input logic [63:0] x, input logic [5:0] n);
        begin
            if (n == 6'd0) begin
                rotr64 = x;
            end else begin
                rotr64 = (x >> n) | (x << (6'd64 - n));
            end
        end
    endfunction

    function automatic logic [63:0] blamka_add64(input logic [63:0] x, input logic [63:0] y);
        logic [31:0] xlo;
        logic [31:0] ylo;
        logic [63:0] prod;
        begin
            xlo = x[31:0];
            ylo = y[31:0];
            prod = xlo * ylo;
            blamka_add64 = x + y + (prod << 1);
        end
    endfunction

    function automatic [255:0] quarter_round;
        input logic [63:0] a_in;
        input logic [63:0] b_in;
        input logic [63:0] c_in;
        input logic [63:0] d_in;
        logic [63:0] a;
        logic [63:0] b;
        logic [63:0] c;
        logic [63:0] d;
        begin
            a = a_in;
            b = b_in;
            c = c_in;
            d = d_in;

            a = blamka_add64(a, b);
            d = rotr64(d ^ a, 6'd32);
            c = blamka_add64(c, d);
            b = rotr64(b ^ c, 6'd24);
            a = blamka_add64(a, b);
            d = rotr64(d ^ a, 6'd16);
            c = blamka_add64(c, d);
            b = rotr64(b ^ c, 6'd63);

            quarter_round = {a, b, c, d};
        end
    endfunction

    function automatic [1023:0] blake2_round_16;
        input logic [1023:0] vin;
        logic [63:0] v0;
        logic [63:0] v1;
        logic [63:0] v2;
        logic [63:0] v3;
        logic [63:0] v4;
        logic [63:0] v5;
        logic [63:0] v6;
        logic [63:0] v7;
        logic [63:0] v8;
        logic [63:0] v9;
        logic [63:0] v10;
        logic [63:0] v11;
        logic [63:0] v12;
        logic [63:0] v13;
        logic [63:0] v14;
        logic [63:0] v15;
        logic [255:0] qr;
        begin
            v0  = vin[64*0   +: 64];
            v1  = vin[64*1   +: 64];
            v2  = vin[64*2   +: 64];
            v3  = vin[64*3   +: 64];
            v4  = vin[64*4   +: 64];
            v5  = vin[64*5   +: 64];
            v6  = vin[64*6   +: 64];
            v7  = vin[64*7   +: 64];
            v8  = vin[64*8   +: 64];
            v9  = vin[64*9   +: 64];
            v10 = vin[64*10  +: 64];
            v11 = vin[64*11  +: 64];
            v12 = vin[64*12  +: 64];
            v13 = vin[64*13  +: 64];
            v14 = vin[64*14  +: 64];
            v15 = vin[64*15  +: 64];

            qr  = quarter_round(v0, v4, v8, v12);
            v0  = qr[255:192]; v4  = qr[191:128]; v8  = qr[127:64];  v12 = qr[63:0];
            qr  = quarter_round(v1, v5, v9, v13);
            v1  = qr[255:192]; v5  = qr[191:128]; v9  = qr[127:64];  v13 = qr[63:0];
            qr  = quarter_round(v2, v6, v10, v14);
            v2  = qr[255:192]; v6  = qr[191:128]; v10 = qr[127:64];  v14 = qr[63:0];
            qr  = quarter_round(v3, v7, v11, v15);
            v3  = qr[255:192]; v7  = qr[191:128]; v11 = qr[127:64];  v15 = qr[63:0];

            qr  = quarter_round(v0, v5, v10, v15);
            v0  = qr[255:192]; v5  = qr[191:128]; v10 = qr[127:64];  v15 = qr[63:0];
            qr  = quarter_round(v1, v6, v11, v12);
            v1  = qr[255:192]; v6  = qr[191:128]; v11 = qr[127:64];  v12 = qr[63:0];
            qr  = quarter_round(v2, v7, v8, v13);
            v2  = qr[255:192]; v7  = qr[191:128]; v8  = qr[127:64];  v13 = qr[63:0];
            qr  = quarter_round(v3, v4, v9, v14);
            v3  = qr[255:192]; v4  = qr[191:128]; v9  = qr[127:64];  v14 = qr[63:0];

            blake2_round_16 = {
                v15, v14, v13, v12, v11, v10, v9, v8,
                v7,  v6,  v5,  v4,  v3,  v2,  v1, v0
            };
        end
    endfunction

    logic [8191:0] block_r0;
    logic [8191:0] block_tmp;
    logic [8191:0] block_r;
    logic [1023:0] grp;
    logic [1023:0] grp_res;
    integer i;
    integer p;
    integer idx_word;

    always_comb begin
        block_r0    = '0;
        block_tmp   = '0;
        block_r     = '0;
        grp         = '0;
        grp_res     = '0;
        computed_out = '0;

        for (i = 0; i < 128; i = i + 1) begin
            block_r0[64*i +: 64] = in_ref_block[64*i +: 64] ^ in_prev_block[64*i +: 64];
        end

        if (in_with_xor) begin
            for (i = 0; i < 128; i = i + 1) begin
                block_tmp[64*i +: 64] = block_r0[64*i +: 64] ^ in_next_block[64*i +: 64];
            end
        end else begin
            block_tmp = block_r0;
        end

        block_r = block_r0;

        for (i = 0; i < 8; i = i + 1) begin
            grp = block_r[1024*i +: 1024];
            grp_res = blake2_round_16(grp);
            block_r[1024*i +: 1024] = grp_res;
        end

        for (i = 0; i < 8; i = i + 1) begin
            grp = '0;
            for (p = 0; p < 16; p = p + 1) begin
                idx_word = 2*i + 16*(p/2) + (p%2);
                grp[64*p +: 64] = block_r[64*idx_word +: 64];
            end
            grp_res = blake2_round_16(grp);
            for (p = 0; p < 16; p = p + 1) begin
                idx_word = 2*i + 16*(p/2) + (p%2);
                block_r[64*idx_word +: 64] = grp_res[64*p +: 64];
            end
        end

        for (i = 0; i < 128; i = i + 1) begin
            computed_out[64*i +: 64] = block_tmp[64*i +: 64] ^ block_r[64*i +: 64];
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy           <= 1'b0;
            out_next_block <= '0;
        end else if (input_accepted) begin
            busy           <= 1'b1;
            out_next_block <= computed_out;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule