module pulse_detect (
    input logic clk,
    input logic rst_n,
    input logic data_in,
    output logic data_out
);
    logic [1:0] history;
    logic [1:0] seen;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            history <= 2'b00;
            seen <= 2'd0;
        end else begin
            history <= {history[0], data_in};
            if (seen < 2) seen <= seen + 1'b1;
        end
    end

    // Mealy output: prior sampled history plus the current input.
    assign data_out = rst_n && seen == 2 && history == 2'b01 && !data_in;
endmodule
