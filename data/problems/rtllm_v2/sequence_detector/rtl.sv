module sequence_detector (
    input logic clk,
    input logic rst_n,
    input logic data_in,
    output logic sequence_detected
);
    logic [3:0] history;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) history <= 4'b0000;
        else history <= {history[2:0], data_in};
    end

    assign sequence_detected = history == 4'b1001;
endmodule
