module AiMlArchSystolicPePipelinedFp (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a,
    input  logic [31:0] in_b,
    input  logic [31:0] in_d,
    input  logic        in_control_dataflow,
    input  logic        in_control_propagate,
    input  logic [4:0]  in_control_shift,
    input  logic [7:0]  in_id,
    input  logic        in_last,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_a,
    output logic [31:0] out_b,
    output logic [31:0] out_c,
    output logic        out_control_dataflow,
    output logic        out_control_propagate,
    output logic [4:0]  out_control_shift,
    output logic [7:0]  out_id,
    output logic        out_last,
    output logic        bad_dataflow
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

    logic        busy;
    logic [31:0] c1;
    logic [31:0] c2;
    logic        last_s;

    logic        flip;
    logic [4:0]  shift_offset;
    logic [31:0] shifted_c1;
    logic [31:0] shifted_c2;
    logic [31:0] mul_ab;
    logic [31:0] add_res;

    logic [31:0] next_c1;
    logic [31:0] next_c2;
    logic        next_last_s;

    logic [31:0] next_out_a;
    logic [31:0] next_out_b;
    logic [31:0] next_out_c;
    logic        next_out_control_dataflow;
    logic        next_out_control_propagate;
    logic [4:0]  next_out_control_shift;
    logic [7:0]  next_out_id;
    logic        next_out_last;
    logic        next_bad_dataflow;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        flip = (last_s != in_control_propagate);
        shift_offset = flip ? in_control_shift : 5'd0;
        shifted_c1 = c1 >> shift_offset;
        shifted_c2 = c2 >> shift_offset;
        mul_ab = 32'h00000000;
        add_res = 32'h00000000;

        next_c1 = c1;
        next_c2 = c2;
        next_last_s = in_control_propagate;

        next_out_a = in_a;
        next_out_b = 32'h00000000;
        next_out_c = 32'h00000000;
        next_out_control_dataflow = in_control_dataflow;
        next_out_control_propagate = in_control_propagate;
        next_out_control_shift = in_control_shift;
        next_out_id = in_id;
        next_out_last = in_last;
        next_bad_dataflow = 1'b0;

        if (in_control_dataflow == 1'b0) begin
            mul_ab = fp32_mul_bits(in_a, in_b);
            next_out_b = in_b;
            if (in_control_propagate == 1'b1) begin
                next_out_c = shifted_c1;
                add_res = fp32_add_bits(c2, mul_ab);
                next_c1 = in_d;
                next_c2 = add_res;
            end else begin
                next_out_c = shifted_c2;
                add_res = fp32_add_bits(c1, mul_ab);
                next_c1 = add_res;
                next_c2 = in_d;
            end
        end else begin
            if (in_control_propagate == 1'b1) begin
                next_out_c = c1;
                mul_ab = fp32_mul_bits(in_a, c2);
                next_out_b = fp32_add_bits(in_b, mul_ab);
                next_c1 = in_d;
                next_c2 = c2;
            end else begin
                next_out_c = c2;
                mul_ab = fp32_mul_bits(in_a, c1);
                next_out_b = fp32_add_bits(in_b, mul_ab);
                next_c1 = c1;
                next_c2 = in_d;
            end
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy <= 1'b0;
            c1 <= 32'h00000000;
            c2 <= 32'h00000000;
            last_s <= 1'b0;

            out_a <= 32'h00000000;
            out_b <= 32'h00000000;
            out_c <= 32'h00000000;
            out_control_dataflow <= 1'b0;
            out_control_propagate <= 1'b0;
            out_control_shift <= 5'b00000;
            out_id <= 8'h00;
            out_last <= 1'b0;
            bad_dataflow <= 1'b0;
        end else if (input_accepted) begin
            busy <= 1'b1;
            c1 <= next_c1;
            c2 <= next_c2;
            last_s <= next_last_s;

            out_a <= next_out_a;
            out_b <= next_out_b;
            out_c <= next_out_c;
            out_control_dataflow <= next_out_control_dataflow;
            out_control_propagate <= next_out_control_propagate;
            out_control_shift <= next_out_control_shift;
            out_id <= next_out_id;
            out_last <= next_out_last;
            bad_dataflow <= next_bad_dataflow;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
