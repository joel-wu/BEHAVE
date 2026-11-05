module ai_ml_quant_mx_mxfp4_block_quant (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x0,
    input  logic [15:0] in_x1,
    input  logic [15:0] in_x2,
    input  logic [15:0] in_x3,
    input  logic [15:0] in_x4,
    input  logic [15:0] in_x5,
    input  logic [15:0] in_x6,
    input  logic [15:0] in_x7,
    input  logic [15:0] in_x8,
    input  logic [15:0] in_x9,
    input  logic [15:0] in_x10,
    input  logic [15:0] in_x11,
    input  logic [15:0] in_x12,
    input  logic [15:0] in_x13,
    input  logic [15:0] in_x14,
    input  logic [15:0] in_x15,
    input  logic [15:0] in_x16,
    input  logic [15:0] in_x17,
    input  logic [15:0] in_x18,
    input  logic [15:0] in_x19,
    input  logic [15:0] in_x20,
    input  logic [15:0] in_x21,
    input  logic [15:0] in_x22,
    input  logic [15:0] in_x23,
    input  logic [15:0] in_x24,
    input  logic [15:0] in_x25,
    input  logic [15:0] in_x26,
    input  logic [15:0] in_x27,
    input  logic [15:0] in_x28,
    input  logic [15:0] in_x29,
    input  logic [15:0] in_x30,
    input  logic [15:0] in_x31,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_scale,
    output logic [7:0]  out_q0,
    output logic [7:0]  out_q1,
    output logic [7:0]  out_q2,
    output logic [7:0]  out_q3,
    output logic [7:0]  out_q4,
    output logic [7:0]  out_q5,
    output logic [7:0]  out_q6,
    output logic [7:0]  out_q7,
    output logic [7:0]  out_q8,
    output logic [7:0]  out_q9,
    output logic [7:0]  out_q10,
    output logic [7:0]  out_q11,
    output logic [7:0]  out_q12,
    output logic [7:0]  out_q13,
    output logic [7:0]  out_q14,
    output logic [7:0]  out_q15
);

    logic [15:0] xs [0:31];
    logic [15:0] data0 [0:31], data1 [0:31], data2 [0:31];
    logic [7:0] pair_exp [0:15], group_exp [0:3];
    logic [7:0] maximum_exp, scale2;
    logic valid0, valid1, valid2, advance;
    logic [3:0] q [0:31];
    assign advance = !out_valid || out_ready;
    assign in_ready = advance;

    function automatic logic [7:0] max_exp(input logic [7:0] a, b);
        max_exp = a > b ? a : b;
    endfunction

    function automatic logic [3:0] quantize(input logic [15:0] x, input logic [7:0] sc);
        logic [7:0] exponent, significand;
        logic [11:0] scaled;
        logic [2:0] payload;
        begin
            exponent = x[14:7] == 0 ? 8'd1 : x[14:7];
            significand = {x[14:7] != 0, x[6:0]};
            // The block scale implies effective_exponent <= sc+2.
            // Larger shifts round to zero; retain all bits at the five
            // remaining alignments instead of synthesizing wide shifters.
            case ({1'b0, sc} + 9'd5 - {1'b0, exponent})
                9'd3: scaled = {significand, 4'b0};
                9'd4: scaled = {1'b0, significand, 3'b0};
                9'd5: scaled = {2'b0, significand, 2'b0};
                9'd6: scaled = {3'b0, significand, 1'b0};
                9'd7: scaled = {4'b0, significand};
                default: scaled = 0;
            endcase
            // Midpoints of E2M1 magnitudes, scaled by 512; ties to even.
            if      (scaled >  12'd2560) payload = 7;
            else if (scaled >= 12'd1792) payload = 6;
            else if (scaled >  12'd1280) payload = 5;
            else if (scaled >= 12'd896)  payload = 4;
            else if (scaled >  12'd640)  payload = 3;
            else if (scaled >= 12'd384)  payload = 2;
            else if (scaled >  12'd128)  payload = 1;
            else                        payload = 0;
            quantize = sc == 255 ? 4'b0 : {x[15],payload};
        end
    endfunction

    always_comb begin
        xs[0] = in_x0;
        xs[1] = in_x1;
        xs[2] = in_x2;
        xs[3] = in_x3;
        xs[4] = in_x4;
        xs[5] = in_x5;
        xs[6] = in_x6;
        xs[7] = in_x7;
        xs[8] = in_x8;
        xs[9] = in_x9;
        xs[10] = in_x10;
        xs[11] = in_x11;
        xs[12] = in_x12;
        xs[13] = in_x13;
        xs[14] = in_x14;
        xs[15] = in_x15;
        xs[16] = in_x16;
        xs[17] = in_x17;
        xs[18] = in_x18;
        xs[19] = in_x19;
        xs[20] = in_x20;
        xs[21] = in_x21;
        xs[22] = in_x22;
        xs[23] = in_x23;
        xs[24] = in_x24;
        xs[25] = in_x25;
        xs[26] = in_x26;
        xs[27] = in_x27;
        xs[28] = in_x28;
        xs[29] = in_x29;
        xs[30] = in_x30;
        xs[31] = in_x31;

        maximum_exp = max_exp(max_exp(group_exp[0], group_exp[1]),
                              max_exp(group_exp[2], group_exp[3]));
        for (integer i=0;i<32;i=i+1) q[i]=quantize(data2[i],scale2);
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            valid0 <= 0;
            valid1 <= 0;
            valid2 <= 0;
            out_valid <= 0;
            scale2 <= 0;
            for (integer i=0;i<32;i=i+1) begin
                data0[i] <= 0;
                data1[i] <= 0;
                data2[i] <= 0;
            end
            for (integer i=0;i<16;i=i+1) pair_exp[i] <= 0;
            for (integer i=0;i<4;i=i+1) group_exp[i] <= 0;
            out_scale <= 0;
            out_q0 <= 0;
            out_q1 <= 0;
            out_q2 <= 0;
            out_q3 <= 0;
            out_q4 <= 0;
            out_q5 <= 0;
            out_q6 <= 0;
            out_q7 <= 0;
            out_q8 <= 0;
            out_q9 <= 0;
            out_q10 <= 0;
            out_q11 <= 0;
            out_q12 <= 0;
            out_q13 <= 0;
            out_q14 <= 0;
            out_q15 <= 0;

        end else if (advance) begin
            // Freeze every stage on output backpressure, preserving order.
            valid0 <= in_valid;
            valid1 <= valid0;
            valid2 <= valid1;
            out_valid <= valid2;
            for (integer i=0;i<32;i=i+1) begin
                data0[i] <= xs[i];
                data1[i] <= data0[i];
                data2[i] <= data1[i];
            end
            for (integer i=0;i<16;i=i+1)
                pair_exp[i] <= max_exp(xs[2*i][14:7], xs[2*i+1][14:7]);
            for (integer i=0;i<4;i=i+1)
                group_exp[i] <= max_exp(max_exp(pair_exp[4*i], pair_exp[4*i+1]),
                                        max_exp(pair_exp[4*i+2], pair_exp[4*i+3]));
            scale2 <= maximum_exp == 255 ? 8'd255 :
                      maximum_exp < 2 ? 8'd0 : maximum_exp-8'd2;
            if (valid2) begin
            out_scale <= scale2;
            out_q0 <= {q[1],q[0]};
            out_q1 <= {q[3],q[2]};
            out_q2 <= {q[5],q[4]};
            out_q3 <= {q[7],q[6]};
            out_q4 <= {q[9],q[8]};
            out_q5 <= {q[11],q[10]};
            out_q6 <= {q[13],q[12]};
            out_q7 <= {q[15],q[14]};
            out_q8 <= {q[17],q[16]};
            out_q9 <= {q[19],q[18]};
            out_q10 <= {q[21],q[20]};
            out_q11 <= {q[23],q[22]};
            out_q12 <= {q[25],q[24]};
            out_q13 <= {q[27],q[26]};
            out_q14 <= {q[29],q[28]};
            out_q15 <= {q[31],q[30]};

            end
        end
    end
endmodule
