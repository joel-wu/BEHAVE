module serial2parallel (
    input logic clk,
    input logic rst_n,
    input logic din_serial,
    input logic din_valid,
    output logic [7:0] dout_parallel,
    output logic dout_valid
);
    logic [7:0] shift_reg, completed_word;
    logic [2:0] bit_count;
    logic completed;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            shift_reg <= 0;
            completed_word <= 0;
            bit_count <= 0;
            completed <= 0;
            dout_parallel <= 0;
            dout_valid <= 0;
        end else begin
            // Retain the original output stage, without dropping the next bit.
            dout_valid <= completed;
            if (completed) dout_parallel <= completed_word;
            completed <= 0;
            if (din_valid) begin
                shift_reg <= {shift_reg[6:0], din_serial};
                bit_count <= bit_count + 1'b1;
                if (bit_count == 7) begin
                    completed_word <= {shift_reg[6:0], din_serial};
                    completed <= 1;
                end
            end
        end
    end
endmodule
