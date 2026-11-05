module data_systems_vector_search_pq_search_lut_scan (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_lut_0,
    input  logic [31:0]  in_lut_1,
    input  logic [31:0]  in_lut_2,
    input  logic [31:0]  in_lut_3,
    input  logic [31:0]  in_lut_4,
    input  logic [31:0]  in_lut_5,
    input  logic [31:0]  in_lut_6,
    input  logic [31:0]  in_lut_7,
    input  logic [31:0]  in_lut_8,
    input  logic [31:0]  in_lut_9,
    input  logic [31:0]  in_lut_10,
    input  logic [31:0]  in_lut_11,
    input  logic [31:0]  in_lut_12,
    input  logic [31:0]  in_lut_13,
    input  logic [31:0]  in_lut_14,
    input  logic [31:0]  in_lut_15,
    input  logic [31:0]  in_code_0,
    input  logic [31:0]  in_code_1,
    input  logic [31:0]  in_code_2,
    input  logic [31:0]  in_code_3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_distance
);

    logic        busy;
    logic [31:0] sel0, sel1, sel2, sel3;
    logic [31:0] sum0, sum01, sum012, sum_all;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        unique case (in_code_0[1:0])
            2'd0: sel0 = in_lut_0;
            2'd1: sel0 = in_lut_1;
            2'd2: sel0 = in_lut_2;
            2'd3: sel0 = in_lut_3;
        endcase

        unique case (in_code_1[1:0])
            2'd0: sel1 = in_lut_4;
            2'd1: sel1 = in_lut_5;
            2'd2: sel1 = in_lut_6;
            2'd3: sel1 = in_lut_7;
        endcase

        unique case (in_code_2[1:0])
            2'd0: sel2 = in_lut_8;
            2'd1: sel2 = in_lut_9;
            2'd2: sel2 = in_lut_10;
            2'd3: sel2 = in_lut_11;
        endcase

        unique case (in_code_3[1:0])
            2'd0: sel3 = in_lut_12;
            2'd1: sel3 = in_lut_13;
            2'd2: sel3 = in_lut_14;
            2'd3: sel3 = in_lut_15;
        endcase
    end

    fp32_add u_add0   (.a(32'd0), .b(sel0), .y(sum0));
    fp32_add u_add01  (.a(sum0), .b(sel1), .y(sum01));
    fp32_add u_add012 (.a(sum01), .b(sel2), .y(sum012));
    fp32_add u_addall (.a(sum012), .b(sel3), .y(sum_all));

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy         <= 1'b0;
            out_distance <= 32'h00000000;
        end else if (input_accepted) begin
            busy         <= 1'b1;
            out_distance <= sum_all;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule


module fp32_add (
    input  logic [31:0] a,
    input  logic [31:0] b,
    output logic [31:0] y
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
assign y = fp32_add_bits(a, b);
endmodule
