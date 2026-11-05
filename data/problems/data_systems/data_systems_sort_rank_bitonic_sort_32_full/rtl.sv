module bitonic_sort_u32x16 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_data_0,
    input  logic [31:0]  in_data_1,
    input  logic [31:0]  in_data_2,
    input  logic [31:0]  in_data_3,
    input  logic [31:0]  in_data_4,
    input  logic [31:0]  in_data_5,
    input  logic [31:0]  in_data_6,
    input  logic [31:0]  in_data_7,
    input  logic [31:0]  in_data_8,
    input  logic [31:0]  in_data_9,
    input  logic [31:0]  in_data_10,
    input  logic [31:0]  in_data_11,
    input  logic [31:0]  in_data_12,
    input  logic [31:0]  in_data_13,
    input  logic [31:0]  in_data_14,
    input  logic [31:0]  in_data_15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_data_0,
    output logic [31:0]  out_data_1,
    output logic [31:0]  out_data_2,
    output logic [31:0]  out_data_3,
    output logic [31:0]  out_data_4,
    output logic [31:0]  out_data_5,
    output logic [31:0]  out_data_6,
    output logic [31:0]  out_data_7,
    output logic [31:0]  out_data_8,
    output logic [31:0]  out_data_9,
    output logic [31:0]  out_data_10,
    output logic [31:0]  out_data_11,
    output logic [31:0]  out_data_12,
    output logic [31:0]  out_data_13,
    output logic [31:0]  out_data_14,
    output logic [31:0]  out_data_15
);
    logic busy;
    logic [31:0] lanes [0:15];
    logic [31:0] swap_value;
    integer level, stage, lane, partner;
    assign in_ready = !busy || out_ready;
    assign out_valid = busy;
    always_comb begin
        lanes[0] = in_data_0;
        lanes[1] = in_data_1;
        lanes[2] = in_data_2;
        lanes[3] = in_data_3;
        lanes[4] = in_data_4;
        lanes[5] = in_data_5;
        lanes[6] = in_data_6;
        lanes[7] = in_data_7;
        lanes[8] = in_data_8;
        lanes[9] = in_data_9;
        lanes[10] = in_data_10;
        lanes[11] = in_data_11;
        lanes[12] = in_data_12;
        lanes[13] = in_data_13;
        lanes[14] = in_data_14;
        lanes[15] = in_data_15;
        swap_value = 0;
        partner = 0;
        for (level = 1; level <= 4; level = level + 1) begin
            for (stage = level - 1; stage >= 0; stage = stage - 1) begin
                for (lane = 0; lane < 16; lane = lane + 1) begin
                    partner = lane ^ (1 << stage);
                    if (partner > lane) begin
                        if (((lane & (1 << level)) == 0 && lanes[lane] > lanes[partner]) ||
                            ((lane & (1 << level)) != 0 && lanes[lane] < lanes[partner])) begin
                            swap_value = lanes[lane];
                            lanes[lane] = lanes[partner];
                            lanes[partner] = swap_value;
                        end
                    end
                end
            end
        end
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            out_data_0 <= 0;
            out_data_1 <= 0;
            out_data_2 <= 0;
            out_data_3 <= 0;
            out_data_4 <= 0;
            out_data_5 <= 0;
            out_data_6 <= 0;
            out_data_7 <= 0;
            out_data_8 <= 0;
            out_data_9 <= 0;
            out_data_10 <= 0;
            out_data_11 <= 0;
            out_data_12 <= 0;
            out_data_13 <= 0;
            out_data_14 <= 0;
            out_data_15 <= 0;
        end else if (in_valid && in_ready) begin
            busy <= 1;
            out_data_0 <= lanes[0];
            out_data_1 <= lanes[1];
            out_data_2 <= lanes[2];
            out_data_3 <= lanes[3];
            out_data_4 <= lanes[4];
            out_data_5 <= lanes[5];
            out_data_6 <= lanes[6];
            out_data_7 <= lanes[7];
            out_data_8 <= lanes[8];
            out_data_9 <= lanes[9];
            out_data_10 <= lanes[10];
            out_data_11 <= lanes[11];
            out_data_12 <= lanes[12];
            out_data_13 <= lanes[13];
            out_data_14 <= lanes[14];
            out_data_15 <= lanes[15];
        end else if (out_ready) busy <= 0;
    end
endmodule
