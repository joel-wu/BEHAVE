module ai_ml_ssm_gla_gla_chunk #(
    parameter int B  = 1,
    parameter int T  = 1,
    parameter int H  = 1,
    parameter int K  = 1,
    parameter int V  = 1,
    parameter int BT = 16
) (
    input  logic                           clk,
    input  logic                           rst,

    input  logic                           in_valid,
    output logic                           in_ready,
    input  logic [31:0]                    in_scale,
    input  logic                           in_output_final_state,

    input  logic [B*T*H*K*32-1:0]          in_q,
    input  logic [B*T*H*K*32-1:0]          in_k,
    input  logic [B*T*H*V*32-1:0]          in_v,
    input  logic [B*T*H*K*32-1:0]          in_g,
    input  logic [B*H*K*V*32-1:0]          in_initial_state,

    output logic                           out_valid,
    input  logic                           out_ready,
    output logic [B*T*H*V*32-1:0]          out_o,
    output logic [B*H*K*V*32-1:0]          out_final_state
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

    localparam integer NQ=B*T*H*K, NV=B*T*H*V, NS=B*H*K*V;
    logic [31:0] q[0:NQ-1], k[0:NQ-1], g[0:NQ-1], gc[0:NQ-1];
    logic [31:0] v[0:NV-1], state[0:NS-1], entry[0:K*V-1];
    logic [31:0] acc, term, inter_value, intra_value, score, gate, end_g;
    logic [NV*32-1:0] next_out_o;
    logic [NS*32-1:0] next_out_final_state;
    integer ts,te;
    function automatic integer iq(input integer b,t,h,d);
        iq=(((b*T)+t)*H+h)*K+d;
    endfunction
    function automatic integer iv(input integer b,t,h,e);
        iv=(((b*T)+t)*H+h)*V+e;
    endfunction
    function automatic integer is(input integer b,h,d,e);
        is=((b*H+h)*K+d)*V+e;
    endfunction
    always_comb begin
        next_out_o=0;next_out_final_state=0;
        acc=0;term=0;inter_value=0;intra_value=0;score=0;gate=0;end_g=0;ts=0;te=0;
        for (int i=0;i<NQ;i++) begin q[i]=in_q[32*i +: 32]; k[i]=in_k[32*i +: 32]; g[i]=in_g[32*i +: 32];gc[i]=0;end
        for (int i=0;i<NV;i++) v[i]=in_v[32*i +: 32];
        for (int i=0;i<NS;i++) state[i]=in_initial_state[32*i +: 32];
        for (int i=0;i<K*V;i++) entry[i]=0;
        for (int b=0;b<B;b++) for (int h=0;h<H;h++) begin
            for (int c=0;c<(T+BT-1)/BT;c++) begin
                ts=c*BT;te=(c+1)*BT<T ? (c+1)*BT : T;
                for (int d=0;d<K;d++) begin
                    acc=0;
                    for (int t=0;t<T;t++) if(t>=ts && t<te) begin
                        acc=fp32_add_bits(acc,g[iq(b,t,h,d)]);gc[iq(b,t,h,d)]=acc;
                    end
                end
            end
            for (int c=0;c<(T+BT-1)/BT;c++) begin
                ts=c*BT;te=(c+1)*BT<T ? (c+1)*BT : T;
                for (int d=0;d<K;d++) for (int e=0;e<V;e++) entry[d*V+e]=state[is(b,h,d,e)];
                for (int t=0;t<T;t++) if(t>=ts && t<te) begin
                    for (int e=0;e<V;e++) begin
                        acc=0;
                        for (int d=0;d<K;d++) begin
                            term=fp32_mul_bits(q[iq(b,t,h,d)],exp_finite(gc[iq(b,t,h,d)]));
                            term=fp32_mul_bits(term,entry[d*V+e]);acc=fp32_add_bits(acc,term);
                        end
                        inter_value=fp32_mul_bits(acc,in_scale);intra_value=0;
                        for (int u=0;u<T;u++) if(u>=ts && u<=t) begin
                            score=0;
                            for (int d=0;d<K;d++) begin
                                term=fp32_mul_bits(q[iq(b,t,h,d)],k[iq(b,u,h,d)]);
                                gate=exp_finite(fp32_add_bits(gc[iq(b,t,h,d)],gc[iq(b,u,h,d)]^32'h80000000));
                                score=fp32_add_bits(score,fp32_mul_bits(term,gate));
                            end
                            term=fp32_mul_bits(fp32_mul_bits(score,in_scale),v[iv(b,u,h,e)]);
                            intra_value=fp32_add_bits(intra_value,term);
                        end
                        next_out_o[32*iv(b,t,h,e) +: 32]=fp32_add_bits(inter_value,intra_value);
                    end
                end
                for (int d=0;d<K;d++) begin
                    end_g=gc[iq(b,te-1,h,d)];
                    for (int e=0;e<V;e++) begin
                        acc=fp32_mul_bits(state[is(b,h,d,e)],exp_finite(end_g));
                        for (int u=0;u<T;u++) if(u>=ts && u<te) begin
                            gate=exp_finite(fp32_add_bits(end_g,gc[iq(b,u,h,d)]^32'h80000000));
                            term=fp32_mul_bits(k[iq(b,u,h,d)],v[iv(b,u,h,e)]);
                            acc=fp32_add_bits(acc,fp32_mul_bits(gate,term));
                        end
                        state[is(b,h,d,e)]=acc;
                    end
                end
            end
        end
        if(in_output_final_state) for (int i=0;i<NS;i++) next_out_final_state[32*i +: 32]=state[i];
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin out_valid <= 0; out_o <= 0; out_final_state <= 0; end
        else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin out_o <= next_out_o; out_final_state <= next_out_final_state; end
            else begin out_o <= 0; out_final_state <= 0; end
        end
    end
endmodule
