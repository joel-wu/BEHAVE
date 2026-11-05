module signal_processing_audio_codec_opus_celt_mathops (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [2:0]  in_op,
    input  logic [31:0] in_x,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y
);

    logic busy;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic signed [15:0] trunc16(input logic signed [31:0] v);
        logic signed [15:0] t;
        begin
            t = v[15:0];
            trunc16 = t;
        end
    endfunction

    function automatic logic signed [31:0] trunc32(input logic signed [63:0] v);
        logic signed [31:0] t;
        begin
            t = v[31:0];
            trunc32 = t;
        end
    endfunction

    function automatic logic signed [15:0] add16(
        input logic signed [15:0] a,
        input logic signed [15:0] b
    );
        logic signed [31:0] s;
        begin
            s = a + b;
            add16 = trunc16(s);
        end
    endfunction

    function automatic logic signed [15:0] sub16(
        input logic signed [15:0] a,
        input logic signed [15:0] b
    );
        logic signed [31:0] d;
        begin
            d = a - b;
            sub16 = trunc16(d);
        end
    endfunction

    function automatic logic signed [31:0] add32(
        input logic signed [31:0] a,
        input logic signed [31:0] b
    );
        logic signed [63:0] s;
        begin
            s = a + b;
            add32 = trunc32(s);
        end
    endfunction

    function automatic logic signed [31:0] sub32(
        input logic signed [31:0] a,
        input logic signed [31:0] b
    );
        logic signed [63:0] d;
        begin
            d = a - b;
            sub32 = trunc32(d);
        end
    endfunction

    function automatic logic signed [15:0] shl16(
        input logic signed [15:0] a,
        input integer s
    );
        logic signed [31:0] tmp;
        begin
            if (s >= 0) begin
                tmp = a <<< s;
                shl16 = trunc16(tmp);
            end else begin
                tmp = a >>> (-s);
                shl16 = trunc16(tmp);
            end
        end
    endfunction

    function automatic logic signed [31:0] shl32(
        input logic signed [31:0] a,
        input integer s
    );
        logic signed [63:0] tmp;
        begin
            if (s >= 0) begin
                tmp = a <<< s;
                shl32 = trunc32(tmp);
            end else begin
                tmp = a >>> (-s);
                shl32 = trunc32(tmp);
            end
        end
    endfunction

    function automatic logic signed [31:0] shr32(
        input logic signed [31:0] a,
        input integer s
    );
        logic signed [63:0] tmp;
        begin
            if (s >= 0) begin
                tmp = a >>> s;
                shr32 = trunc32(tmp);
            end else begin
                tmp = a <<< (-s);
                shr32 = trunc32(tmp);
            end
        end
    endfunction

    function automatic logic signed [31:0] vshr32(
        input logic signed [31:0] a,
        input integer s
    );
        logic signed [63:0] tmp;
        begin
            if (s >= 0) begin
                tmp = a >>> s;
                vshr32 = trunc32(tmp);
            end else begin
                tmp = a <<< (-s);
                vshr32 = trunc32(tmp);
            end
        end
    endfunction

    function automatic logic signed [31:0] pshr32(
        input logic signed [31:0] a,
        input integer s
    );
        logic signed [63:0] tmp;
        logic signed [63:0] rnd;
        begin
            if (s > 0) begin
                rnd = 64'sd1 <<< (s - 1);
                tmp = (a + rnd) >>> s;
                pshr32 = trunc32(tmp);
            end else begin
                tmp = a <<< (-s);
                pshr32 = trunc32(tmp);
            end
        end
    endfunction

    function automatic logic signed [15:0] mult16_16_q15(
        input logic signed [15:0] a,
        input logic signed [15:0] b
    );
        logic signed [31:0] p;
        logic signed [31:0] q;
        begin
            p = a * b;
            q = p >>> 15;
            mult16_16_q15 = trunc16(q);
        end
    endfunction

    function automatic logic signed [31:0] mult32_32_q31(
        input logic signed [31:0] a,
        input logic signed [31:0] b
    );
        logic signed [63:0] p;
        logic signed [63:0] q;
        begin
            p = a * b;
            q = p >>> 31;
            mult32_32_q31 = trunc32(q);
        end
    endfunction

    function automatic integer celt_ilog2_u32(input logic [31:0] x);
        begin
            celt_ilog2_u32 = 0;
            if (x[31])      celt_ilog2_u32 = 32;
            else if (x[30]) celt_ilog2_u32 = 31;
            else if (x[29]) celt_ilog2_u32 = 30;
            else if (x[28]) celt_ilog2_u32 = 29;
            else if (x[27]) celt_ilog2_u32 = 28;
            else if (x[26]) celt_ilog2_u32 = 27;
            else if (x[25]) celt_ilog2_u32 = 26;
            else if (x[24]) celt_ilog2_u32 = 25;
            else if (x[23]) celt_ilog2_u32 = 24;
            else if (x[22]) celt_ilog2_u32 = 23;
            else if (x[21]) celt_ilog2_u32 = 22;
            else if (x[20]) celt_ilog2_u32 = 21;
            else if (x[19]) celt_ilog2_u32 = 20;
            else if (x[18]) celt_ilog2_u32 = 19;
            else if (x[17]) celt_ilog2_u32 = 18;
            else if (x[16]) celt_ilog2_u32 = 17;
            else if (x[15]) celt_ilog2_u32 = 16;
            else if (x[14]) celt_ilog2_u32 = 15;
            else if (x[13]) celt_ilog2_u32 = 14;
            else if (x[12]) celt_ilog2_u32 = 13;
            else if (x[11]) celt_ilog2_u32 = 12;
            else if (x[10]) celt_ilog2_u32 = 11;
            else if (x[9])  celt_ilog2_u32 = 10;
            else if (x[8])  celt_ilog2_u32 = 9;
            else if (x[7])  celt_ilog2_u32 = 8;
            else if (x[6])  celt_ilog2_u32 = 7;
            else if (x[5])  celt_ilog2_u32 = 6;
            else if (x[4])  celt_ilog2_u32 = 5;
            else if (x[3])  celt_ilog2_u32 = 4;
            else if (x[2])  celt_ilog2_u32 = 3;
            else if (x[1])  celt_ilog2_u32 = 2;
            else if (x[0])  celt_ilog2_u32 = 1;
        end
    endfunction

    function automatic logic signed [15:0] celt_rsqrt_norm16_stage(
        input logic signed [15:0] x16
    );
        logic signed [15:0] n;
        logic signed [15:0] r;
        logic signed [15:0] r2;
        logic signed [15:0] y;
        logic signed [15:0] t0;
        logic signed [15:0] t1;
        logic signed [15:0] t2;
        begin
            n  = trunc16($signed(x16) - 32'sd32768);
            t0 = mult16_16_q15(n, 16'sd6713);
            t1 = add16(-16'sd13490, t0);
            t2 = mult16_16_q15(n, t1);
            r  = add16(16'sd23557, t2);
            r2 = mult16_16_q15(r, r);
            y  = shl16(sub16(add16(mult16_16_q15(r2, n), r2), 16'sd16384), 1);
            t0 = mult16_16_q15(y, 16'sd12288);
            t1 = sub16(t0, 16'sd16384);
            t2 = mult16_16_q15(y, t1);
            celt_rsqrt_norm16_stage = add16(r, mult16_16_q15(r, t2));
        end
    endfunction

    function automatic logic signed [31:0] celt_rsqrt_norm32_fn(
        input logic signed [31:0] x
    );
        logic signed [15:0] x16;
        logic signed [15:0] r0;
        logic signed [31:0] r0_32;
        logic signed [31:0] r_q29;
        logic signed [31:0] tmp;
        begin
            if (x <= 0) begin
                celt_rsqrt_norm32_fn = 32'sd0;
            end else begin
                x16   = trunc16(shr32(x, 15));
                r0    = celt_rsqrt_norm16_stage(x16);
                r0_32 = {{16{r0[15]}}, r0};
                r_q29 = shl32(r0_32, 15);
                tmp   = mult32_32_q31(r_q29, r_q29);
                tmp   = mult32_32_q31(32'sd1073741824, tmp);
                tmp   = mult32_32_q31(x, tmp);
                celt_rsqrt_norm32_fn = shl32(mult32_32_q31(r_q29, sub32(32'sd201326592, tmp)), 4);
            end
        end
    endfunction

    function automatic logic signed [15:0] celt_rcp_norm16_fn(
        input logic signed [15:0] x16
    );
        logic signed [15:0] r;
        logic signed [15:0] t;
        begin
            r = add16(16'sd30840, mult16_16_q15(-16'sd15420, x16));
            t = add16(mult16_16_q15(r, x16), add16(r, -16'sd32768));
            r = sub16(r, mult16_16_q15(r, t));
            t = add16(mult16_16_q15(r, x16), add16(r, -16'sd32768));
            celt_rcp_norm16_fn = sub16(r, add16(16'sd1, mult16_16_q15(r, t)));
        end
    endfunction

    logic [2:0] op_c;
    logic [31:0] x_u_c;
    logic signed [31:0] x_s_c;
    logic [31:0] y_u_c;
    logic signed [31:0] y_s_c;
    integer k_c;
    integer i_c;
    integer shift_c;
    logic [31:0] g_c;
    logic [31:0] val_u_c;
    logic [31:0] b_u_c;
    logic [63:0] t_u_c;
    logic signed [31:0] x_frac_c;
    logic signed [31:0] tmp32_c;
    logic signed [31:0] x_sq_q29_c;
    logic signed [31:0] r_q30_c;
    logic signed [15:0] x16_c;
    logic signed [15:0] r16_c;
    logic signed [31:0] r16_32_c;
    logic signed [31:0] ax_c;

    always_comb begin
        op_c       = in_op & 3'b111;
        x_u_c      = in_x;
        x_s_c      = in_x;
        y_u_c      = 32'd0;
        y_s_c      = 32'sd0;
        k_c        = 0;
        i_c        = 0;
        shift_c    = 0;
        g_c        = 32'd0;
        val_u_c    = 32'd0;
        b_u_c      = 32'd0;
        t_u_c      = 64'd0;
        x_frac_c   = 32'sd0;
        tmp32_c    = 32'sd0;
        x_sq_q29_c = 32'sd0;
        r_q30_c    = 32'sd0;
        x16_c      = 16'sd0;
        r16_c      = 16'sd0;
        r16_32_c   = 32'sd0;
        ax_c       = 32'sd0;

        unique case (op_c)
            3'd0: begin
                if (x_u_c == 32'd0) begin
                    y_u_c = 32'd0;
                end else begin
                    val_u_c = x_u_c;
                    g_c     = 32'd0;
                    k_c     = (celt_ilog2_u32(val_u_c) - 1) >> 1;
                    b_u_c   = 32'd1 << k_c;
                    for (i_c = 0; i_c < 16; i_c = i_c + 1) begin
                        shift_c = k_c - i_c;
                        if (shift_c >= 0) begin
                            t_u_c = ({32'd0, ((g_c << 1) + b_u_c)} << shift_c);
                            if (t_u_c <= {32'd0, val_u_c}) begin
                                g_c     = g_c + b_u_c;
                                val_u_c = val_u_c - t_u_c[31:0];
                            end
                            b_u_c = b_u_c >> 1;
                        end
                    end
                    y_u_c = g_c;
                end
            end

            3'd1: begin
                y_s_c = celt_rsqrt_norm32_fn(x_s_c);
                y_u_c = y_s_c;
            end

            3'd2: begin
                if (x_s_c <= 0) begin
                    y_s_c = 32'sd0;
                end else if (x_s_c >= 32'sd1073741824) begin
                    y_s_c = 32'sd2147483647;
                end else begin
                    k_c      = (celt_ilog2_u32(x_u_c) - 1) >> 1;
                    x_frac_c = vshr32(x_s_c, 2 * (k_c - 14) - 1);
                    x_frac_c = mult32_32_q31(celt_rsqrt_norm32_fn(x_frac_c), x_frac_c);
                    if (k_c < 12) begin
                        y_s_c = pshr32(x_frac_c, 12 - k_c);
                    end else begin
                        y_s_c = shl32(x_frac_c, k_c - 12);
                    end
                end
                y_u_c = y_s_c;
            end

            3'd3: begin
                if (x_s_c >= 0) ax_c = x_s_c;
                else            ax_c = -x_s_c;

                if (ax_c == 32'sd1073741824) begin
                    y_s_c = 32'sd0;
                end else begin
                    x_sq_q29_c = mult32_32_q31(x_s_c, x_s_c);
                    tmp32_c    = add32(-32'sd178761936, mult32_32_q31(x_sq_q29_c, 32'sd29487206));
                    tmp32_c    = add32(32'sd544710848,  mult32_32_q31(x_sq_q29_c, tmp32_c));
                    tmp32_c    = add32(-32'sd662336704, mult32_32_q31(x_sq_q29_c, tmp32_c));
                    y_s_c      = shl32(add32(32'sd134217720, mult32_32_q31(x_sq_q29_c, tmp32_c)), 4);
                end
                y_u_c = y_s_c;
            end

            3'd4: begin
                if (x_s_c < 32'sd1073741824) begin
                    y_s_c = 32'sd0;
                end else begin
                    x16_c    = trunc16(shr32(x_s_c, 15) - 32'sd32768);
                    r16_c    = celt_rcp_norm16_fn(x16_c);
                    r16_32_c = {{16{r16_c[15]}}, r16_c};
                    r_q30_c  = shl32(r16_32_c, 16);
                    tmp32_c  = mult32_32_q31(r_q30_c, x_s_c);
                    tmp32_c  = add32(tmp32_c, -32'sd1073741824);
                    tmp32_c  = mult32_32_q31(tmp32_c, r_q30_c);
                    y_s_c    = sub32(r_q30_c, add32(shl32(tmp32_c, 1), 32'sd1));
                end
                y_u_c = y_s_c;
            end

            3'd5: begin
                x16_c = in_x[15:0];
                r16_c = celt_rcp_norm16_fn(x16_c);
                y_s_c = {{16{r16_c[15]}}, r16_c};
                y_u_c = y_s_c;
            end

            3'd6: begin
                y_u_c = 32'd0;
            end

            3'd7: begin
                y_u_c = 32'd0;
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy  <= 1'b0;
            out_y <= 32'd0;
        end else if (input_accepted) begin
            busy  <= 1'b1;
            out_y <= y_u_c;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
