module control_motion_trapezoidal_motion_profile (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic [31:0] in_xf,
    input logic [31:0] in_xi,
    input logic [31:0] in_vi,
    input logic [31:0] in_vmax,
    input logic [31:0] in_amax,
    input logic [31:0] in_dmax,
    input logic [31:0] in_t,
    input logic out_ready,
    output logic out_valid,
    output logic [31:0] out_y,
    output logic [31:0] out_yd,
    output logic [31:0] out_ydd,
    output logic [31:0] out_ta,
    output logic [31:0] out_tv,
    output logic [31:0] out_td,
    output logic [31:0] out_tf,
    output logic [0:0] out_error
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
endfunction
    function automatic logic [31:0] fp32_is_nan(input logic [31:0] x);
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

    logic [31:0] next_out_y;
    logic [31:0] next_out_yd;
    logic [31:0] next_out_ydd;
    logic [31:0] next_out_ta;
    logic [31:0] next_out_tv;
    logic [31:0] next_out_td;
    logic [31:0] next_out_tf;
    logic [0:0] next_out_error;
    function automatic [31:0] add(input logic [31:0] a,b);add=fp32_add_bits(a,b);endfunction
    function automatic [31:0] neg(input logic [31:0] a);neg=a^32'h80000000;endfunction
    function automatic [31:0] sub(input logic [31:0] a,b);sub=add(a,neg(b));endfunction
    function automatic [31:0] mul(input logic [31:0] a,b);mul=fp32_mul_bits(a,b);endfunction
    function automatic [31:0] divide(input logic [31:0] a,b);divide=fp32_div_bits(a,b);endfunction
    function automatic less(input logic [31:0] a,b);less=!fp32_is_nan(a) && !fp32_is_nan(b) && greater(b,a);endfunction
    logic [31:0] xf,xi,vi,vmax,amax,dmax,t,dx,stop,dstop,ar,dr,vr,minimum,rad,ta,tv,td,tf,ta_tv,end_accel,y,yd,ydd,numerator,elapsed,sign_word;
    logic direction,bad;
    always_comb begin
        xf=clean(in_xf);xi=clean(in_xi);vi=clean(in_vi);vmax=clean(in_vmax);amax=clean(in_amax);dmax=clean(in_dmax);t=clean(in_t);
        dx=sub(xf,xi);stop=divide(mul(vi,vi),mul(32'h40000000,dmax));dstop={vi[31],stop[30:0]};
        sign_word=sub(dx,dstop);direction=sign_word[31];sign_word={direction,31'd0};
        ar=amax^sign_word;dr=dmax^{!direction,31'd0};vr=vmax^sign_word;
        if(less(vr^sign_word,vi^sign_word)) ar=neg(ar);
        ta=divide(sub(vr,vi),ar);td=divide(neg(vr),dr);
        minimum=add(mul(mul(32'h3f000000,ta),add(vr,vi)),mul(mul(32'h3f000000,td),vr));
        rad=0;numerator=0;elapsed=0;
        if(less(dx^sign_word,minimum^sign_word)) begin
            numerator=add(mul(dr,mul(vi,vi)),mul(mul(mul(32'h40000000,ar),dr),dx));
            rad=divide(numerator,sub(dr,ar));if(less(rad,0)) rad=0;
            vr=fp32_sqrt_bits(rad)^sign_word;
            ta=divide(sub(vr,vi),ar);td=divide(neg(vr),dr);
            if(less(ta,0)) ta=0;if(less(td,0)) td=0;tv=0;
        end else tv=divide(sub(dx,minimum),vr);
        ta_tv=add(ta,tv);tf=add(ta_tv,td);
        end_accel=add(add(xi,mul(vi,ta)),mul(mul(32'h3f000000,ar),mul(ta,ta)));
        if(less(t,0)) begin y=xi;yd=vi;ydd=0;end
        else if(less(t,ta)) begin y=add(add(xi,mul(vi,t)),mul(mul(32'h3f000000,ar),mul(t,t)));yd=add(vi,mul(ar,t));ydd=ar;end
        else if(less(t,ta_tv)) begin y=add(end_accel,mul(vr,sub(t,ta)));yd=vr;ydd=0;end
        else if(less(t,tf)) begin elapsed=sub(t,tf);y=add(xf,mul(mul(32'h3f000000,dr),mul(elapsed,elapsed)));yd=mul(dr,elapsed);ydd=dr;end
        else begin y=xf;yd=0;ydd=0;end
        bad=less(vmax,0) || less(amax,0) || less(dmax,0) || vmax[30:0]==0 || amax[30:0]==0 || dmax[30:0]==0;
        bad=bad || xf[30:23]==255 || xi[30:23]==255 || vi[30:23]==255 || vmax[30:23]==255 || amax[30:23]==255 || dmax[30:23]==255 || t[30:23]==255 || dx[30:23]==255 || stop[30:23]==255 || dstop[30:23]==255 || ar[30:23]==255 || dr[30:23]==255 || vr[30:23]==255 || minimum[30:23]==255 || rad[30:23]==255 || ta[30:23]==255 || tv[30:23]==255 || td[30:23]==255 || tf[30:23]==255 || end_accel[30:23]==255 || y[30:23]==255 || yd[30:23]==255 || ydd[30:23]==255;
        next_out_y=bad ? 0 : y;
        next_out_yd=bad ? 0 : yd;
        next_out_ydd=bad ? 0 : ydd;
        next_out_ta=bad ? 0 : ta;
        next_out_tv=bad ? 0 : tv;
        next_out_td=bad ? 0 : td;
        next_out_tf=bad ? 0 : tf;
        next_out_error=bad;
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin out_valid <= 0; out_y <= 0; out_yd <= 0; out_ydd <= 0; out_ta <= 0; out_tv <= 0; out_td <= 0; out_tf <= 0; out_error <= 0; end
        else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin out_y <= next_out_y; out_yd <= next_out_yd; out_ydd <= next_out_ydd; out_ta <= next_out_ta; out_tv <= next_out_tv; out_td <= next_out_td; out_tf <= next_out_tf; out_error <= next_out_error; end
            else begin out_y <= 0; out_yd <= 0; out_ydd <= 0; out_ta <= 0; out_tv <= 0; out_td <= 0; out_tf <= 0; out_error <= 0; end
        end
    end
endmodule
