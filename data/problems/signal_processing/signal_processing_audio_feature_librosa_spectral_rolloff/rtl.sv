module spectral_rolloff (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_roll_percent,

    input  logic [31:0]  in_S_0,
    input  logic [31:0]  in_S_1,
    input  logic [31:0]  in_S_2,
    input  logic [31:0]  in_S_3,
    input  logic [31:0]  in_S_4,
    input  logic [31:0]  in_S_5,
    input  logic [31:0]  in_S_6,
    input  logic [31:0]  in_S_7,
    input  logic [31:0]  in_S_8,
    input  logic [31:0]  in_S_9,
    input  logic [31:0]  in_S_10,
    input  logic [31:0]  in_S_11,
    input  logic [31:0]  in_S_12,
    input  logic [31:0]  in_S_13,
    input  logic [31:0]  in_S_14,
    input  logic [31:0]  in_S_15,

    input  logic [31:0]  in_freq_0,
    input  logic [31:0]  in_freq_1,
    input  logic [31:0]  in_freq_2,
    input  logic [31:0]  in_freq_3,
    input  logic [31:0]  in_freq_4,
    input  logic [31:0]  in_freq_5,
    input  logic [31:0]  in_freq_6,
    input  logic [31:0]  in_freq_7,
    input  logic [31:0]  in_freq_8,
    input  logic [31:0]  in_freq_9,
    input  logic [31:0]  in_freq_10,
    input  logic [31:0]  in_freq_11,
    input  logic [31:0]  in_freq_12,
    input  logic [31:0]  in_freq_13,
    input  logic [31:0]  in_freq_14,
    input  logic [31:0]  in_freq_15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_rolloff
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    logic [35:0] total_energy;
    logic [51:0] threshold_num;
    logic [35:0] cumulative0;
    logic [35:0] cumulative1;
    logic [35:0] cumulative2;
    logic [35:0] cumulative3;
    logic [35:0] cumulative4;
    logic [35:0] cumulative5;
    logic [35:0] cumulative6;
    logic [35:0] cumulative7;
    logic [35:0] cumulative8;
    logic [35:0] cumulative9;
    logic [35:0] cumulative10;
    logic [35:0] cumulative11;
    logic [35:0] cumulative12;
    logic [35:0] cumulative13;
    logic [35:0] cumulative14;

    logic [51:0] cumulative0_shift;
    logic [51:0] cumulative1_shift;
    logic [51:0] cumulative2_shift;
    logic [51:0] cumulative3_shift;
    logic [51:0] cumulative4_shift;
    logic [51:0] cumulative5_shift;
    logic [51:0] cumulative6_shift;
    logic [51:0] cumulative7_shift;
    logic [51:0] cumulative8_shift;
    logic [51:0] cumulative9_shift;
    logic [51:0] cumulative10_shift;
    logic [51:0] cumulative11_shift;
    logic [51:0] cumulative12_shift;
    logic [51:0] cumulative13_shift;
    logic [51:0] cumulative14_shift;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        total_energy = 36'd0
                     + {4'd0, in_S_0}  + {4'd0, in_S_1}
                     + {4'd0, in_S_2}  + {4'd0, in_S_3}
                     + {4'd0, in_S_4}  + {4'd0, in_S_5}
                     + {4'd0, in_S_6}  + {4'd0, in_S_7}
                     + {4'd0, in_S_8}  + {4'd0, in_S_9}
                     + {4'd0, in_S_10} + {4'd0, in_S_11}
                     + {4'd0, in_S_12} + {4'd0, in_S_13}
                     + {4'd0, in_S_14} + {4'd0, in_S_15};

        threshold_num = {16'd0, in_roll_percent} * {16'd0, total_energy};

        cumulative0  = {4'd0, in_S_0};
        cumulative1  = cumulative0  + {4'd0, in_S_1};
        cumulative2  = cumulative1  + {4'd0, in_S_2};
        cumulative3  = cumulative2  + {4'd0, in_S_3};
        cumulative4  = cumulative3  + {4'd0, in_S_4};
        cumulative5  = cumulative4  + {4'd0, in_S_5};
        cumulative6  = cumulative5  + {4'd0, in_S_6};
        cumulative7  = cumulative6  + {4'd0, in_S_7};
        cumulative8  = cumulative7  + {4'd0, in_S_8};
        cumulative9  = cumulative8  + {4'd0, in_S_9};
        cumulative10 = cumulative9  + {4'd0, in_S_10};
        cumulative11 = cumulative10 + {4'd0, in_S_11};
        cumulative12 = cumulative11 + {4'd0, in_S_12};
        cumulative13 = cumulative12 + {4'd0, in_S_13};
        cumulative14 = cumulative13 + {4'd0, in_S_14};

        cumulative0_shift  = {cumulative0,  16'd0};
        cumulative1_shift  = {cumulative1,  16'd0};
        cumulative2_shift  = {cumulative2,  16'd0};
        cumulative3_shift  = {cumulative3,  16'd0};
        cumulative4_shift  = {cumulative4,  16'd0};
        cumulative5_shift  = {cumulative5,  16'd0};
        cumulative6_shift  = {cumulative6,  16'd0};
        cumulative7_shift  = {cumulative7,  16'd0};
        cumulative8_shift  = {cumulative8,  16'd0};
        cumulative9_shift  = {cumulative9,  16'd0};
        cumulative10_shift = {cumulative10, 16'd0};
        cumulative11_shift = {cumulative11, 16'd0};
        cumulative12_shift = {cumulative12, 16'd0};
        cumulative13_shift = {cumulative13, 16'd0};
        cumulative14_shift = {cumulative14, 16'd0};
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy        <= 1'b0;
            out_rolloff <= 32'd0;
        end else if (input_accepted) begin
            if (cumulative0_shift >= threshold_num) begin
                out_rolloff <= in_freq_0;
            end else if (cumulative1_shift >= threshold_num) begin
                out_rolloff <= in_freq_1;
            end else if (cumulative2_shift >= threshold_num) begin
                out_rolloff <= in_freq_2;
            end else if (cumulative3_shift >= threshold_num) begin
                out_rolloff <= in_freq_3;
            end else if (cumulative4_shift >= threshold_num) begin
                out_rolloff <= in_freq_4;
            end else if (cumulative5_shift >= threshold_num) begin
                out_rolloff <= in_freq_5;
            end else if (cumulative6_shift >= threshold_num) begin
                out_rolloff <= in_freq_6;
            end else if (cumulative7_shift >= threshold_num) begin
                out_rolloff <= in_freq_7;
            end else if (cumulative8_shift >= threshold_num) begin
                out_rolloff <= in_freq_8;
            end else if (cumulative9_shift >= threshold_num) begin
                out_rolloff <= in_freq_9;
            end else if (cumulative10_shift >= threshold_num) begin
                out_rolloff <= in_freq_10;
            end else if (cumulative11_shift >= threshold_num) begin
                out_rolloff <= in_freq_11;
            end else if (cumulative12_shift >= threshold_num) begin
                out_rolloff <= in_freq_12;
            end else if (cumulative13_shift >= threshold_num) begin
                out_rolloff <= in_freq_13;
            end else if (cumulative14_shift >= threshold_num) begin
                out_rolloff <= in_freq_14;
            end else begin
                out_rolloff <= in_freq_15;
            end
            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule