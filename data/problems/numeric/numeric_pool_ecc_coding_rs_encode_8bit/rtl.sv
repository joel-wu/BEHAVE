module rs_m1_single_loss_recover64 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [3:0]   in_original_count,
    input  logic [7:0]   in_present_mask,
    input  logic [511:0] in_recovery_data,
    input  logic [511:0] in_original_data0,
    input  logic [511:0] in_original_data1,
    input  logic [511:0] in_original_data2,
    input  logic [511:0] in_original_data3,
    input  logic [511:0] in_original_data4,
    input  logic [511:0] in_original_data5,
    input  logic [511:0] in_original_data6,
    input  logic [511:0] in_original_data7,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [2:0]   out_missing_index,
    output logic [511:0] out_recovered_data,
    output logic [2:0]   out_status
);

    logic busy;

    logic [2:0]   calc_missing_index;
    logic [511:0] calc_recovered_data;
    logic [2:0]   calc_status;

    logic [3:0] missing_count;
    logic [511:0] recovered_accum;

    wire input_accepted;
    wire output_consumed;

    assign output_consumed = out_valid & out_ready;
    assign input_accepted  = in_valid & in_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    logic [511:0] originals [0:7];
    assign originals[0] = in_original_data0;
    assign originals[1] = in_original_data1;
    assign originals[2] = in_original_data2;
    assign originals[3] = in_original_data3;
    assign originals[4] = in_original_data4;
    assign originals[5] = in_original_data5;
    assign originals[6] = in_original_data6;
    assign originals[7] = in_original_data7;

    always_comb begin
        missing_count = 0;
        calc_missing_index = 0;
        calc_recovered_data = 0;
        recovered_accum = in_recovery_data;
        calc_status = 0;
        if (in_original_count == 0 || in_original_count > 8) begin
            calc_status = 1;
        end else begin
            for (int i = 0; i < 8; i++) begin
                if (i < in_original_count) begin
                    if (in_present_mask[i]) recovered_accum ^= originals[i];
                    else begin
                        missing_count += 1;
                        calc_missing_index = 3'(i);
                    end
                end
            end
            if (missing_count == 0) calc_status = 2;
            else if (missing_count > 1) calc_status = 3;
            else calc_recovered_data = recovered_accum;
            if (calc_status != 0) calc_missing_index = 0;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy               <= 1'b0;
            out_missing_index  <= 3'd0;
            out_recovered_data <= 512'd0;
            out_status         <= 3'd0;
        end else if (input_accepted) begin
            busy               <= 1'b1;
            out_missing_index  <= calc_missing_index;
            out_recovered_data <= calc_recovered_data;
            out_status         <= calc_status;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
