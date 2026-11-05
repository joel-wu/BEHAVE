module signal_processing_cnn_activation_arm_relu6_s8 (
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
logic signed [7:0] x_s;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    x_s = $signed(in_data);
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy     <= 1'b0;
        out_data <= 8'h00;
    end else if (input_accepted) begin
        if (x_s < 8'sd0) begin
            out_data <= 8'h00;
        end else if (x_s > 8'sd6) begin
            out_data <= 8'h06;
        end else begin
            out_data <= in_data;
        end
        busy <= 1'b1;
    end else if (output_consumed) begin
        busy     <= 1'b0;
        out_data <= 8'h00;
    end
end

endmodule