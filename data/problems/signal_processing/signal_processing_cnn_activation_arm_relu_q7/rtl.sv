module signal_processing_cnn_activation_arm_relu_q7 (
    input  logic       clk,
    input  logic       rst,

    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_data,

    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_data
);

logic busy;

wire input_accepted;
wire output_consumed;
logic [7:0] relu_result;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = (~rst) & (~busy | output_consumed);
assign out_valid = (~rst) & busy;

always_comb begin
    if (in_data[7]) begin
        relu_result = 8'h00;
    end else begin
        relu_result = in_data;
    end
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy     <= 1'b0;
        out_data <= 8'h00;
    end else if (input_accepted) begin
        busy     <= 1'b1;
        out_data <= relu_result;
    end else if (output_consumed) begin
        busy     <= 1'b0;
        out_data <= out_data;
    end
end

endmodule