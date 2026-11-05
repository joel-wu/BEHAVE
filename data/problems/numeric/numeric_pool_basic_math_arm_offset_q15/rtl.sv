module q15_vector_offset (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_sample,
    input  logic [15:0]  in_offset,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_sample
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    logic signed [16:0] sample_s;
    logic signed [16:0] offset_s;
    logic signed [16:0] sum17;

    function automatic logic [15:0] sat_q15(input logic signed [16:0] value);
        begin
            if (value < -17'sd32768) begin
                sat_q15 = 16'h8000;
            end else if (value > 17'sd32767) begin
                sat_q15 = 16'h7FFF;
            end else begin
                sat_q15 = value[15:0];
            end
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        sample_s = $signed({in_sample[15], in_sample});
        offset_s = $signed({in_offset[15], in_offset});
        sum17    = sample_s + offset_s;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy       <= 1'b0;
            out_sample <= 16'h0000;
        end else if (input_accepted) begin
            busy       <= 1'b1;
            out_sample <= sat_q15(sum17);
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule