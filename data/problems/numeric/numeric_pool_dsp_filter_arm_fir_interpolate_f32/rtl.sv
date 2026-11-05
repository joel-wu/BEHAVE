module fir_interpolator_f32_l4_p4 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_sample,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_sample
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
function automatic logic [31:0] fp32_addsub_bits(input logic [31:0] a, b, input logic sub);
    fp32_addsub_bits = fp32_add_bits(a, b ^ {sub,31'b0});
endfunction


    // The packed history is oldest first. One multiplier/adder serves all phases.
    localparam logic [511:0] COEFF = 512'h390000003af000003c5200003d6380003e2aa0003ebbb0003f1c68003f4918003f4918003f1c68003ebbb0003e2aa0003d6380003c5200003af0000039000000;
    localparam logic [1:0] IDLE=0, MAC=1, EMIT=2;
    logic [1:0] state, phase, tap;
    logic [127:0] history;
    logic [31:0] accumulator, result;
    logic [3:0] coefficient_index;
    logic [31:0] product, next_sum;
    assign coefficient_index = {tap,2'b00} + (4'd3 - {2'b00,phase});
    assign product = fp32_mul_bits(history[32*tap +: 32], COEFF[32*coefficient_index +: 32]);
    assign next_sum = fp32_addsub_bits(accumulator, product, 1'b0);
    assign in_ready = state==IDLE;
    assign out_valid = state==EMIT;
    assign out_sample = result;

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= IDLE;
            phase <= 0;
            tap <= 0;
            history <= 0;
            accumulator <= 0;
            result <= 0;
        end else begin
            case (state)
                IDLE: if (in_valid) begin
                    history <= {in_sample,history[127:32]};
                    phase <= 0;
                    tap <= 0;
                    accumulator <= 0;
                    state <= MAC;
                end
                MAC: begin
                    accumulator <= next_sum;
                    if (tap==3) begin
                        result <= next_sum;
                        state <= EMIT;
                    end else tap <= tap+1'b1;
                end
                EMIT: if (out_ready) begin
                    if (phase==3) state <= IDLE;
                    else begin
                        phase <= phase+1'b1;
                        tap <= 0;
                        accumulator <= 0;
                        state <= MAC;
                    end
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
