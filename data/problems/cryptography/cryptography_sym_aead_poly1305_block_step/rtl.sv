module cryptography_sym_aead_poly1305_block_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_h0,
    input  logic [31:0]  in_h1,
    input  logic [31:0]  in_h2,
    input  logic [31:0]  in_h3,
    input  logic [2:0]   in_h4,
    input  logic [31:0]  in_r0,
    input  logic [31:0]  in_r1,
    input  logic [31:0]  in_r2,
    input  logic [31:0]  in_r3,
    input  logic [31:0]  in_s0,
    input  logic [31:0]  in_s1,
    input  logic [31:0]  in_s2,
    input  logic [31:0]  in_s3,
    input  logic [127:0] in_msg,
    input  logic [4:0]   in_len,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_h0,
    output logic [31:0]  out_h1,
    output logic [31:0]  out_h2,
    output logic [31:0]  out_h3,
    output logic [2:0]   out_h4,
    output logic [31:0]  out_tag0,
    output logic [31:0]  out_tag1,
    output logic [31:0]  out_tag2,
    output logic [31:0]  out_tag3
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    logic [31:0] next_out_h0, next_out_h1, next_out_h2, next_out_h3;
    logic [2:0]  next_out_h4;
    logic [31:0] next_out_tag0, next_out_tag1, next_out_tag2, next_out_tag3;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = (~busy) | output_consumed;
    assign out_valid = busy;

    always_comb begin
        logic [4:0]   len_eff;
        logic [31:0]  h0, h1, h2, h3, h4;
        logic [31:0]  r0, r1, r2, r3;
        logic [31:0]  rr0, rr1, rr2, rr3;
        logic [31:0]  s0, s1, s2, s3, s4;
        logic [7:0]   copy0, copy1, copy2, copy3, copy4, copy5, copy6, copy7;
        logic [7:0]   copy8, copy9, copy10, copy11, copy12, copy13, copy14, copy15;
        logic [7:0]   copy16, copy17, copy18, copy19;
        logic [31:0]  m0, m1, m2, m3, m4;
        logic [32:0]  sum0a;
        logic [33:0]  sum1a, sum2a, sum3a, sum4a;
        logic [63:0]  p00, p01, p02, p03, p04;
        logic [63:0]  p10, p11, p12, p13, p14;
        logic [63:0]  p20, p21, p22, p23, p24;
        logic [63:0]  p30, p31, p32, p33, p34;
        logic [63:0]  p40;
        logic [65:0]  x0, x1, x2, x3;
        logic [33:0]  x4;
        logic [34:0]  x4_tmp;
        logic [34:0]  carry35;
        logic [66:0]  x0c, x1c, x2c;
        logic [33:0]  x3_low;
        logic [34:0]  x3c;
        logic [36:0]  x4c;
        logic [31:0]  t0, t1, t2, t3, t4;
        logic [31:0]  g0, g1, g2, g3, g4;
        logic [31:0]  mask, nmask;
        logic [32:0]  fsum0;
        logic [33:0]  fsum1, fsum2, fsum3, fsum4;
        logic [31:0]  ft0, ft1, ft2, ft3, ft4;
        logic         carry1;

        next_out_h0   = 32'h00000000;
        next_out_h1   = 32'h00000000;
        next_out_h2   = 32'h00000000;
        next_out_h3   = 32'h00000000;
        next_out_h4   = 3'b000;
        next_out_tag0 = 32'h00000000;
        next_out_tag1 = 32'h00000000;
        next_out_tag2 = 32'h00000000;
        next_out_tag3 = 32'h00000000;

        len_eff = (in_len <= 5'd16) ? in_len : 5'd16;

        h0 = in_h0;
        h1 = in_h1;
        h2 = in_h2;
        h3 = in_h3;
        h4 = {29'd0, in_h4};

        r0 = in_r0 & 32'h0FFFFFFF;
        r1 = in_r1 & 32'h0FFFFFFC;
        r2 = in_r2 & 32'h0FFFFFFC;
        r3 = in_r3 & 32'h0FFFFFFC;

        rr0 = ((r0 >> 2) * 32'd5);
        rr1 = ((r1 >> 2) * 32'd5);
        rr2 = ((r2 >> 2) * 32'd5);
        rr3 = ((r3 >> 2) * 32'd5);

        s0 = in_s0;
        s1 = in_s1;
        s2 = in_s2;
        s3 = in_s3;
        s4 = 32'h00000000;

        copy0  = 8'h00;
        copy1  = 8'h00;
        copy2  = 8'h00;
        copy3  = 8'h00;
        copy4  = 8'h00;
        copy5  = 8'h00;
        copy6  = 8'h00;
        copy7  = 8'h00;
        copy8  = 8'h00;
        copy9  = 8'h00;
        copy10 = 8'h00;
        copy11 = 8'h00;
        copy12 = 8'h00;
        copy13 = 8'h00;
        copy14 = 8'h00;
        copy15 = 8'h00;
        copy16 = 8'h00;
        copy17 = 8'h00;
        copy18 = 8'h00;
        copy19 = 8'h00;

        sum0a = 33'd0;
        sum1a = 34'd0;
        sum2a = 34'd0;
        sum3a = 34'd0;
        sum4a = 34'd0;
        p00 = 64'd0; p01 = 64'd0; p02 = 64'd0; p03 = 64'd0; p04 = 64'd0;
        p10 = 64'd0; p11 = 64'd0; p12 = 64'd0; p13 = 64'd0; p14 = 64'd0;
        p20 = 64'd0; p21 = 64'd0; p22 = 64'd0; p23 = 64'd0; p24 = 64'd0;
        p30 = 64'd0; p31 = 64'd0; p32 = 64'd0; p33 = 64'd0; p34 = 64'd0;
        p40 = 64'd0;
        x0 = 66'd0;
        x1 = 66'd0;
        x2 = 66'd0;
        x3 = 66'd0;
        x4 = 34'd0;
        x4_tmp = 35'd0;
        carry35 = 35'd0;
        x0c = 67'd0;
        x1c = 67'd0;
        x2c = 67'd0;
        x3_low = 34'd0;
        x3c = 35'd0;
        x4c = 37'd0;
        t0 = 32'd0;
        t1 = 32'd0;
        t2 = 32'd0;
        t3 = 32'd0;
        t4 = 32'd0;
        g0 = 32'd0;
        g1 = 32'd0;
        g2 = 32'd0;
        g3 = 32'd0;
        g4 = 32'd0;
        mask = 32'd0;
        nmask = 32'd0;
        fsum0 = 33'd0;
        fsum1 = 34'd0;
        fsum2 = 34'd0;
        fsum3 = 34'd0;
        fsum4 = 34'd0;
        ft0 = 32'd0;
        ft1 = 32'd0;
        ft2 = 32'd0;
        ft3 = 32'd0;
        ft4 = 32'd0;
        carry1 = 1'b0;

        if (5'd0  < len_eff) copy0  = in_msg[7:0];
        if (5'd1  < len_eff) copy1  = in_msg[15:8];
        if (5'd2  < len_eff) copy2  = in_msg[23:16];
        if (5'd3  < len_eff) copy3  = in_msg[31:24];
        if (5'd4  < len_eff) copy4  = in_msg[39:32];
        if (5'd5  < len_eff) copy5  = in_msg[47:40];
        if (5'd6  < len_eff) copy6  = in_msg[55:48];
        if (5'd7  < len_eff) copy7  = in_msg[63:56];
        if (5'd8  < len_eff) copy8  = in_msg[71:64];
        if (5'd9  < len_eff) copy9  = in_msg[79:72];
        if (5'd10 < len_eff) copy10 = in_msg[87:80];
        if (5'd11 < len_eff) copy11 = in_msg[95:88];
        if (5'd12 < len_eff) copy12 = in_msg[103:96];
        if (5'd13 < len_eff) copy13 = in_msg[111:104];
        if (5'd14 < len_eff) copy14 = in_msg[119:112];
        if (5'd15 < len_eff) copy15 = in_msg[127:120];

        unique case (len_eff)
            5'd0:  copy0  = 8'h01;
            5'd1:  copy1  = 8'h01;
            5'd2:  copy2  = 8'h01;
            5'd3:  copy3  = 8'h01;
            5'd4:  copy4  = 8'h01;
            5'd5:  copy5  = 8'h01;
            5'd6:  copy6  = 8'h01;
            5'd7:  copy7  = 8'h01;
            5'd8:  copy8  = 8'h01;
            5'd9:  copy9  = 8'h01;
            5'd10: copy10 = 8'h01;
            5'd11: copy11 = 8'h01;
            5'd12: copy12 = 8'h01;
            5'd13: copy13 = 8'h01;
            5'd14: copy14 = 8'h01;
            5'd15: copy15 = 8'h01;
            5'd16: copy16 = 8'h01;
            default: copy16 = 8'h01;
        endcase

        m0 = {copy3,  copy2,  copy1,  copy0 };
        m1 = {copy7,  copy6,  copy5,  copy4 };
        m2 = {copy11, copy10, copy9,  copy8 };
        m3 = {copy15, copy14, copy13, copy12};
        m4 = {copy19, copy18, copy17, copy16};

        if (len_eff != 5'd0) begin
            sum0a = {1'b0, h0} + {1'b0, m0};
            h0 = sum0a[31:0];
            carry1 = (h0 < m0);

            sum1a = {1'b0, h1} + {1'b0, m1} + {33'd0, carry1};
            h1 = sum1a[31:0];

            sum2a = {1'b0, h2} + {1'b0, m2} + {33'd0, sum1a[32]};
            h2 = sum2a[31:0];

            sum3a = {1'b0, h3} + {1'b0, m3} + {33'd0, sum2a[32]};
            h3 = sum3a[31:0];

            sum4a = {1'b0, h4} + {1'b0, m4} + {33'd0, sum3a[32]};
            h4 = sum4a[31:0];

            p00 = r0  * h0;
            p01 = rr0 * h4;
            p02 = rr1 * h3;
            p03 = rr2 * h2;
            p04 = rr3 * h1;
            x0  = {2'd0, p00} + {2'd0, p01} + {2'd0, p02} + {2'd0, p03} + {2'd0, p04};

            p10 = r0  * h1;
            p11 = r1  * h0;
            p12 = rr1 * h4;
            p13 = rr2 * h3;
            p14 = rr3 * h2;
            x1  = {2'd0, p10} + {2'd0, p11} + {2'd0, p12} + {2'd0, p13} + {2'd0, p14};

            p20 = r0  * h2;
            p21 = r1  * h1;
            p22 = r2  * h0;
            p23 = rr2 * h4;
            p24 = rr3 * h3;
            x2  = {2'd0, p20} + {2'd0, p21} + {2'd0, p22} + {2'd0, p23} + {2'd0, p24};

            p30 = r0  * h3;
            p31 = r1  * h2;
            p32 = r2  * h1;
            p33 = r3  * h0;
            p34 = rr3 * h4;
            x3  = {2'd0, p30} + {2'd0, p31} + {2'd0, p32} + {2'd0, p33} + {2'd0, p34};

            p40 = (r0 & 32'h00000003) * h4;
            x4  = p40[33:0];

            x4_tmp  = {1'b0, x4} + {1'b0, x3[65:32]};
            x3_low  = {2'b00, x3[31:0]};
            carry35 = ({1'b0, x4_tmp[34:2]} * 35'd5);
            x4c     = {35'd0, x4_tmp[1:0]};

            x0c = {1'b0, x0} + {{32{1'b0}}, carry35};
            h0  = x0c[31:0];

            x1c = {1'b0, x1} + {34'd0, x0c[66:32]};
            h1  = x1c[31:0];

            x2c = {1'b0, x2} + {34'd0, x1c[66:32]};
            h2  = x2c[31:0];

            x3c = {1'b0, x3_low} + {2'd0, x2c[66:32]};
            h3  = x3c[31:0];

            x4c = x4c + {34'd0, x3c[34:32]};
            h4  = x4c[31:0];
        end

        next_out_h0 = h0;
        next_out_h1 = h1;
        next_out_h2 = h2;
        next_out_h3 = h3;
        next_out_h4 = h4[2:0];

        t0 = h0;
        t1 = h1;
        t2 = h2;
        t3 = h3;
        t4 = h4;

        g0 = t0 + 32'd5;
        carry1 = (g0 < t0);
        g1 = t1 + {31'd0, carry1};
        carry1 = (g1 < t1);
        g2 = t2 + {31'd0, carry1};
        carry1 = (g2 < t2);
        g3 = t3 + {31'd0, carry1};
        carry1 = (g3 < t3);
        g4 = t4 + {31'd0, carry1} - 32'd4;
        mask = (g4[31] == 1'b0) ? 32'hFFFFFFFF : 32'h00000000;
        nmask = ~mask;
        t0 = (t0 & nmask) ^ (g0 & mask);
        t1 = (t1 & nmask) ^ (g1 & mask);
        t2 = (t2 & nmask) ^ (g2 & mask);
        t3 = (t3 & nmask) ^ (g3 & mask);
        t4 = (t4 & nmask) ^ (g4 & mask);

        g0 = t0 + 32'd5;
        carry1 = (g0 < t0);
        g1 = t1 + {31'd0, carry1};
        carry1 = (g1 < t1);
        g2 = t2 + {31'd0, carry1};
        carry1 = (g2 < t2);
        g3 = t3 + {31'd0, carry1};
        carry1 = (g3 < t3);
        g4 = t4 + {31'd0, carry1} - 32'd4;
        mask = (g4[31] == 1'b0) ? 32'hFFFFFFFF : 32'h00000000;
        nmask = ~mask;
        t0 = (t0 & nmask) ^ (g0 & mask);
        t1 = (t1 & nmask) ^ (g1 & mask);
        t2 = (t2 & nmask) ^ (g2 & mask);
        t3 = (t3 & nmask) ^ (g3 & mask);
        t4 = (t4 & nmask) ^ (g4 & mask);

        fsum0 = {1'b0, t0} + {1'b0, s0};
        ft0   = fsum0[31:0];
        carry1 = (ft0 < s0);

        fsum1 = {1'b0, t1} + {1'b0, s1} + {33'd0, carry1};
        ft1   = fsum1[31:0];

        fsum2 = {1'b0, t2} + {1'b0, s2} + {33'd0, fsum1[32]};
        ft2   = fsum2[31:0];

        fsum3 = {1'b0, t3} + {1'b0, s3} + {33'd0, fsum2[32]};
        ft3   = fsum3[31:0];

        fsum4 = {1'b0, t4} + {1'b0, s4} + {33'd0, fsum3[32]};
        ft4   = fsum4[31:0];
        ft4   = 32'h00000000;

        next_out_tag0 = ft0;
        next_out_tag1 = ft1;
        next_out_tag2 = ft2;
        next_out_tag3 = ft3;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy     <= 1'b0;
            out_h0   <= 32'h00000000;
            out_h1   <= 32'h00000000;
            out_h2   <= 32'h00000000;
            out_h3   <= 32'h00000000;
            out_h4   <= 3'b000;
            out_tag0 <= 32'h00000000;
            out_tag1 <= 32'h00000000;
            out_tag2 <= 32'h00000000;
            out_tag3 <= 32'h00000000;
        end else if (input_accepted) begin
            busy     <= 1'b1;
            out_h0   <= next_out_h0;
            out_h1   <= next_out_h1;
            out_h2   <= next_out_h2;
            out_h3   <= next_out_h3;
            out_h4   <= next_out_h4;
            out_tag0 <= next_out_tag0;
            out_tag1 <= next_out_tag1;
            out_tag2 <= next_out_tag2;
            out_tag3 <= next_out_tag3;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule