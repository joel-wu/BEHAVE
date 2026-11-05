module ai_ml_tx_attn_cross_t5_cross_attention (
    input  logic          clk,
    input  logic          rst,

    input  logic          in_valid,
    output logic          in_ready,
    input  logic [127:0]  in_hidden_states,
    input  logic [511:0]  in_key_value_states,
    input  logic [127:0]  in_attention_mask,
    input  logic [127:0]  in_ln_weight,
    input  logic [511:0]  in_q_weight,
    input  logic [511:0]  in_k_weight,
    input  logic [511:0]  in_v_weight,
    input  logic [511:0]  in_o_weight,

    output logic          out_valid,
    input  logic          out_ready,
    output logic [127:0]  out_layer_output,
    output logic [127:0]  out_position_bias,
    output logic [127:0]  out_attn_weights
);

function automatic logic [63:0] fp_shift_jam(input logic [63:0] x, input integer n);
    if (n <= 0) fp_shift_jam = x;
    else if (n >= 64) fp_shift_jam = {63'b0, |x};
    else fp_shift_jam = (x >> n) | {63'b0, |(x << (64-n))};
endfunction

function automatic logic [31:0] fp_round_pack(
    input logic sign_bit, input integer exponent, input logic [63:0] sig
);
    integer e;
    logic [63:0] shifted, rounded;
    begin
        e = exponent;
        shifted = sig;
        if (e < 1) begin
            shifted = fp_shift_jam(shifted, 1-e);
            e = 1;
        end
        rounded = (shifted >> 3) + {63'b0, (shifted[2] &&
                  (shifted[1] || shifted[0] || shifted[3]))};
        if (rounded[24]) begin
            rounded = rounded >> 1;
            e = e+1;
        end
        if (e >= 255) fp_round_pack = {sign_bit, 8'hff, 23'b0};
        else if (rounded < 64'h800000)
            fp_round_pack = {sign_bit, 8'b0, rounded[22:0]};
        else fp_round_pack = {sign_bit, e[7:0], rounded[22:0]};
    end
endfunction

function automatic logic [31:0] fp32_add_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sa, sb, sign_bit;
    logic [63:0] ma, mb, hi, lo, sum;
    begin
        sa = a[31]; sb = b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
            fp32_add_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) && (b[30:0] == 31'h7f800000) && (sa != sb))
            fp32_add_bits = 32'h7fc00000;
        else if (a[30:0] == 31'h7f800000) fp32_add_bits = a;
        else if (b[30:0] == 31'h7f800000) fp32_add_bits = b;
        else if ((a[30:0] == 0) && (b[30:0] == 0))
            fp32_add_bits = {sa & sb, 31'b0};
        else begin
            if ((ea > eb) || ((ea == eb) && (ma >= mb))) begin
                hi = ma << 3; lo = fp_shift_jam(mb << 3, ea-eb);
                sign_bit = sa; e = ea;
            end else begin
                hi = mb << 3; lo = fp_shift_jam(ma << 3, eb-ea);
                sign_bit = sb; e = eb;
            end
            sum = sa == sb ? hi+lo : hi-lo;
            if (sum == 0) fp32_add_bits = 32'b0;
            else begin
                if (sum[27]) begin
                    sum = fp_shift_jam(sum, 1); e = e+1;
                end
                for (i=0; i<26; i=i+1) begin
                    if (!sum[26] && e>1) begin sum=sum<<1; e=e-1; end
                end
                fp32_add_bits = fp_round_pack(sign_bit, e, sum);
            end
        end
    end
endfunction

function automatic logic [31:0] fp32_mul_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sign_bit;
    logic [63:0] ma, mb, product, sig;
    begin
        sign_bit = a[31] ^ b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000) ||
            ((a[30:0] == 31'h7f800000) && (b[30:0] == 0)) ||
            ((b[30:0] == 31'h7f800000) && (a[30:0] == 0)))
            fp32_mul_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) || (b[30:0] == 31'h7f800000))
            fp32_mul_bits = {sign_bit, 8'hff, 23'b0};
        else if ((a[30:0] == 0) || (b[30:0] == 0))
            fp32_mul_bits = {sign_bit, 31'b0};
        else begin
            for (i=0; i<23; i=i+1) begin
                if (!ma[23]) begin ma=ma<<1; ea=ea-1; end
                if (!mb[23]) begin mb=mb<<1; eb=eb-1; end
            end
            product = ma*mb;
            e = ea+eb-127;
            if (product[47]) begin sig=fp_shift_jam(product,21); e=e+1; end
            else sig=fp_shift_jam(product,20);
            fp32_mul_bits = fp_round_pack(sign_bit, e, sig);
        end
    end
endfunction

function automatic logic [31:0] fp32_sqrt_bits(input logic [31:0] a);
    integer e, i;
    logic [63:0] mant, radicand, root, rem, trial;
    begin
        if (a[30:0] > 31'h7f800000) fp32_sqrt_bits = 32'h7fc00000;
        else if (a[30:0] == 0) fp32_sqrt_bits = a;
        else if (a[31]) fp32_sqrt_bits = 32'h7fc00000;
        else if (a[30:0] == 31'h7f800000) fp32_sqrt_bits = a;
        else begin
            e = a[30:23] == 0 ? 1 : int'(a[30:23]);
            mant = {40'b0, (a[30:23] != 0), a[22:0]};
            for (i=0; i<23; i=i+1) begin
                if (!mant[23]) begin mant=mant<<1; e=e-1; end
            end
            e = e-127;
            if (e & 1) mant = mant<<1;
            radicand = mant<<29;
            root = 0; rem = 0;
            for (i=26; i>=0; i=i-1) begin
                rem = (rem<<2) | ((radicand>>(2*i)) & 64'd3);
                trial = (root<<2) | 64'd1;
                root = root<<1;
                if (rem >= trial) begin rem=rem-trial; root=root|64'd1; end
            end
            root = root | {63'b0, (rem != 0)};
            fp32_sqrt_bits = fp_round_pack(1'b0, (e >>> 1)+127, root);
        end
    end
endfunction

function automatic logic [31:0] fp32_div_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sign_bit;
    logic [63:0] ma, mb, numerator, quotient, remainder;
    begin
        sign_bit = a[31] ^ b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000) ||
            ((a[30:0] == 31'h7f800000) && (b[30:0] == 31'h7f800000)) ||
            ((a[30:0] == 0) && (b[30:0] == 0)))
            fp32_div_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) || (b[30:0] == 0))
            fp32_div_bits = {sign_bit, 8'hff, 23'b0};
        else if ((a[30:0] == 0) || (b[30:0] == 31'h7f800000))
            fp32_div_bits = {sign_bit, 31'b0};
        else begin
            for (i=0; i<23; i=i+1) begin
                if (!ma[23]) begin ma=ma<<1; ea=ea-1; end
                if (!mb[23]) begin mb=mb<<1; eb=eb-1; end
            end
            e = ea-eb+127;
            if (ma < mb) begin ma=ma<<1; e=e-1; end
            numerator = ma<<26;
            quotient = numerator/mb;
            remainder = numerator%mb;
            quotient = quotient | {63'b0, (remainder != 0)};
            fp32_div_bits = fp_round_pack(sign_bit, e, quotient);
        end
    end
endfunction    function automatic logic [31:0] fp32_is_nan(input logic [31:0] x);
        logic [7:0] expf;
        logic [22:0] fracf;
        begin
            expf = x[30:23];
            fracf = x[22:0];
            if ((expf == 8'hff) && (fracf != 23'h0)) fp32_is_nan = 32'd1;
            else fp32_is_nan = 32'd0;
        end
    endfunction

    function automatic logic [31:0] clean(input logic [31:0] x);
    clean = x;
endfunction
    function automatic logic signed [1:0] fp32_cmp_bits(input logic [31:0] a, b);
    if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
        fp32_cmp_bits = 2'sb10; // -2: unordered, never an ordered <= result.
    else if ((a == b) || ((a[30:0] == 0) && (b[30:0] == 0))) fp32_cmp_bits = 0;
    else if (a[31] != b[31]) fp32_cmp_bits = a[31] ? -2'sd1 : 2'sd1;
    else if ((a[30:0] < b[30:0]) != a[31]) fp32_cmp_bits = -2'sd1;
    else fp32_cmp_bits = 2'sd1;
endfunction
function automatic logic greater(input logic [31:0] ax, bx);
    greater = fp32_cmp_bits(ax,bx) == 2'sd1;
endfunction
    function automatic [31:0] exp2_node(input integer index);
        case(index)
            0: exp2_node = 32'd1073741824;
            1: exp2_node = 32'd1097253708;
            2: exp2_node = 32'd1121280436;
            3: exp2_node = 32'd1145833280;
            4: exp2_node = 32'd1170923762;
            5: exp2_node = 32'd1196563654;
            6: exp2_node = 32'd1222764986;
            7: exp2_node = 32'd1249540052;
            8: exp2_node = 32'd1276901417;
            9: exp2_node = 32'd1304861917;
            10: exp2_node = 32'd1333434672;
            11: exp2_node = 32'd1362633090;
            12: exp2_node = 32'd1392470869;
            13: exp2_node = 32'd1422962010;
            14: exp2_node = 32'd1454120821;
            15: exp2_node = 32'd1485961921;
            16: exp2_node = 32'd1518500250;
            17: exp2_node = 32'd1551751076;
            18: exp2_node = 32'd1585730000;
            19: exp2_node = 32'd1620452965;
            20: exp2_node = 32'd1655936265;
            21: exp2_node = 32'd1692196547;
            22: exp2_node = 32'd1729250827;
            23: exp2_node = 32'd1767116489;
            24: exp2_node = 32'd1805811301;
            25: exp2_node = 32'd1845353420;
            26: exp2_node = 32'd1885761398;
            27: exp2_node = 32'd1927054196;
            28: exp2_node = 32'd1969251188;
            29: exp2_node = 32'd2012372174;
            30: exp2_node = 32'd2056437387;
            31: exp2_node = 32'd2101467502;
            32: exp2_node = 32'd2147483648;
            default: exp2_node=32'd2147483648;
        endcase
    endfunction
    function automatic [31:0] exp2_finite(input logic [31:0] x);
        integer exponent,shift,q,n,j,r;
        logic [31:0] magnitude,mantissa,lo,hi,significand;
        logic [63:0] y;
        logic [7:0] encoded_exp;
        begin
            magnitude=x & 32'h7fffffff; exponent=int'(x[30:23]);
            q=0;n=0;j=0;r=0;shift=0;mantissa=0;lo=0;hi=0;significand=0;y=0;encoded_exp=0;
            if (magnitude>32'h7f800000) exp2_finite=32'h7fc00000;
            else if (magnitude==32'h7f800000) exp2_finite=x[31] ? 0 : 32'h7f800000;
            else if (exponent==0) exp2_finite=32'h3f800000;
            else if (x[31] && magnitude>32'h42fc0000) exp2_finite=0;
            else if (!x[31] && magnitude>=32'h43000000) exp2_finite=32'h7f800000;
            else begin
                if (exponent<111) q=x[31] ? -1 : 0;
                else begin
                    shift=134-exponent;
                    mantissa={8'd0,1'b1,x[22:0]};
                    if (x[31]) q=-int'((mantissa+(32'd1<<shift)-1)>>shift);
                    else q=int'(mantissa>>shift);
                end
                n=q>>>16; j=(q & 65535)>>11; r=q & 2047;
                lo=exp2_node(j);hi=exp2_node(j+1);
                y={32'd0,lo}+(({32'd0,hi}-{32'd0,lo})*r>>11);
                significand=32'(y>>7);
                if (y[6:0]>7'd64 || (y[6:0]==7'd64 && significand[0])) significand=significand+1;
                if (significand==32'h1000000) begin significand=significand>>1;n=n+1;end
                encoded_exp=8'(n+127);
                exp2_finite=n>127 ? 32'h7f800000 : {1'b0,encoded_exp,significand[22:0]};
            end
        end
    endfunction
    function automatic [31:0] exp_finite(input logic [31:0] x);
        exp_finite=exp2_finite(fp32_mul_bits(x,32'h3fb8aa3b));
    endfunction

    function automatic [127:0] softmax(input logic [127:0] packed_scores);
        logic [31:0] scores[0:3], weights[0:3], maximum, total;
        logic bad;
        begin
            maximum=32'hff800000; total=0; bad=0; softmax='0;
            for (integer i=0;i<4;i=i+1) begin
                scores[i]=clean(packed_scores[32*i+:32]); weights[i]=0;
                if (fp32_is_nan(scores[i])) bad=1;
                else if (greater(scores[i],maximum)) maximum=scores[i];
            end
            if (bad) softmax={4{32'h7fc00000}};
            else if (maximum!=32'hff800000) begin
                for (integer i=0;i<4;i=i+1) begin
                    if (maximum==32'h7f800000) weights[i]=scores[i]==maximum ? 32'h3f800000 : 0;
                    else weights[i]=exp_finite(fp32_add_bits(scores[i],maximum^32'h80000000));
                    total=fp32_add_bits(total,weights[i]);
                end
                for (integer i=0;i<4;i=i+1) softmax[32*i+:32]=fp32_div_bits(weights[i],total);
            end
        end
    endfunction

    function automatic [127:0] matvec(input logic [511:0] matrix, input logic [127:0] vector);
        logic [31:0] a,b,c,d;
        begin
            matvec=0;a=0;b=0;c=0;d=0;
            for (integer r=0;r<4;r=r+1) begin
                a=fp32_mul_bits(matrix[32*(4*r)+:32],vector[0+:32]);
                b=fp32_mul_bits(matrix[32*(4*r+1)+:32],vector[32+:32]);
                c=fp32_mul_bits(matrix[32*(4*r+2)+:32],vector[64+:32]);
                d=fp32_mul_bits(matrix[32*(4*r+3)+:32],vector[96+:32]);
                matvec[32*r+:32]=fp32_add_bits(fp32_add_bits(a,b),fp32_add_bits(c,d));
            end
        end
    endfunction

    logic [127:0] next_out_layer_output, next_out_position_bias, next_out_attn_weights;
    always_comb begin
        logic [31:0] squares[0:3], norm_scale, mean, term[0:3];
        logic [127:0] norm, query, keys[0:3], values[0:3], scores, weights[0:1], context_vector, projected;
        next_out_layer_output=0;next_out_position_bias=in_attention_mask;next_out_attn_weights=0;
        norm_scale=0;mean=0;norm=0;query=0;scores=0;context_vector=0;projected=0;
        weights[0]=0;weights[1]=0;
        for (integer d=0;d<4;d=d+1) begin
            squares[d]=fp32_mul_bits(in_hidden_states[32*d+:32],in_hidden_states[32*d+:32]);term[d]=0;keys[d]=0;values[d]=0;
        end
        mean=fp32_div_bits(fp32_add_bits(fp32_add_bits(squares[0],squares[1]),fp32_add_bits(squares[2],squares[3])),32'h40800000);
        norm_scale=fp32_div_bits(32'h3f800000,fp32_sqrt_bits(fp32_add_bits(mean,32'h358637bd)));
        for (integer d=0;d<4;d=d+1) norm[32*d+:32]=fp32_mul_bits(in_ln_weight[32*d+:32],fp32_mul_bits(in_hidden_states[32*d+:32],norm_scale));
        query=matvec(in_q_weight,norm);
        for (integer t=0;t<4;t=t+1) begin
            keys[t]=matvec(in_k_weight,in_key_value_states[128*t+:128]);values[t]=matvec(in_v_weight,in_key_value_states[128*t+:128]);
        end
        for (integer h=0;h<2;h=h+1) begin
            for (integer t=0;t<4;t=t+1)
                scores[32*t+:32]=fp32_add_bits(fp32_add_bits(fp32_mul_bits(query[32*(2*h)+:32],keys[t][32*(2*h)+:32]),fp32_mul_bits(query[32*(2*h+1)+:32],keys[t][32*(2*h+1)+:32])),in_attention_mask[32*t+:32]);
            weights[h]=softmax(scores);
            for (integer d=0;d<2;d=d+1) begin
                for (integer t=0;t<4;t=t+1)
                    term[t]=(weights[h][32*t+:32]&32'h7fffffff)==0 ? 0 : fp32_mul_bits(weights[h][32*t+:32],values[t][32*(2*h+d)+:32]);
                context_vector[32*(2*h+d)+:32]=fp32_add_bits(fp32_add_bits(term[0],term[1]),fp32_add_bits(term[2],term[3]));
            end
        end
        projected=matvec(in_o_weight,context_vector);
        for (integer d=0;d<4;d=d+1) next_out_layer_output[32*d+:32]=fp32_add_bits(in_hidden_states[32*d+:32],projected[32*d+:32]);
        next_out_attn_weights=weights[0];
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin out_valid <= 0; out_layer_output <= 0; out_position_bias <= 0; out_attn_weights <= 0; end
        else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin out_layer_output <= next_out_layer_output; out_position_bias <= next_out_position_bias; out_attn_weights <= next_out_attn_weights; end
            else begin out_layer_output <= 0; out_position_bias <= 0; out_attn_weights <= 0; end
        end
    end
endmodule
