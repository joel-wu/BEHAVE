module grouped_conv_resnext_grouped_conv (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [1:0]   in_group,
    input  logic [1:0]   in_out_chan_in_group,
    input  logic signed [7:0] in_act_0,
    input  logic signed [7:0] in_act_1,
    input  logic signed [7:0] in_act_2,
    input  logic signed [7:0] in_act_3,
    input  logic signed [7:0] in_act_4,
    input  logic signed [7:0] in_act_5,
    input  logic signed [7:0] in_act_6,
    input  logic signed [7:0] in_act_7,
    input  logic signed [7:0] in_act_8,
    input  logic signed [7:0] in_act_9,
    input  logic signed [7:0] in_act_10,
    input  logic signed [7:0] in_act_11,
    input  logic signed [7:0] in_act_12,
    input  logic signed [7:0] in_act_13,
    input  logic signed [7:0] in_act_14,
    input  logic signed [7:0] in_act_15,
    input  logic signed [7:0] in_act_16,
    input  logic signed [7:0] in_act_17,
    input  logic signed [7:0] in_act_18,
    input  logic signed [7:0] in_act_19,
    input  logic signed [7:0] in_act_20,
    input  logic signed [7:0] in_act_21,
    input  logic signed [7:0] in_act_22,
    input  logic signed [7:0] in_act_23,
    input  logic signed [7:0] in_act_24,
    input  logic signed [7:0] in_act_25,
    input  logic signed [7:0] in_act_26,
    input  logic signed [7:0] in_act_27,
    input  logic signed [7:0] in_act_28,
    input  logic signed [7:0] in_act_29,
    input  logic signed [7:0] in_act_30,
    input  logic signed [7:0] in_act_31,
    input  logic signed [7:0] in_act_32,
    input  logic signed [7:0] in_act_33,
    input  logic signed [7:0] in_act_34,
    input  logic signed [7:0] in_act_35,

    input  logic         cfg_valid,
    input  logic         cfg_we,
    input  logic [9:0]   cfg_addr,
    input  logic signed [7:0] cfg_wdata,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [21:0] out_sum
);

    localparam int NUM_GROUPS    = 4;
    localparam int OC_PER_GROUP  = 4;
    localparam int TAPS          = 9;
    localparam int IC_PER_GROUP  = 4;
    localparam int NUM_WEIGHTS   = NUM_GROUPS * OC_PER_GROUP * TAPS * IC_PER_GROUP;  // 576

    // Linearized weight memory; index = ((g*4 + co)*9 + tap)*4 + ci.
    logic signed [7:0] weights [0:NUM_WEIGHTS-1];

    logic busy;
    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;
    wire cfg_write_hits  = input_accepted & cfg_valid & cfg_we & (cfg_addr < NUM_WEIGHTS[9:0]);

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    // Address decode for the input transaction.
    // base_idx = (g*4 + co)*36; max = (4*4-1)*36 = 540 < 1024.
    wire [9:0] base_idx;
    assign base_idx = ({6'd0, in_group, in_out_chan_in_group} * 10'd36);

    function automatic logic signed [7:0] eff_w(input logic [9:0] idx);
        if (cfg_write_hits && cfg_addr == idx) begin
            eff_w = cfg_wdata;
        end else begin
            eff_w = weights[idx];
        end
    endfunction

    logic signed [21:0] dot_next;
    logic signed [7:0]  acts_arr [0:35];
    integer n;
    logic signed [16:0] term;

    always_comb begin
        acts_arr[0]  = in_act_0;  acts_arr[1]  = in_act_1;  acts_arr[2]  = in_act_2;  acts_arr[3]  = in_act_3;
        acts_arr[4]  = in_act_4;  acts_arr[5]  = in_act_5;  acts_arr[6]  = in_act_6;  acts_arr[7]  = in_act_7;
        acts_arr[8]  = in_act_8;  acts_arr[9]  = in_act_9;  acts_arr[10] = in_act_10; acts_arr[11] = in_act_11;
        acts_arr[12] = in_act_12; acts_arr[13] = in_act_13; acts_arr[14] = in_act_14; acts_arr[15] = in_act_15;
        acts_arr[16] = in_act_16; acts_arr[17] = in_act_17; acts_arr[18] = in_act_18; acts_arr[19] = in_act_19;
        acts_arr[20] = in_act_20; acts_arr[21] = in_act_21; acts_arr[22] = in_act_22; acts_arr[23] = in_act_23;
        acts_arr[24] = in_act_24; acts_arr[25] = in_act_25; acts_arr[26] = in_act_26; acts_arr[27] = in_act_27;
        acts_arr[28] = in_act_28; acts_arr[29] = in_act_29; acts_arr[30] = in_act_30; acts_arr[31] = in_act_31;
        acts_arr[32] = in_act_32; acts_arr[33] = in_act_33; acts_arr[34] = in_act_34; acts_arr[35] = in_act_35;

        dot_next = 22'sd0;
        for (n = 0; n < 36; n = n + 1) begin
            term = $signed(acts_arr[n]) * $signed(eff_w(base_idx + n[9:0]));
            dot_next = dot_next + {{(22-17){term[16]}}, term};
        end
    end

    integer wi;
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy    <= 1'b0;
            out_sum <= 22'sd0;
            for (wi = 0; wi < NUM_WEIGHTS; wi = wi + 1) begin
                // Identity-ish default: weight = 1 only at tap == 4 and ci == 0;
                // i.e. linear index modulo 36 equals 16 (tap*4 + ci = 4*4 + 0 = 16).
                weights[wi] <= (wi % 36 == 16) ? 8'sd1 : 8'sd0;
            end
        end else begin
            if (cfg_write_hits) begin
                weights[cfg_addr] <= cfg_wdata;
            end
            if (input_accepted) begin
                busy    <= 1'b1;
                out_sum <= dot_next;
            end else if (output_consumed) begin
                busy <= 1'b0;
            end
        end
    end

endmodule
